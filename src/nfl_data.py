#!/usr/bin/env python3
"""Download and normalize nflverse player-game data on GitHub-hosted Actions.

Python 3.9+. Requires pandas, pyarrow, requests. Run from any directory with
``python src/nfl_data.py --start-season 1999 --end-season 2025``. Execution is
refused outside a GitHub-hosted Actions runner. Raw data goes to
SPORTS_PROPS_CACHE or RUNNER_TEMP/nfl; normalized data goes to repo data/nfl.
The player-game table contains outcomes, NOT a leakage-safe feature matrix.
For the current experiment, development dates are before 2025-01-01 UTC,
2024 is calibration, and held-out evaluation is 2025-01-01 UTC onward.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://github.com/nflverse/nflverse-data/releases/download/"
TEAM_ALIASES = {"LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV", "JAC": "JAX", "WSH": "WAS"}
TARGETS = ["passing_yards", "passing_tds", "passing_interceptions", "attempts", "completions", "rushing_yards", "carries", "rushing_tds", "receiving_yards", "receptions", "targets", "receiving_tds"]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def require_hosted_runner():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise SystemExit("Data ingestion is restricted to GitHub-hosted Actions. No local datasets may be downloaded or written.")


def clean_team(values):
    return values.astype("string").replace(TEAM_ALIASES)


def download(job, cache):
    require_hosted_runner()
    category, year, url, name = job
    path = cache / name
    sidecar = path.with_suffix(path.suffix + ".download.json")
    if path.exists() and sidecar.exists():
        meta = json.loads(sidecar.read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest() == meta["sha256"]:
            return job, path, meta
    response = None
    for attempt in range(4):
        try:
            response = requests.get(url, timeout=(15, 90))
            if response.status_code == 404:
                return job, None, {"category": category, "season": year, "source_url": url, "status": "not_available", "checked_at_utc": utc_now()}
            response.raise_for_status()
            if len(response.content) > 30 * 1024 * 1024:
                raise RuntimeError("Unexpected large response; refusing to exceed raw-data budget")
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(response.content)
            tmp.replace(path)
            meta = {"category": category, "season": year, "source_url": url, "downloaded_at_utc": utc_now(), "http_last_modified": response.headers.get("Last-Modified"), "bytes": path.stat().st_size, "sha256": hashlib.sha256(response.content).hexdigest(), "status": "downloaded"}
            sidecar.write_text(json.dumps(meta, indent=2))
            return job, path, meta
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1 + attempt * 2)
    raise RuntimeError("Download failed: " + url)


def make_schedule(path, start, end):
    frame = pd.read_csv(path, low_memory=False)
    frame = frame.loc[frame.season.between(start, end)].copy()
    frame["game_date_time_known"] = frame.gametime.notna()
    local = pd.to_datetime(frame.gameday + " " + frame.gametime.fillna("00:00"), errors="raise")
    frame["game_date"] = local.dt.tz_localize("America/New_York", ambiguous="raise", nonexistent="raise").dt.tz_convert("UTC")
    frame["is_neutral_site"] = frame.location.eq("Neutral")
    for c in ["home_team", "away_team"]:
        frame[c + "_source"] = frame[c]
        frame[c] = clean_team(frame[c])
    frame["sport"] = "NFL"
    frame["season_type"] = frame.game_type.where(frame.game_type.eq("REG"), "POST")
    assert not frame.game_id.duplicated().any(), "Duplicate schedule game IDs"
    return frame


def team_games(schedule):
    parts = []
    for side, other in [("home", "away"), ("away", "home")]:
        cols = ["game_id", "season", "season_type", "week", "game_date", "game_date_time_known", "is_neutral_site", "gameday", "gametime", "stadium", "roof", "surface", side + "_team", other + "_team"]
        d = schedule[cols].rename(columns={side + "_team": "team", other + "_team": "opponent"}).copy()
        d["is_home"] = side == "home"
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def write_table(frame, path):
    require_hosted_runner()
    # Annual roster sources have evolving object schemas (e.g. jersey 27
    # becomes string '27'). Preserve metadata as strings rather than letting
    # Arrow infer an incompatible schema from one year's values.
    frame = frame.copy()
    for column in frame.select_dtypes(include=["object"]).columns:
        value_types = frame[column].dropna().map(type).unique()
        if len(value_types) > 1:
            frame[column] = frame[column].astype("string")
    frame.to_parquet(path, index=False, compression="zstd")
    return {"file": str(path.relative_to(ROOT)), "rows": len(frame), "columns": len(frame.columns), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-season", type=int, default=1999)
    parser.add_argument("--end-season", type=int, default=2025)
    parser.add_argument("--cache-dir", type=Path, default=None, help="Defaults to SPORTS_PROPS_CACHE or RUNNER_TEMP/nfl on the hosted runner")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "nfl")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    require_hosted_runner()
    default_cache = os.environ.get("SPORTS_PROPS_CACHE")
    if not default_cache:
        runner_temp = os.environ.get("RUNNER_TEMP")
        if not runner_temp:
            raise SystemExit("RUNNER_TEMP or SPORTS_PROPS_CACHE must be provided by the hosted environment.")
        default_cache = str(Path(runner_temp) / "nfl")
    cache, out = args.cache_dir or Path(default_cache), args.output_dir
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for year in range(args.start_season, args.end_season + 1):
        jobs.append(("player_stats", year, BASE + "stats_player/stats_player_week_%d.parquet" % year, "stats_%d.parquet" % year))
        jobs.append(("rosters", year, BASE + "rosters/roster_%d.parquet" % year, "roster_%d.parquet" % year))
        if year >= 2012:
            jobs.append(("snap_counts", year, BASE + "snap_counts/snap_counts_%d.parquet" % year, "snaps_%d.parquet" % year))
        if 2009 <= year <= 2024:
            jobs.append(("injuries", year, BASE + "injuries/injuries_%d.parquet" % year, "injuries_%d.parquet" % year))
    jobs.extend([("players", None, BASE + "players/players.parquet", "players.parquet"), ("schedules", None, "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv", "games.csv")])
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download, job, cache) for job in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if i % 20 == 0 or i == len(futures):
                print("Downloaded/verified %d/%d source files" % (i, len(futures)), flush=True)
    results.sort(key=lambda x: (x[0][0], x[0][1] or 0))
    paths = {}
    for job, path, meta in results:
        if path is not None:
            paths.setdefault(job[0], []).append(path)
    missing_stats = [m for j, p, m in results if j[0] == "player_stats" and p is None]
    if missing_stats:
        raise RuntimeError("Required player-stat seasons unavailable: " + str(missing_stats))
    source_rows = {}
    tables = {}
    for category in ["player_stats", "rosters", "snap_counts", "injuries", "players"]:
        tables[category] = pd.concat([pd.read_parquet(p) for p in paths.get(category, [])], ignore_index=True)
        source_rows[category] = len(tables[category])
    schedule = make_schedule(paths["schedules"][0], args.start_season, args.end_season)
    team_schedule = team_games(schedule)
    stats = tables["player_stats"]
    invalid = ~stats.player_id.astype("string").str.match(r"^00-\d{7}$", na=False)
    rejected = stats.loc[invalid].copy()
    rejected["exclusion_reason"] = "Missing or non-GSIS player identifier (includes source pseudo-player 0)"
    stats = stats.loc[~invalid].copy()
    exact_dupes = int(stats.duplicated().sum())
    stats = stats.drop_duplicates()
    if stats.duplicated(["game_id", "player_id"]).any():
        bad = stats.loc[stats.duplicated(["game_id", "player_id"], keep=False)]
        bad.to_parquet(out / "duplicate_player_games_for_review.parquet", index=False)
        raise ValueError("Conflicting duplicate game/player rows; saved for review")
    stats["team_source"] = stats.team
    stats["team"] = clean_team(stats.team)
    # One archived 1999 Steve Bono row has neither team nor opponent.
    # Only use an unambiguous same-season roster identity to repair it.
    missing_teams_before = int(stats.team.isna().sum())
    roster_identity = tables["rosters"][["season", "gsis_id", "team"]].dropna().copy()
    roster_identity["team"] = clean_team(roster_identity.team)
    roster_identity = roster_identity.drop_duplicates()
    roster_identity = roster_identity.loc[~roster_identity.duplicated(["season", "gsis_id"], keep=False)]
    roster_map = roster_identity.set_index(["season", "gsis_id"]).team.to_dict()
    stats["team_repaired_from_annual_roster"] = stats.team.isna()
    for idx in stats.index[stats.team.isna()]:
        stats.at[idx, "team"] = roster_map.get((stats.at[idx, "season"], stats.at[idx, "player_id"]), pd.NA)
    stats["opponent"] = clean_team(stats.opponent_team)
    stats["player_short_name"] = stats.player_name
    stats["player_name"] = stats.player_display_name.fillna(stats.player_name)
    stats["sport"] = "NFL"
    date_cols = ["game_id", "team", "game_date", "game_date_time_known", "is_home", "is_neutral_site", "gameday"]
    stats = stats.merge(team_schedule[date_cols], on=["game_id", "team"], how="left", validate="many_to_one")
    opponent_map = team_schedule.set_index(["game_id", "team"]).opponent.to_dict()
    for idx in stats.index[stats.opponent.isna()]:
        stats.at[idx, "opponent"] = opponent_map.get((stats.at[idx, "game_id"], stats.at[idx, "team"]), pd.NA)
    if stats.game_date.isna().any():
        raise ValueError("Some player games did not match schedule: " + str(stats.loc[stats.game_date.isna(), ["game_id", "team"]].drop_duplicates().head(20).to_dict("records")))
    stats["source_quality_era"] = stats.season.map(lambda y: "legacy_1999_2000_caution" if y < 2001 else "standard")
    front = ["sport", "game_id", "player_id", "player_name", "team", "opponent", "game_date", "season", "week", "season_type", "position", "is_home", "is_neutral_site", "game_date_time_known", "source_quality_era"]
    stats = stats[front + [c for c in stats.columns if c not in front]].sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    outputs = {}
    outputs["player_games"] = write_table(stats, out / "player_games.parquet")
    outputs["schedules"] = write_table(schedule, out / "schedules.parquet")
    outputs["team_games"] = write_table(team_schedule, out / "team_games.parquet")
    outputs["excluded_source_rows"] = write_table(rejected, out / "excluded_source_rows.parquet")
    for category in ["rosters", "players"]:
        df = tables[category].rename(columns={"gsis_id": "player_id"})
        if "team" in df:
            df["team_source"] = df.team
            df["team"] = clean_team(df.team)
        outputs[category] = write_table(df, out / (category + ".parquet"))
    snaps = tables["snap_counts"].rename(columns={"game_type": "season_type", "player": "player_name"})
    snaps["season_type_source"] = snaps.season_type
    snaps["season_type"] = snaps.season_type.where(snaps.season_type.eq("REG"), "POST")
    snaps["team"] = clean_team(snaps.team)
    snaps["opponent"] = clean_team(snaps.opponent)
    players = tables["players"]
    idmap = players.loc[players.pfr_id.notna() & players.gsis_id.notna(), ["pfr_id", "gsis_id"]].drop_duplicates()
    ambiguous_ids = idmap.loc[idmap.pfr_id.duplicated(keep=False), "pfr_id"].unique()
    idmap = idmap.loc[~idmap.pfr_id.isin(ambiguous_ids)].rename(columns={"pfr_id": "pfr_player_id", "gsis_id": "player_id"})
    snaps = snaps.merge(idmap, on="pfr_player_id", how="left", validate="many_to_one")
    snaps = snaps.merge(team_schedule[date_cols], on=["game_id", "team"], how="left", validate="many_to_one")
    outputs["snap_counts"] = write_table(snaps, out / "snap_counts.parquet")
    injuries = tables["injuries"].rename(columns={"gsis_id": "player_id", "full_name": "player_name", "game_type": "season_type"})
    injuries["season_type"] = injuries.season_type.where(injuries.season_type.eq("REG"), "POST")
    injuries["team"] = clean_team(injuries.team)
    injuries["date_modified"] = pd.to_datetime(injuries.date_modified, utc=True, errors="coerce")
    injury_schedule_cols = ["season", "season_type", "week", "team", "game_id", "game_date", "game_date_time_known", "is_home", "opponent"]
    injuries = injuries.merge(team_schedule[injury_schedule_cols], on=["season", "season_type", "week", "team"], how="left", validate="many_to_one")
    injuries["modified_before_kickoff"] = injuries.date_modified.lt(injuries.game_date) & injuries.game_date_time_known.fillna(False)
    outputs["injuries"] = write_table(injuries, out / "injuries.parquet")
    validation = {
        "source_player_stat_rows": source_rows["player_stats"], "clean_player_game_rows": len(stats),
        "excluded_invalid_player_id_rows": len(rejected), "exact_duplicate_rows_removed": exact_dupes,
        "missing_team_rows_repaired_from_unambiguous_roster": missing_teams_before,
        "unique_player_game_key": not bool(stats.duplicated(["game_id", "player_id"]).any()),
        "null_game_dates": int(stats.game_date.isna().sum()), "game_date_dtype": str(stats.game_date.dtype),
        "date_only_player_game_rows": int((~stats.game_date_time_known).sum()),
        "distinct_games": int(stats.game_id.nunique()), "distinct_players": int(stats.player_id.nunique()),
        "min_game_date": stats.game_date.min().isoformat(), "max_game_date": stats.game_date.max().isoformat(),
        "rows_by_season": {str(k): int(v) for k, v in stats.groupby("season").size().items()},
        "rows_by_position": {str(k): int(v) for k, v in stats.groupby("position", dropna=False).size().items()},
        "rows_by_season_type": {str(k): int(v) for k, v in stats.groupby("season_type").size().items()},
        "target_null_counts": {c: int(stats[c].isna().sum()) for c in TARGETS},
        "snap_rows_without_gsis_mapping": int(snaps.player_id.isna().sum()),
        "ambiguous_pfr_ids_not_mapped": [str(x) for x in ambiguous_ids],
        "injury_rows_without_schedule_match": int(injuries.game_id.isna().sum()),
        "injury_rows_modified_before_kickoff": int(injuries.modified_before_kickoff.sum()),
        "injury_rows_with_timestamp": int(injuries.date_modified.notna().sum()),
        "cache_bytes": sum(p.stat().st_size for p in cache.glob("*")),
        "output_bytes": sum(p.stat().st_size for p in out.glob("*")),
    }
    (out / "validation.json").write_text(json.dumps(validation, indent=2))
    manifest = {
        "created_at_utc": utc_now(), "requested_seasons": [args.start_season, args.end_season],
        "execution_environment": "GitHub-hosted Actions only; datasets must not be downloaded to local workstations",
        "experiment_split": {"development_end_exclusive": "2025-01-01T00:00:00Z", "calibration_start": "2024-01-01T00:00:00Z", "test_start": "2025-01-01T00:00:00Z"},
        "source": "nflverse community data, derived from NFL and other source providers",
        "attribution": "nflverse contributors; player statistics calculated by nflfastR; schedules maintained by Lee Sharpe; snap counts originally from Pro Football Reference.",
        "licenses": [
            {"repository": "nflverse/nflverse-data", "license": "CC-BY-4.0", "url": "https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md"},
            {"repository": "nflverse/nfldata", "license": "No repository license asserted by GitHub metadata at retrieval; upstream rights/terms apply", "url": "https://github.com/nflverse/nfldata"},
        ],
        "upstream_terms_notice": "nflverse states NFL data belong to their respective owners and are governed by their terms. Repository licensing is not a blanket assertion about third-party rights.",
        "terms_source": "https://nflverse.nflverse.com/", "downloads": [m for _, _, m in results], "outputs": outputs,
        "dictionary_urls": {k: "https://nflreadr.nflverse.com/articles/dictionary_%s.html" % v for k, v in [("player_games", "player_stats"), ("schedules", "schedules"), ("rosters", "rosters"), ("players", "players"), ("snap_counts", "snap_counts"), ("injuries", "injuries")]},
        "availability_source": "https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html",
        "transformations": ["Concatenated published season-level parquet files; no play-by-play scraped", "Quarantined invalid/non-GSIS source player identifiers", "Renamed displayed names and standardized franchise abbreviations, preserving source teams", "Joined schedule by game ID and team; kickoff Eastern timezone converted to UTC", "Missing kickoff times represented as local midnight and explicitly flagged date-only", "Kept postgame outcomes and context tables separate from model features", "Snap PFR IDs mapped only when unambiguous to GSIS", "Injury modification timestamp preserved; game join uses season/week/type/team"],
        "caveats": ["Absent player-game rows do not establish zero performance, inactivity, or DNP. No complete eligibility universe is inferred.", "1999-2000 are archived legacy data with source-consistency issues; source_quality_era flags them for sensitivity analysis.", "All numeric player-game boxscore metrics are realized outcomes; use only shifted historical values in pregame models.", "Snap counts are postgame and may only be used after lagging. Do not use target-game snaps to predict target-game outcomes.", "Roster and player master tables are current/revised snapshots, not complete as-of roster histories. Status and latest_team leak future information if used historically.", "Injury source ended after 2024. Historical injury files preserve final report snapshots, not the full sequence of available reports; date_modified is not guaranteed publication/ingestion time.", "Schedule scores, actual weather, actual starting QBs, closing odds and revised venue information are not point-in-time pregame features.", "No historical betting lines, tradable prices, settlement rules, fill or execution data are included; model accuracy alone cannot establish betting profitability."]
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    dictionary = {
        "sport": "NFL", "game_id": "Source game key: season_week_away_home. Source abbreviations remain in ID.",
        "player_id": "GSIS identifier; player-game primary key is (game_id, player_id).",
        "player_name": "Human-readable full display name; never join on names.",
        "team": "Normalized team/franchise abbreviation (LAR, LAC, LV, JAX, WAS aliases).",
        "opponent": "Normalized opposing team.", "game_date": "UTC kickoff timestamp; if exact time missing, local Eastern midnight placeholder. Consult game_date_time_known.",
        "game_date_time_known": "True if schedule has exact kickoff time. False means date-only precision.",
        "is_home": "Designated home team; neutral sites may still designate a home team.",
        "is_neutral_site": "Schedule marks neutral venue.", "season": "NFL season year, including postseason January/February of following calendar year.",
        "week": "nflverse week number; postseason weeks follow regular season.", "season_type": "REG regular season or POST postseason.",
        "position": "Player position in source game statistics; not a verified historical depth-chart snapshot.",
        "source_quality_era": "Flags 1999/2000 legacy source limitations.",
    }
    lines = ["# NFL data dictionary", "", "`player_games.parquet` is a realized-outcome table, not model-ready pregame features. No missing player games were imputed as zero.", "", "| Column | dtype | Null rows | Meaning / use |", "|---|---|---:|---|"]
    idcols = set(front + ["player_short_name", "player_display_name", "team_source", "opponent_team", "gameday", "headshot_url", "position_group"])
    for c in stats.columns:
        desc = dictionary.get(c, "Source identity/metadata; see upstream dictionary." if c in idcols else "Realized game statistic; outcome/label or lagged history only. See upstream dictionary.")
        lines.append("| %s | %s | %s | %s |" % (c, stats[c].dtype, int(stats[c].isna().sum()), desc))
    lines.extend(["", "Upstream detailed definitions: https://nflreadr.nflverse.com/articles/dictionary_player_stats.html", "", "## Context tables", "", "- `schedules.parquet`: games, exact/date-only UTC kickoff, venue, outcomes, actual weather, odds. Unsafe columns are preserved for audit, not pregame use.", "- `team_games.parquet`: two rows per game with opponent/home/venue/calendar fields. Venue fields are revised snapshots, not verified as-of features.", "- `snap_counts.parquet`: postgame offense/defense/special-teams counts and proportions, 2012 onward; PFR ID retained and GSIS mapping may be null.", "- `injuries.parquet`: 2009–2024 final report snapshots, GSIS, report/practice status, injury descriptions, date_modified UTC, matched game and modified_before_kickoff. That flag alone does not prove point-in-time data availability.", "- `rosters.parquet`: annual roster snapshots for selected seasons; status/week fields do not turn them into full historical weekly rosters.", "- `players.parquet`: player ID crosswalk and demographics; latest team/status/career endpoint are future-looking historically.", "- `excluded_source_rows.parquet`: rejected pseudo-player/missing identifier records for reproducibility.", "", "Source attribution, each download URL, retrieval timestamp, SHA256 and license information are in `manifest.json`. Validation and coverage are in `validation.json`."])
    (out / "DATA_DICTIONARY.md").write_text("\n".join(lines) + "\n")
    if validation["cache_bytes"] + validation["output_bytes"] > 500 * 1024 * 1024:
        raise RuntimeError("NFL disk budget exceeded")
    print(json.dumps(validation, indent=2), flush=True)


if __name__ == "__main__":
    main()
