#!/usr/bin/env python3
"""Collect NBA schedules and play events on GitHub-hosted Actions runners only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

RELEASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
LICENSES = [
    ("hoopR-nba-data-LICENSE.txt", "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/LICENSE.md", "CC-BY-4.0"),
    ("sportsdataverse-data-LICENSE.txt", "https://raw.githubusercontent.com/sportsdataverse/sportsdataverse-data/main/LICENSE", "MIT"),
]
LIMIT = 80_000_000


def now():
    return datetime.now(timezone.utc).isoformat()


def series(frame, *names):
    for name in names:
        if name in frame:
            return frame[name]
    return pd.Series(pd.NA, index=frame.index, dtype="object")


def identifier(values):
    # Numeric provider identifiers; nullable strings match existing ESPN boxes.
    return pd.to_numeric(values, errors="coerce").astype("Int64").astype("string")


def boolean(values):
    return values.astype("string").str.lower().map(
        {"true": True, "false": False, "1": True, "0": False, "1.0": True, "0.0": False}
    ).astype("boolean")


def dates(values):
    return pd.to_datetime(values, utc=True, errors="coerce", format="mixed")


def date_precision(values):
    text = values.astype("string")
    result = pd.Series("unknown", index=values.index, dtype="string")
    valid = dates(values).notna()
    result.loc[valid] = "source_calendar_date"
    result.loc[valid & text.str.contains(r"\d{2}:\d{2}", na=False)] = "source_timestamp"
    return result


def split(values, precision=None):
    result = pd.Series("unknown_date", index=values.index, dtype="string")
    result.loc[values < pd.Timestamp("2024-01-01", tz="UTC")] = "fit_pre_2024"
    result.loc[(values >= pd.Timestamp("2024-01-01", tz="UTC")) & (values < pd.Timestamp("2025-01-01", tz="UTC"))] = "calibration_2024"
    result.loc[values >= pd.Timestamp("2025-01-01", tz="UTC")] = "holdout_2025_plus"
    if precision is not None:
        boundaries = values.dt.strftime("%Y-%m-%d").isin(["2023-12-31", "2024-01-01", "2024-12-31", "2025-01-01"])
        result.loc[precision.eq("source_calendar_date") & boundaries] = "date_precision_unresolved"
    return result


def fetch(url, cache):
    require_github_hosted_runner()
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    if not cache.resolve().is_relative_to(runner_temp):
        raise RuntimeError("Raw NBA context cache must be inside RUNNER_TEMP")
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / url.rsplit("/", 1)[-1]
    # Re-fetch deliberately: mutable upstream assets need a truthful retrieval time/hash.
    for attempt in range(4):
        digest = hashlib.sha256()
        received = 0
        try:
            with requests.get(url, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                with destination.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        received += len(chunk)
                        if received > LIMIT:
                            raise RuntimeError("NBA asset exceeds the 80MB compressed safety bound")
                        digest.update(chunk)
                        stream.write(chunk)
                return destination, {"source_url": url, "downloaded_at_utc": now(),
                    "sha256": digest.hexdigest(), "bytes": received,
                    "last_modified": response.headers.get("Last-Modified"),
                    "license": "CC-BY-4.0 producer; MIT distribution repository"}
        except requests.RequestException:
            destination.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def normalize_schedule(raw, season):
    out = pd.DataFrame(index=raw.index)
    out["game_id"] = identifier(series(raw, "game_id", "id"))
    out["game_date"] = dates(series(raw, "game_date_time", "date", "start_date"))
    out["source_game_date_precision"] = date_precision(series(raw, "game_date_time", "date", "start_date"))
    out["game_local_date"] = series(raw, "game_date").astype("string")
    out["season"] = season
    out["season_type"] = pd.to_numeric(series(raw, "season_type"), errors="coerce").astype("Int64")
    for name, source in [("home_team_id", "home_id"), ("away_team_id", "away_id"), ("venue_id", "venue_id")]:
        out[name] = identifier(series(raw, source))
    for name in ["venue_full_name", "venue_address_city", "venue_address_state", "status_type_name", "status_type_state", "notes_headline"]:
        out[name] = series(raw, name).astype("string")
    out["neutral_site"] = boolean(series(raw, "neutral_site"))
    out["game_completed_actual"] = boolean(series(raw, "status_type_completed"))
    out["attendance_actual"] = pd.to_numeric(series(raw, "attendance"), errors="coerce").astype("Float64")
    out["venue_capacity_source"] = pd.to_numeric(series(raw, "venue_capacity"), errors="coerce").astype("Float64")
    out["evaluation_split"] = split(out.game_date, out.source_game_date_precision)
    out["historical_publication_time_verified"] = False
    return out


def schedule_team_context(schedule):
    frames = []
    for side, other in [("home", "away"), ("away", "home")]:
        frame = schedule[["game_id", "game_date", "season", "season_type", "evaluation_split", "source_game_date_precision", "venue_id", "neutral_site"]].copy()
        frame["team_id"] = schedule[f"{side}_team_id"]
        frame["opponent_id"] = schedule[f"{other}_team_id"]
        frame["is_home"] = side == "home"
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True).dropna(subset=["game_id", "team_id", "game_date"])
    out = out.sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)
    group = out.groupby("team_id", sort=False)
    out["previous_listed_game_date"] = group.game_date.shift(1)
    out["interval_uses_calendar_dates"] = (out.source_game_date_precision.eq("source_calendar_date") | group.source_game_date_precision.shift(1).eq("source_calendar_date")).astype("boolean")
    out["hours_since_previous_listed_game"] = ((out.game_date - out.previous_listed_game_date).dt.total_seconds() / 3600).where(~out.interval_uses_calendar_dates.fillna(False))
    # UTC calendar intervals are explicit; do not call this actual physiological rest/travel.
    out["utc_calendar_days_since_previous_listed_game"] = (out.game_date.dt.normalize() - out.previous_listed_game_date.dt.normalize()).dt.days.astype("Int64")
    out["previous_venue_id"] = group.venue_id.shift(1)
    out["venue_changed_since_previous_listed_game"] = out.venue_id.ne(out.previous_venue_id).astype("boolean")
    return out.sort_values(["game_date", "game_id", "team_id"]).reset_index(drop=True)


def normalize_events(raw, season):
    out = pd.DataFrame(index=raw.index)
    out["game_id"] = identifier(series(raw, "game_id"))
    # Preserve provider event IDs as labels; no numeric conversion or invented keys.
    out["event_id"] = series(raw, "id").astype("string").str.replace(r"\.0$", "", regex=True)
    out["game_date"] = dates(series(raw, "game_date_time", "game_date"))
    out["source_game_date_precision"] = date_precision(series(raw, "game_date_time", "game_date"))
    out["season"] = season
    for name in ["team_id", "home_team_id", "away_team_id", "athlete_id_1", "athlete_id_2", "athlete_id_3"]:
        out[name] = identifier(series(raw, name))
    out = out.rename(columns={"athlete_id_1": "primary_player_id", "athlete_id_2": "secondary_actor_id", "athlete_id_3": "tertiary_actor_id"})
    for name in ["type_id", "type_text", "type_abbreviation", "text", "clock_display_value", "sequence_number"]:
        out[name] = series(raw, name).astype("string")
    for name in ["season_type", "game_play_number", "period_number", "away_score", "home_score", "score_value", "coordinate_x_raw", "coordinate_y_raw", "coordinate_x", "coordinate_y"]:
        alternate = "period" if name == "period_number" else name
        out[name] = pd.to_numeric(series(raw, name, alternate), errors="coerce").astype("Float64")
    out["shooting_play"] = boolean(series(raw, "shooting_play"))
    out["scoring_play"] = boolean(series(raw, "scoring_play"))
    out["source_wallclock"] = dates(series(raw, "wallclock"))
    out["evaluation_split"] = split(out.game_date, out.source_game_date_precision)
    out["historical_publication_time_verified"] = False
    return out.sort_values(["game_date", "game_id", "game_play_number", "event_id"], na_position="last").reset_index(drop=True)


def player_event_context(events):
    out = events.dropna(subset=["game_id", "primary_player_id"]).copy()
    out["observed_shooting_events"] = out.shooting_play.fillna(False).astype(int)
    out["shooting_events_with_coordinates"] = (out.shooting_play.fillna(False) & out.coordinate_x.notna() & out.coordinate_y.notna()).astype(int)
    out["observed_scoring_shooting_events"] = (out.shooting_play.fillna(False) & out.scoring_play.fillna(False)).astype(int)
    # Text-based proxy is documented separately from boxscore attempts.
    three = out.text.str.contains(r"three[ -]point|3[ -]point", case=False, regex=True, na=False)
    out["text_identified_three_point_events"] = (out.shooting_play.fillna(False) & three).astype(int)
    out["primary_actor_overtime_events"] = out.period_number.ge(5).fillna(False).astype(int)
    out["described_shot_distance_feet"] = pd.to_numeric(out.text.str.extract(r"(?i)(\d+(?:\.\d+)?)\s*(?:-\s*)?foot\b", expand=False), errors="coerce").where(out.shooting_play.fillna(False))
    out["shooting_flag_observed_events"] = out.shooting_play.notna().astype(int)
    out["period_observed_events"] = out.period_number.notna().astype(int)
    out["primary_actor_events"] = 1
    metrics = ["primary_actor_events", "shooting_flag_observed_events", "period_observed_events", "observed_shooting_events", "shooting_events_with_coordinates", "observed_scoring_shooting_events", "text_identified_three_point_events", "primary_actor_overtime_events"]
    aggregation = {name: (name, "sum") for name in metrics}
    aggregation.update(game_date=("game_date", "first"), season=("season", "first"),
        evaluation_split=("evaluation_split", "first"), source_game_date_precision=("source_game_date_precision", "first"), described_shot_distance_mean_feet=("described_shot_distance_feet", "mean"),
        described_shot_distance_observations=("described_shot_distance_feet", "count"))
    result = out.groupby(["game_id", "primary_player_id"], as_index=False).agg(**aggregation)
    result = result.rename(columns={"primary_player_id": "player_id"})
    result["same_game_retrospective_only"] = True
    return result.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)


def table_report(frame):
    timestamps = frame.game_date.dropna() if "game_date" in frame else pd.Series(dtype="datetime64[ns, UTC]")
    return {"rows": len(frame), "games": int(frame.game_id.nunique()) if "game_id" in frame else None,
        "date_min": timestamps.min().isoformat() if len(timestamps) else None,
        "date_max": timestamps.max().isoformat() if len(timestamps) else None,
        "split_counts": {str(k): int(v) for k, v in frame.evaluation_split.value_counts(dropna=False).items()} if "evaluation_split" in frame else {},
        "schema": {name: str(dtype) for name, dtype in frame.dtypes.items()},
        "missing_values": {name: int(n) for name, n in frame.isna().sum().items()}}


def write_parquet(frame, destination):
    require_github_hosted_runner()
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(destination, index=False, compression="zstd")


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=2002)
    parser.add_argument("--end", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "context" / "nba")
    parser.add_argument("--cache", type=Path, default=Path(os.environ["RUNNER_TEMP"]) / "nba_context")
    args = parser.parse_args()
    if not 2002 <= args.start <= args.end <= 2026:
        raise ValueError("Supported source seasons: 2002 through 2026")
    args.output.mkdir(parents=True, exist_ok=True)
    assets, seasons, schedules = [], [], []
    player_parts = []
    for season in range(args.start, args.end + 1):
        path, provenance = fetch(f"{RELEASE}/espn_nba_schedules/nba_schedule_{season}.parquet", args.cache)
        schedule_raw = pd.read_parquet(path)
        schedule = normalize_schedule(schedule_raw, season)
        provenance.update(dataset="schedules", season=season, raw_rows=len(schedule_raw), source_schema={c: str(d) for c, d in schedule_raw.dtypes.items()})
        assets.append(provenance)
        schedules.append(schedule)
        del schedule_raw
        path, provenance = fetch(f"{RELEASE}/espn_nba_pbp/play_by_play_{season}.parquet", args.cache)
        raw = pd.read_parquet(path)
        provenance.update(dataset="play_by_play", season=season, raw_rows=len(raw), source_schema={c: str(d) for c, d in raw.dtypes.items()})
        events = normalize_events(raw, season)
        del raw
        assets.append(provenance)
        if sum(a["bytes"] for a in assets) > 1_000_000_000:
            raise RuntimeError("Combined compressed NBA inputs exceed 1GB")
        context = player_event_context(events)
        player_parts.append(context)
        write_parquet(events, args.output / "events" / f"season={season}" / "events.parquet")
        event_report = table_report(events)
        event_report.update(season=season,
            missing_game_id_rows=int(events.game_id.isna().sum()),
            missing_event_id_rows=int(events.event_id.isna().sum()),
            duplicate_nonnull_event_keys=int(events.dropna(subset=["game_id", "event_id"]).duplicated(["game_id", "event_id"]).sum()),
            primary_actor_present_rows=int(events.primary_player_id.notna().sum()),
            player_game_rows=len(context),
            event_games_without_schedule=int((~events.game_id.dropna().drop_duplicates().isin(schedule.game_id)).sum()))
        seasons.append(event_report)
        print(json.dumps({"sport": "NBA", "season": season, "event_rows": len(events), "player_game_rows": len(context), "schedule_rows": len(schedule)}), flush=True)
        del events
    schedule = pd.concat(schedules, ignore_index=True).sort_values(["game_date", "game_id"]).reset_index(drop=True)
    if schedule.dropna(subset=["game_id"]).duplicated("game_id").any():
        raise ValueError("Duplicate NBA schedule game IDs require reconciliation")
    team_schedule = schedule_team_context(schedule)
    players = pd.concat(player_parts, ignore_index=True).sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    if players.duplicated(["game_id", "player_id"]).any():
        raise ValueError("Duplicate cross-season player-game event summary IDs")
    for name, frame in [("schedules", schedule), ("team_schedule_context", team_schedule), ("player_game_event_context", players)]:
        write_parquet(frame, args.output / f"{name}.parquet")
    license_records = []
    for filename, url, license_name in LICENSES:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        (args.output / filename).write_text(response.text)
        license_records.append({"source_url": url, "license": license_name, "filename": filename,
            "retrieved_at_utc": now(), "sha256": hashlib.sha256(response.content).hexdigest()})
    summary = {"generated_at_utc": now(), "sport": "NBA", "season_end_years": [args.start, args.end],
        "event_rows": sum(s["rows"] for s in seasons), "player_game_event_rows": len(players),
        "schedule_rows": len(schedule), "compressed_source_bytes": sum(a["bytes"] for a in assets),
        "tables": {"schedules": table_report(schedule), "team_schedule_context": table_report(team_schedule), "player_game_event_context": table_report(players)},
        "seasons": seasons,
        "id_namespace": "ESPN: game_id, player_id, team_id join existing NBA boxes without name matching",
        "existing_boxscore_join_status": "Identifier namespace compatible; actual boxscore overlap requires separate remote join audit",
        "development_end_exclusive_utc": "2025-01-01T00:00:00Z",
        "feature_policy": ["All play events and player event summaries are retrospective outcomes; use only earlier completed games.",
            "Attendance is observed attendance, not a pregame forecast; never use same-game attendance as a historical pregame feature.",
            "Schedule venue/start fields are mutable retrospective records, not verified pregame announcement snapshots.",
            "Date-only fallback precision is explicit; cutoff-boundary rows are unresolved and hourly intervals using date-only endpoints are missing.",
            "Team schedule gaps are intervals between source-listed games, not verified travel itineraries or injury layoff durations.",
            "Secondary and tertiary actors are not labeled defenders. Shot coordinates retain upstream coordinate systems.",
            "Missing athlete IDs or shooting flags limit coverage. Event summaries are partial observations, not reconciled boxscore statistics.",
            "source_wallclock is provider event timing, not certified quote/news publication timing.",
            "No model training or holdout tuning is performed by this collector."],
        "unavailable_fields": ["confirmed defensive assignments", "optical tracking touches/potential assists/rebound chances", "point-in-time historical injury announcements", "who attended the stadium", "verified historical prop lines"],
        "attribution": "NBA ESPN-derived play events and schedules compiled by hoopR/SportsDataverse; normalized and aggregated by this project.",
        "license_note": "Producer states CC BY 4.0 for data/code/docs; distribution repo MIT. Preserve attribution; third-party rights are not newly granted by this pipeline.",
        "licenses": license_records, "source_assets": assets}
    (args.output / "context_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in ["sport", "season_end_years", "event_rows", "player_game_event_rows", "schedule_rows", "compressed_source_bytes"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
