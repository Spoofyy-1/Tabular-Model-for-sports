"""Cloud-only NBA shot locations and offensive-player/defender matchups.

Source archives use official NBA IDs, not ESPN IDs. Game crosswalks are
explicit; unmatched identities stay unmatched. Current-game tracking is an
outcome, not a pregame feature. No model is trained here.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

REPO = "shufinskiy/nba_data"
BASE_RELEASE = "https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/download/snapshot-36055555015-1"
ALIASES = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX", "NJ": "BKN", "NJN": "BKN"}


def ids(s, width=None):
    value = s.astype("string").str.replace(r"\.0$", "", regex=True)
    return value.str.zfill(width) if width else value


def teams(s):
    return s.astype("string").str.upper().replace(ALIASES)


def column(frame, *aliases):
    for name in aliases:
        if name in frame.columns:
            return frame[name]
    raise ValueError("Missing source aliases " + repr(aliases) + "; available schema=" + repr(list(frame.columns)))


def fetch(url, path, manifest, limit=100_000_000):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=(20, 120)) as response:
        response.raise_for_status()
        with path.open("wb") as stream:
            for block in response.iter_content(1024 * 1024):
                count += len(block)
                if count > limit:
                    raise RuntimeError("Source exceeded the bounded download size")
                stream.write(block)
                digest.update(block)
    manifest.append({"url": url, "bytes": count, "sha256": digest.hexdigest(), "retrieved_at_utc": datetime.now(timezone.utc).isoformat()})
    return path


def read_archive(path):
    # Never extract arbitrary paths from a third-party tar archive.
    with tarfile.open(path, "r:xz") as archive:
        members = [m for m in archive.getmembers() if m.isfile() and m.name.endswith(".csv")]
        if len(members) != 1 or members[0].size > 1_000_000_000:
            raise ValueError("Unexpected source archive members or uncompressed size")
        with archive.extractfile(members[0]) as stream:
            return pd.read_csv(stream, dtype="string")


def write(frame, path):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False, compression="zstd")


def partition(frame):
    cut = pd.Timestamp("2025-01-01", tz="UTC")
    source_date = pd.to_datetime(frame.source_game_date, errors="coerce", utc=True)
    result = pd.Series("date_unresolved", index=frame.index, dtype="string")
    result.loc[source_date < pd.Timestamp("2024-12-31", tz="UTC")] = "development"
    result.loc[source_date >= cut] = "holdout"
    result.loc[source_date == pd.Timestamp("2024-12-31", tz="UTC")] = "boundary_unresolved"
    precise = pd.to_datetime(frame.game_date, errors="coerce", utc=True)
    result.loc[precise.notna() & (precise < cut)] = "development"
    result.loc[precise.notna() & (precise >= cut)] = "holdout"
    return result


def quality(frame, keys):
    nonnull = frame.dropna(subset=keys)
    return {
        "duplicate_nonnull_keys": int(nonnull.duplicated(keys).sum()),
        "null_identifiers": {key: int(frame[key].isna().sum()) for key in keys},
        "source_date_min": frame.source_game_date.dropna().min() if frame.source_game_date.notna().any() else None,
        "source_date_max": frame.source_game_date.dropna().max() if frame.source_game_date.notna().any() else None,
        "schema": {name: str(dtype) for name, dtype in frame.dtypes.items()},
    }


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1996)
    parser.add_argument("--end", type=int, default=2025)
    args = parser.parse_args()
    out = ROOT / "data/context/matchups"
    cache = Path(os.environ["RUNNER_TEMP"]) / "nba-matchup-cache"
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    manifest = []
    meta = requests.get("https://api.github.com/repos/" + REPO + "/commits/main", timeout=30)
    meta.raise_for_status()
    commit = meta.json()["sha"]
    raw = "https://raw.githubusercontent.com/" + REPO + "/" + commit + "/"
    index_path = fetch(raw + "list_data.txt", cache / "index.txt", manifest, 1_000_000)
    fetch(raw + "LICENSE", out / "SOURCE_LICENSE.txt", manifest, 1_000_000)
    index = {}
    for line in index_path.read_text().splitlines():
        name, sep, url = line.partition("=")
        if sep:
            index[name] = url
    base = fetch(BASE_RELEASE + "/dataset-nba.tar.gz", cache / "base-nba.tar.gz", manifest)
    with tarfile.open(base, "r:gz") as archive:
        with archive.extractfile("data/nba/games.parquet") as stream:
            games = pd.read_parquet(stream)
    base.unlink()
    games["join_date"] = pd.to_datetime(games.game_local_date, errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
    games["join_home"] = teams(games.home_team)
    games["join_away"] = teams(games.away_team)
    keys = ["join_date", "join_home", "join_away"]
    ambiguous = games.duplicated(keys, keep=False)
    lookup = games.loc[~ambiguous, keys + ["game_id", "game_date"]].rename(columns={"game_id": "espn_game_id"})
    shot_counts, matchup_counts, maps = [], [], []
    raw_numeric = ["PERIOD", "MINUTES_REMAINING", "SECONDS_REMAINING", "SHOT_DISTANCE", "LOC_X", "LOC_Y", "SHOT_ATTEMPTED_FLAG", "SHOT_MADE_FLAG"]
    for season in range(args.start, args.end + 1):
        for phase, marker in [("REG", ""), ("POST", "po_")]:
            name = "shotdetail_" + marker + str(season)
            if name not in index:
                continue
            url = raw + "datasets/" + name + ".tar.xz"
            path = fetch(url, cache / (name + ".tar.xz"), manifest)
            shots = read_archive(path)
            path.unlink()
            required = {"GAME_ID", "GAME_DATE", "HTM", "VTM", "PLAYER_ID", "GAME_EVENT_ID"}
            if not required.issubset(shots.columns):
                raise ValueError("Shot schema missing " + str(required - set(shots.columns)))
            shots["nba_game_id"] = ids(shots.GAME_ID, 10)
            shots["nba_player_id"] = ids(shots.PLAYER_ID)
            shots["event_order"] = pd.to_numeric(shots.GAME_EVENT_ID, errors="raise").astype("Int64")
            shots["source_game_date"] = pd.to_datetime(shots.GAME_DATE, format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
            if shots.source_game_date.isna().any():
                raise ValueError("Unparseable source game dates")
            shots["season_start_year"] = season
            shots["season_end_year"] = season + 1
            shots["season_type"] = phase
            for col in raw_numeric:
                if col in shots:
                    shots[col] = pd.to_numeric(shots[col], errors="coerce")
            game_map = shots[["nba_game_id", "source_game_date", "HTM", "VTM"]].drop_duplicates()
            if game_map.nba_game_id.duplicated().any():
                raise ValueError("Conflicting date/team identity for an NBA game")
            game_map["join_date"] = game_map.source_game_date
            game_map["join_home"] = teams(game_map.HTM)
            game_map["join_away"] = teams(game_map.VTM)
            game_map = game_map.merge(lookup, on=keys, how="left", validate="many_to_one")
            game_map["id_mapping_method"] = "exact_source_date_and_home_away_teams"
            game_map["game_mapping_verified"] = game_map.espn_game_id.notna()
            maps.append(game_map)
            shots = shots.merge(game_map[["nba_game_id", "espn_game_id", "game_date"]], on="nba_game_id", how="left", validate="many_to_one")
            shots["experiment_partition"] = partition(shots)
            shots["verified_asof"] = False
            shots = shots.sort_values(["nba_game_id", "nba_player_id", "event_order"])
            write(shots, out / "shots" / (name + ".parquet"))
            shot_counts.append({"season_start": season, "season_type": phase, "rows": len(shots), "games": int(shots.nba_game_id.nunique()), "mapped_rows": int(shots.espn_game_id.notna().sum()), "partitions": {str(k): int(v) for k, v in shots.experiment_partition.value_counts().items()}})
            shot_counts[-1].update(quality(shots, ["nba_game_id", "GAME_EVENT_ID", "nba_player_id"]))
            # Matchup IDs share the NBA namespace with these shot records.
            mname = "matchups_" + marker + str(season)
            if mname in index:
                path = fetch(raw + "datasets/" + mname + ".tar.xz", cache / (mname + ".tar.xz"), manifest)
                match = read_archive(path)
                path.unlink()
                match["nba_game_id"] = ids(column(match, "gameId", "game_id", "GAME_ID"), 10)
                match["nba_offensive_player_id"] = ids(column(match, "personIdOff", "person_id_off", "person_id"))
                match["nba_defensive_player_id"] = ids(column(match, "personIdDef", "person_id_def", "matchups_person_id"))
                for col in ["partialPossessions", "playerPoints", "teamPoints", "matchupAssists", "matchupPotentialAssists", "matchupTurnovers", "matchupBlocks", "matchupFieldGoalsMade", "matchupFieldGoalsAttempted", "matchupThreePointersMade", "matchupThreePointersAttempted", "switchesOn"]:
                    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", col).lower()
                    for present in [col, snake]:
                        if present in match:
                            match[present] = pd.to_numeric(match[present], errors="coerce")
                match["season_start_year"] = season
                match["season_end_year"] = season + 1
                match["season_type"] = phase
                match = match.merge(game_map[["nba_game_id", "source_game_date", "espn_game_id", "game_date"]], on="nba_game_id", how="left", validate="many_to_one")
                match["experiment_partition"] = partition(match)
                match["verified_asof"] = False
                match["pregame_assignment_known"] = False
                match = match.sort_values(["nba_game_id", "nba_offensive_player_id", "nba_defensive_player_id"])
                write(match, out / "matchups" / (mname + ".parquet"))
                matchup_counts.append({"season_start": season, "season_type": phase, "rows": len(match), "games": int(match.nba_game_id.nunique()), "dated_rows": int(match.source_game_date.notna().sum()), "mapped_rows": int(match.espn_game_id.notna().sum()), "partitions": {str(k): int(v) for k, v in match.experiment_partition.value_counts().items()}})
                matchup_counts[-1].update(quality(match, ["nba_game_id", "nba_offensive_player_id", "nba_defensive_player_id"]))
            print(json.dumps({"season": season, "phase": phase, "shots": len(shots), "matchups": matchup_counts[-1]["rows"] if mname in index else 0}), flush=True)
    crosswalk = pd.concat(maps, ignore_index=True).drop_duplicates()
    write(crosswalk, out / "game_id_crosswalk.parquet")
    summary = {"source": REPO, "source_commit": commit, "shot_rows": sum(x["rows"] for x in shot_counts), "matchup_rows": sum(x["rows"] for x in matchup_counts), "games_in_crosswalk": int(crosswalk.nba_game_id.nunique()), "games_mapped_to_espn": int(crosswalk.loc[crosswalk.game_mapping_verified, "nba_game_id"].nunique()), "shot_coverage": shot_counts, "matchup_coverage": matchup_counts,
        "source_bytes_downloaded": sum(x["bytes"] for x in manifest), "verified_asof": False, "pregame_assignment_known": False,
        "limitations": ["All shot and matchup statistics describe completed games and must be lagged.", "Defender assignments during the target game are not known pregame.", "Official NBA player IDs are retained; player-level ESPN crosswalk is not fabricated.", "Source dates have date precision until an exact ESPN game match supplies UTC start.", "Historical corrections and availability timestamps are not reconstructed.", "No new data were used to retrain or select the baseline models."]}
    (out / "context_summary.json").write_text(json.dumps(summary, indent=2))
    (out / "source_manifest.json").write_text(json.dumps({"repository": REPO, "commit": commit, "license": "Apache-2.0 repository; retain upstream NBA attribution and source terms", "downloads": manifest}, indent=2))
    (out / "DATA_DICTIONARY.md").write_text("# NBA shots and defender matchups\n\nOfficial NBA IDs are stored as strings, separately from ESPN IDs. Files are partitioned by season START year and regular/postseason. Source fields are preserved; numeric tracking/count fields are converted where documented.\n\n`shots/`: one source shot-event record; locations, shot type, distance, shooter, period, result. `matchups/`: source game/offensive-player/defender tracking rows, including partial possessions and scoring where supplied. These are actual postgame outcomes.\n\n`game_id_crosswalk.parquet`: exact calendar-date and home/away-team mappings to the original ESPN game table; unmatched games stay unmatched. Player IDs have NOT been remapped to ESPN. `experiment_partition` uses verified game UTC start when mapped; otherwise December 31, 2024 remains boundary_unresolved. Missing dates remain date_unresolved.\n\n`verified_asof=false` and `pregame_assignment_known=false`: no target-game matchup or shot outcome can be a pregame feature. A future model may use strictly earlier appearances after a source-timing and player-ID audit.\n")
    print(json.dumps({k: summary[k] for k in ["shot_rows", "matchup_rows", "games_in_crosswalk", "games_mapped_to_espn"]}), flush=True)


if __name__ == "__main__":
    main()
