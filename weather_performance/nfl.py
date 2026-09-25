"""Hosted-only retrospective NFL weather/outcome associations; never predictors."""
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

TAG = "csv-36170200182-1"
ASSET = "csv-base-nfl.tar.gz"
BYTES = 88715353
SHA256 = "eb1c3168d370d02e7497486f5ba9999e7c886f2bd7644da2280184ad116a2647"
URL = f"https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/{TAG}/{ASSET}"
DICTIONARY = "https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_schedules.csv"
METRICS = ["passing_yards", "attempts", "completions", "rushing_yards", "carries", "receiving_yards", "receptions", "targets"]
VOLUMES = {"passing_yards": "attempts", "rushing_yards": "carries", "receiving_yards": "targets", "receptions": "targets", "completions": "attempts"}
ALIASES = {"LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV", "JAC": "JAX", "WSH": "WAS", "AZ": "ARI"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strings(series):
    return series.astype("string").replace(r"^\s*$", pd.NA, regex=True)


def boolean(series):
    return series.astype("string").str.lower().map({"true": True, "false": False, "1": True, "0": False}).astype("boolean")


def panel(players, schedules):
    required = {"game_id", "player_id", "team", "opponent", "position", "season", "game_date"}
    needed = {"game_id", "home_team", "away_team", "game_date", "season", "game_date_time_known", "temp", "wind", "roof", "surface"}
    if not required.issubset(players) or not needed.issubset(schedules):
        raise ValueError("Required player/schedule weather columns missing")
    p = players[list(sorted(required)) + [c for c in METRICS if c in players]].copy()
    s = schedules[list(sorted(needed))].copy()
    for col in ["game_id", "player_id", "team", "opponent", "position"]:
        p[col] = strings(p[col])
    for col in ["game_id", "home_team", "away_team", "roof", "surface"]:
        s[col] = strings(s[col])
    for col in ["team", "opponent"]:
        p[col] = p[col].replace(ALIASES)
    for col in ["home_team", "away_team"]:
        s[col] = s[col].replace(ALIASES)
    if s.game_id.isna().any() or s.game_id.duplicated().any():
        raise ValueError("Schedule game identity missing/duplicated")
    if p.duplicated(["game_id", "player_id"]).any():
        raise ValueError("Duplicate player/game rows cannot weight descriptive summaries")
    p["player_game_date"] = pd.to_datetime(p.pop("game_date"), utc=True, errors="coerce", format="mixed")
    s["game_date"] = pd.to_datetime(s.game_date, utc=True, errors="coerce", format="mixed")
    p["player_season"] = pd.to_numeric(p.pop("season"), errors="coerce")
    s["season"] = pd.to_numeric(s.season, errors="coerce").astype("Int64")
    joined = p.merge(s, on="game_id", how="left", validate="many_to_one", indicator=True)
    ids = joined[["game_id", "player_id", "team", "opponent"]].notna().all(axis=1)
    ids &= joined.player_id.str.fullmatch(r"00-\d{7}", na=False)
    pair = ((joined.team.eq(joined.home_team) & joined.opponent.eq(joined.away_team))
            | (joined.team.eq(joined.away_team) & joined.opponent.eq(joined.home_team)))
    valid = (ids & pair & joined.team.ne(joined.opponent) & joined._merge.eq("both")
             & joined.game_date.eq(joined.player_game_date) & joined.season.eq(joined.player_season)).fillna(False)
    excluded = joined.loc[~valid, ["game_id", "player_id", "team", "opponent"]].copy()
    excluded["exclusion_reason"] = "missing_identity_or_exact_game_team_opponent_date_season_mismatch"
    out = joined.loc[valid].drop(columns=["_merge", "player_game_date", "player_season"]).copy()
    if out.empty:
        raise ValueError("No verified exact player/schedule joins")
    out["is_nominal_home"] = out.team.eq(out.home_team)
    out["game_date_time_known"] = boolean(out.game_date_time_known)
    out["evaluation_split"] = np.where(out.game_date.lt(pd.Timestamp("2025-01-01", tz="UTC")), "development_through_2024", "holdout_2025_onward")
    boundary = out.game_date.dt.strftime("%Y-%m-%d").isin(["2024-12-31", "2025-01-01"])
    out.loc[boundary & ~out.game_date_time_known.fillna(False), "evaluation_split"] = "date_precision_unresolved"
    out["roof_source"] = out.pop("roof")
    roof = out.roof_source.str.lower().str.strip()
    out["weather_exposure"] = roof.map({"outdoors": "outdoors", "open": "retractable_open", "closed": "retractable_closed", "dome": "dome", "retractable": "retractable_status_unknown"}).fillna("roof_unknown")
    out["weather_exposure_open_air"] = roof.isin(["outdoors", "open"])
    out["temperature_source"] = out.pop("temp")
    out["wind_source"] = out.pop("wind")
    temp = pd.to_numeric(out.temperature_source, errors="coerce")
    wind = pd.to_numeric(out.wind_source, errors="coerce")
    out["temperature_value_invalid"] = out.temperature_source.notna() & (~temp.between(-100, 150) | temp.isna())
    out["wind_value_invalid"] = out.wind_source.notna() & (~wind.between(0, 200) | wind.isna())
    out["temperature_fahrenheit_assumed"] = temp.where(~out.temperature_value_invalid)
    out["temperature_celsius_assuming_fahrenheit"] = (out.temperature_fahrenheit_assumed - 32) * 5 / 9
    out["temperature_unit_independently_verified"] = False
    out["wind_mph"] = wind.where(~out.wind_value_invalid)
    out["wind_kmh"] = out.wind_mph * 1.609344
    out["temperature_bucket_assuming_fahrenheit"] = pd.cut(out.temperature_fahrenheit_assumed,
        [-np.inf, 32, 50, 70, 85, np.inf], labels=["below_32F", "32_to_under_50F", "50_to_under_70F", "70_to_under_85F", "85F_or_more"], right=False).astype("string").fillna("unknown")
    out["wind_bucket"] = pd.cut(out.wind_mph, [-np.inf, 5, 10, 20, np.inf], labels=["under_5mph", "5_to_under_10mph", "10_to_under_20mph", "20mph_or_more"], right=False).astype("string").fillna("unknown")
    for col in ["temperature_bucket_assuming_fahrenheit", "wind_bucket"]:
        out.loc[~out.weather_exposure_open_air, col] = "not_applicable_to_roof_category"
    for metric in METRICS:
        out["outcome_" + metric] = pd.to_numeric(out[metric], errors="coerce") if metric in out else np.nan
        if metric in out:
            out = out.drop(columns=metric)
    out["weather_is_retrospective_observation"] = True
    out["weather_historical_availability_verified"] = False
    out["automatic_training_join_allowed"] = False
    return out.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True), excluded


def aggregates(frame):
    group_cols = ["evaluation_split", "position", "weather_exposure", "temperature_bucket_assuming_fahrenheit", "wind_bucket"]
    parts = []
    for keys, group in frame.groupby(group_cols, dropna=False, sort=True):
        for metric in METRICS:
            values = group["outcome_" + metric]
            known = values.notna()
            row = dict(zip(group_cols, keys))
            row.update(metric=metric, player_game_rows=len(group), distinct_games=group.game_id.nunique(), distinct_players=group.player_id.nunique(),
                nonmissing_outcome_rows=int(known.sum()), missing_outcome_rows=int((~known).sum()),
                outcome_sum=values.sum(min_count=1), outcome_mean=values.mean() if known.any() else np.nan, outcome_median=values.median() if known.any() else np.nan,
                opportunity_metric=VOLUMES.get(metric), paired_positive_opportunity_rows=0, paired_opportunity_sum=np.nan,
                paired_outcome_sum=np.nan, outcome_per_positive_opportunity=np.nan)
            if metric in VOLUMES:
                volume = group["outcome_" + VOLUMES[metric]]
                eligible = known & volume.gt(0)
                row["paired_positive_opportunity_rows"] = int(eligible.sum())
                if eligible.any():
                    row["paired_opportunity_sum"] = volume[eligible].sum()
                    row["paired_outcome_sum"] = values[eligible].sum()
                    row["outcome_per_positive_opportunity"] = row["paired_outcome_sum"] / row["paired_opportunity_sum"]
            parts.append(row)
    return pd.DataFrame(parts)


def fetch_and_read(cache):
    require_github_hosted_runner()
    path = cache / ASSET
    digest, size = hashlib.sha256(), 0
    with requests.Session() as session:
        session.max_redirects = 2
        with session.get(URL, stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            with path.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > BYTES:
                        raise ValueError("Pinned source byte bound exceeded")
                    digest.update(chunk)
                    handle.write(chunk)
    if size != BYTES or digest.hexdigest() != SHA256:
        raise ValueError("Pinned source integrity mismatch")
    data, metadata, docs = {}, {}, {}
    with tarfile.open(path, "r:gz") as archive:
        seen = set()
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk() or member.name in seen:
                raise ValueError("Unsafe/duplicate source archive member")
            seen.add(member.name)
        manifest = json.loads(archive.extractfile("csv_manifest.json").read(5_000_001))
        tables = {Path(t["file"]).name: t for t in manifest["tables"]}
        for table in ["schedules", "player_games"]:
            item = tables[table + ".csv.gz"]
            member = archive.getmember(item["file"])
            if not member.isfile() or member.size != item["bytes"] or member.size > 80_000_000:
                raise ValueError("CSV member expansion bound exceeded")
            raw = archive.extractfile(member).read()
            if sha(raw) != item["sha256"]:
                raise ValueError("CSV member checksum mismatch")
            schema_name = item["file"].replace(".csv.gz", ".schema.json")
            schema = json.loads(archive.extractfile(schema_name).read(1_000_001))
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                expanded = stream.read(450_000_001)
            if len(expanded) > 450_000_000:
                raise ValueError("Expanded CSV exceeds memory bound")
            wanted = ({"game_id", "home_team", "away_team", "game_date", "season", "game_date_time_known", "temp", "wind", "roof", "surface"} if table == "schedules"
                      else {"game_id", "player_id", "team", "opponent", "position", "season", "game_date", *METRICS})
            frame = pd.read_csv(io.BytesIO(expanded), dtype="string", keep_default_na=False, na_values=[r"\N"], usecols=lambda name: name in wanted)
            if len(frame) != item["rows"] or not set(frame).issubset({c["name"] for c in schema["columns"]}):
                raise ValueError("CSV manifest/schema mismatch")
            data[table], metadata[table] = frame, {**item, "schema_member": schema_name, "selected_columns": list(frame.columns)}
        for member in archive.getmembers():
            if member.isfile() and ("LICENSE" in Path(member.name).name or member.name.endswith("/manifest.json")) and member.size < 5_000_000:
                docs[member.name.replace("/", "_")] = archive.extractfile(member).read()
    return data, metadata, docs


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes()), "columns": {c: str(t) for c, t in frame.dtypes.items()}}


def main():
    require_github_hosted_runner()
    output, dist = ROOT / "data/weather_performance/nfl", ROOT / "dist"
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Output must be empty")
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"], prefix="nfl-weather-") as temp:
        source, members, docs = fetch_and_read(Path(temp))
        joined, excluded = panel(source["player_games"], source["schedules"])
    if len(joined) < 0.95 * len(source["player_games"]) or not (joined.weather_exposure_open_air & joined.wind_mph.notna()).any():
        raise ValueError("Insufficient exact joins or observed open-air weather")
    descriptive = aggregates(joined)
    tables = {}
    for split, frame in joined.groupby("evaluation_split", sort=True):
        name = "player_weather_outcomes_" + split
        tables[name] = write(frame, output / (name + ".csv.gz"))
    for name, frame in [("descriptive_weather_associations", descriptive), ("excluded_join_rows", excluded)]:
        tables[name] = write(frame, output / (name + ".csv.gz"))
    for name, body in docs.items():
        (output / ("source_" + name)).write_bytes(body)
    summary = {"status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(), "source_url": URL, "source_bytes": BYTES,
        "source_sha256": SHA256, "source_members": members, "joined_player_game_rows": len(joined), "distinct_games": int(joined.game_id.nunique()),
        "excluded_player_game_rows": len(excluded), "rows_by_split": joined.evaluation_split.value_counts().to_dict(),
        "rows_by_roof_exposure": joined.weather_exposure.value_counts().to_dict(),
        "open_air_rows_with_wind": int((joined.weather_exposure_open_air & joined.wind_mph.notna()).sum()),
        "open_air_rows_with_temperature": int((joined.weather_exposure_open_air & joined.temperature_fahrenheit_assumed.notna()).sum()),
        "temperature_invalid_rows": int(joined.temperature_value_invalid.sum()), "wind_invalid_rows": int(joined.wind_value_invalid.sum()),
        "tables": tables, "dictionary_url": DICTIONARY, "training_performed": False, "causal_inference_performed": False,
        "attendance_collected": False, "temperature_unit_independently_verified": False,
        "limitations": ["Actual weather and player statistics are retrospective outcomes; no historical forecast or pregame availability is verified.",
            "Schedule dictionary specifies wind mph and outdoors/open applicability; temperature conversion assumes Fahrenheit, not independently stated by that dictionary.",
            "Positions, roles, team quality, opponent, season, venue, usage and player selection confound descriptive comparisons; no causal effect or betting edge is estimated.",
            "Only source-present player games are included; missing participation is not imputed as zero. Player-game rows share game-level weather and are not independent experiments.",
            "Each outcome mean uses nonmissing player-game values including zeros; paired per-opportunity summaries require known outcomes and strictly positive known opportunities.",
            "Indoor/retractable-closed/unknown roof categories stay separate and receive no open-air weather buckets. No attendance or crowding is inferred."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    (output / "schema.json").write_text(json.dumps({"null_sentinel": r"\N", "ids_are_strings": True, "timestamps": "UTC", "tables": tables,
        "role": "postgame descriptive weather/outcome analysis only", "temperature_conversion": "(assumed Fahrenheit - 32) * 5 / 9", "wind_conversion": "mph * 1.609344"}, indent=2))
    (output / "LICENSE.txt").write_text("Player stats: nflverse contributors, CC BY 4.0, https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md . Schedule/weather: Lee Sharpe/nflverse nfldata, upstream terms apply; no blanket license asserted. Original source manifests are retained. Derived changes: exact game/team join, unit conversions with explicit temperature assumption, and unadjusted descriptive summaries. https://github.com/nflverse/nfldata .\n")
    dist.mkdir(exist_ok=True)
    archive = dist / "csv-nfl-weather-performance.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=1) as handle:
        handle.add(output, arcname="data/weather_performance/nfl")
    manifest = {"archive": archive.name, "bytes": archive.stat().st_size, "sha256": sha(archive.read_bytes()), "tables": tables}
    (dist / "nfl_weather_performance_asset_manifest.json").write_text(json.dumps(manifest, indent=2))
    (dist / "nfl_weather_performance_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({key: summary[key] for key in ["status", "joined_player_game_rows", "distinct_games", "excluded_player_game_rows", "rows_by_split", "rows_by_roof_exposure"]}), flush=True)


if __name__ == "__main__":
    main()
