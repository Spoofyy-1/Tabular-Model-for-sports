#!/usr/bin/env python3
"""Collect NFL tracking, depth charts and injury reports on hosted runners only.

Data is an audit-ready context layer, not a pregame feature matrix. Nothing here
changes the frozen models, and no historical timestamp is declared verified.
"""
import argparse
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
LICENSE_URL = "https://raw.githubusercontent.com/nflverse/nflverse-data/main/LICENSE.md"
SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
TEAM_ALIASES = {"LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV", "JAC": "JAX", "WSH": "WAS"}
DOCS = "https://nflreadr.nflverse.com/"
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def require_hosted_runner():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise SystemExit("NFL context collection is restricted to GitHub-hosted Actions. No local datasets may be read, downloaded or written.")
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace or not Path(workspace).is_dir():
        raise SystemExit("A valid GitHub Actions workspace is required")


def clean_team(series):
    return series.astype("string").replace(TEAM_ALIASES)


def get_file(url, destination, budget, allow_missing=False):
    """Stream bounded responses; raw files and all writes stay on the runner."""
    require_hosted_runner()
    for attempt in range(4):
        part = destination.with_suffix(destination.suffix + ".part")
        try:
            with requests.get(url, stream=True, timeout=(15, 90)) as response:
                if response.status_code == 404 and allow_missing:
                    return None, {"url": url, "status": "not_available", "checked_at_utc": utc_now(), "verified_asof": False}
                response.raise_for_status()
                size = 0
                sha = hashlib.sha256()
                with part.open("wb") as target:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        budget[0] += len(chunk)
                        if size > MAX_FILE_BYTES or budget[0] > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("NFL context raw download budget exceeded")
                        sha.update(chunk)
                        target.write(chunk)
                part.replace(destination)
                return destination, {
                    "url": url, "status": "downloaded", "bytes": size,
                    "sha256": sha.hexdigest(), "retrieved_at_utc": utc_now(),
                    "http_last_modified": response.headers.get("Last-Modified"),
                    "verified_asof": False,
                    "timestamp_note": "Retrieval and HTTP modification timestamps do not prove historical availability.",
                }
        except requests.RequestException:
            part.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(1 + 2 * attempt)
        except Exception:
            part.unlink(missing_ok=True)
            raise
    raise RuntimeError("Unreachable download failure")


def schedule_lookup(frame, start, end):
    frame = frame.loc[pd.to_numeric(frame["season"], errors="coerce").between(start, end)].copy()
    frame["game_date_time_known"] = frame["gametime"].notna()
    local = pd.to_datetime(frame["gameday"] + " " + frame["gametime"].fillna("00:00"), errors="coerce")
    frame["game_date"] = local.dt.tz_localize("America/New_York", ambiguous="raise", nonexistent="raise").dt.tz_convert("UTC")
    frame["season_type"] = frame["game_type"].replace({"WC": "POST", "DIV": "POST", "CON": "POST", "SB": "POST"})
    pieces = []
    for side, other in [("home", "away"), ("away", "home")]:
        selected = frame[["game_id", "season", "season_type", "week", "game_date", "game_date_time_known", side + "_team", other + "_team"]].copy()
        selected = selected.rename(columns={side + "_team": "team", other + "_team": "opponent"})
        selected["team"] = clean_team(selected["team"])
        selected["opponent"] = clean_team(selected["opponent"])
        pieces.append(selected)
    result = pd.concat(pieces, ignore_index=True)
    if result.duplicated(["season", "season_type", "week", "team"]).any():
        raise ValueError("Schedule contains ambiguous season/type/week/team keys")
    return result


def normalize(frame, table, source_season=None):
    """Pure transformation; may be tested only with synthetic frames locally."""
    frame = frame.copy()
    if table == "injuries":
        required = {"week", "team", "gsis_id"}
        if not required.issubset(frame.columns) or not {"game_type", "season_type"}.intersection(frame.columns):
            raise ValueError("Injury schema requires week, team, gsis_id and season type; source columns=" + repr(sorted(frame.columns)))
        if not {"report_status", "practice_status", "report_primary_injury", "practice_primary_injury"}.intersection(frame.columns):
            raise ValueError("Injury schema contains no recognized report or practice status fields")
        if "full_name" in frame and "player_name" not in frame:
            frame["player_name"] = frame["full_name"]
        frame["source_modification_column_present"] = "date_modified" in frame
        # nflverse-rosters #100 documents that the new 2025+ exporter omits
        # this column with no equivalent row timestamp. Never substitute the
        # release upload time or infer a publication date from week/kickoff.
        if "date_modified" not in frame:
            frame["date_modified"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    if "season" not in frame and source_season is not None:
        frame["season"] = source_season
    if "season" not in frame:
        raise ValueError("Required source season field absent from " + table)
    frame["season"] = pd.to_numeric(frame["season"], errors="raise").astype("Int64")
    if frame["season"].isna().any():
        raise ValueError("Source season is missing in " + table)
    for source in ("player_gsis_id", "gsis_id"):
        if source in frame:
            frame["player_id"] = frame[source].astype("string")
            break
    if "player_id" not in frame:
        raise ValueError("Required source GSIS identity field absent from " + table)
    frame["player_id_source"] = frame["player_id"]
    valid = frame["player_id"].str.fullmatch(r"00-\d{7}", na=False)
    frame["identity_valid"] = valid
    frame["identity_exclusion_reason"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    frame.loc[~valid, "identity_exclusion_reason"] = "Missing or malformed GSIS; excluded from canonical context table"
    frame.loc[~valid, "player_id"] = pd.NA
    source_team = next((c for c in ("team_abbr", "club_code", "team") if c in frame), None)
    if source_team is None:
        raise ValueError("Required source team field absent from " + table)
    frame["team_source"] = frame[source_team].astype("string")
    frame["team"] = clean_team(frame[source_team])
    if "game_type" in frame and "season_type" not in frame:
        frame["season_type"] = frame["game_type"]
    if "season_type" in frame:
        frame["season_type_source"] = frame["season_type"]
        frame["season_type"] = frame["season_type"].replace({"WC": "POST", "DIV": "POST", "CON": "POST", "SB": "POST"})
    if "week" in frame:
        frame["week"] = pd.to_numeric(frame["week"], errors="raise").astype("Int64")
    if table.startswith("ngs_") and ("week" not in frame or frame["week"].isna().any() or frame["week"].lt(0).any()):
        raise ValueError("NGS week must identify a weekly result or week-zero season summary")
    if table == "injuries" and (frame["week"].isna().any() or frame["week"].lt(0).any()):
        raise ValueError("Injury week must be nonnegative and present")
    timestamp_column = "date_modified" if table == "injuries" else "dt" if "dt" in frame else None
    frame["source_snapshot_at_utc"] = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce", format="mixed") if timestamp_column else pd.NaT
    if table == "injuries":
        frame["modification_timestamp_status"] = "source_value_parsed_unverified"
        frame.loc[frame["source_snapshot_at_utc"].isna(), "modification_timestamp_status"] = "source_value_null_or_unparseable"
        frame.loc[~frame["source_modification_column_present"], "modification_timestamp_status"] = "source_column_not_provided"
    frame["verified_asof"] = False
    frame["pregame_feature_enabled"] = False
    frame["sport"] = "NFL"
    frame["availability_status"] = (
        "injury_report_modified_timestamp_unverified" if table == "injuries" else
        "postgame_stat_final_revised_snapshot" if table.startswith("ngs_") else
        "source_loaded_timestamp_unverified" if "dt" in frame else
        "historical_week_snapshot_without_publication_timestamp"
    )
    if table == "injuries":
        frame.loc[frame["source_snapshot_at_utc"].isna(), "availability_status"] = "injury_report_without_available_modification_timestamp"
    return frame


def normalize_injuries(frame, source_season, schedule):
    """Preserve final report snapshots without declaring publication timing."""
    if frame.empty:
        raise ValueError("Empty injury source asset requires coverage review")
    frame = normalize(frame, "injuries", source_season=source_season)
    if not frame["season"].eq(source_season).all():
        raise ValueError("Injury source file contains a different season than requested")
    frame = attach_games(frame, schedule)
    frame["modified_before_kickoff"] = frame["source_snapshot_at_utc"].lt(frame["game_date"]) & frame["game_date_time_known"].astype("boolean").fillna(False)
    frame["record_level"] = "injury_report_final_snapshot_unverified"
    return frame


def attach_games(frame, schedule):
    if not {"season", "season_type", "week", "team"}.issubset(frame.columns):
        return frame
    frame = frame.merge(schedule, on=["season", "season_type", "week", "team"], how="left", validate="many_to_one")
    frame["split_by_game_date"] = "unmatched_game_date"
    frame.loc[frame["game_date"].lt(pd.Timestamp("2025-01-01", tz="UTC")), "split_by_game_date"] = "development_through_2024"
    frame.loc[frame["game_date"].ge(pd.Timestamp("2025-01-01", tz="UTC")), "split_by_game_date"] = "holdout_2025_onward"
    return frame


def field_meaning(column, table):
    own = {
        "player_id": "Canonical GSIS player identifier; never joined by display name.",
        "player_id_source": "Original source GSIS value retained for audit.",
        "team": "Canonical franchise code; relocation aliases normalized.",
        "team_source": "Original source team code.",
        "game_id": "nflverse schedule game ID; original team codes inside the ID are preserved.",
        "game_date": "Schedule kickoff in UTC; date-only placeholders are explicitly flagged.",
        "game_date_time_known": "True when source schedule provides kickoff time.",
        "source_snapshot_at_utc": "Parsed source dt, when present; upstream loaded timestamp, not independent point-in-time verification.",
        "date_modified": "Original injury source modification value; not guaranteed publication/ingestion time.",
        "source_modification_column_present": "Whether the source file actually supplied date_modified; false means the normalized null column was added for schema consistency.",
        "modification_timestamp_status": "Distinguishes absent source column, null/unparseable value and parsed but unverified timestamp.",
        "modified_before_kickoff": "Source modification timestamp precedes exact schedule kickoff; does not establish point-in-time availability.",
        "dt": "Unmodified upstream snapshot timestamp string (2025 onward depth charts).",
        "verified_asof": "Always false: historical availability has not been independently verified.",
        "pregame_feature_enabled": "Always false; no automatic addition to model features.",
        "availability_status": "Classifies postgame results, undated historical snapshots or source-timestamped snapshots.",
        "split_by_game_date": "Development before 2025-01-01 UTC or holdout thereafter; never assigned using season alone.",
        "source_file": "Raw source filename referenced in manifest downloads.",
        "record_level": "Week-level outcomes versus completed-season summaries. Week 0 summaries are segregated.",
        "identity_valid": "True for GSIS strings matching 00- plus seven digits.",
        "identity_exclusion_reason": "Reason row is excluded from canonical context tables.",
    }
    if column in own:
        if column == "source_snapshot_at_utc" and name_is_injury(table):
            return "Parsed injury date_modified UTC; not guaranteed publication/ingestion time."
        return own[column]
    if column in ("season", "week", "season_type", "season_type_source", "sport", "opponent"):
        return "Source/game identity or calendar metadata; season includes following-calendar-year playoffs."
    return ("Source final injury report/practice status or identity; preserved missing values; historical availability unverified." if name_is_injury(table) else
            "Upstream NGS realized statistic/identity; see NGS dictionary; outcome measurements must be lagged." if table.startswith("ngs_") else
            "Upstream depth-chart field; see dictionary for this source era; preserved without imputing missing values.")


def name_is_injury(name):
    return name.startswith("injuries_") or name == "injuries"


def prepare_table(frame, name):
    """Validate and sort a frame without I/O, allowing synthetic local tests."""
    frame = frame.copy()
    before = len(frame)
    frame = frame.drop_duplicates()
    # Historical files have evolving scalar types; retain strings for mixed objects.
    for column in frame.select_dtypes(include=["object"]).columns:
        if len(frame[column].dropna().map(type).unique()) > 1:
            frame[column] = frame[column].astype("string")
    order = [c for c in ["season", "game_date", "source_snapshot_at_utc", "week", "team", "player_id", "formation", "depth_position", "pos_slot", "pos_rank"] if c in frame]
    frame = frame.sort_values(order, na_position="last", kind="stable").reset_index(drop=True)
    weekly_ngs = name.startswith("ngs_") and name.endswith("_weekly")
    keys = ["season", "season_type", "week", "team", "player_id"]
    if weekly_ngs and frame.duplicated(keys).any():
        raise ValueError("Conflicting canonical NGS player/team/week rows in " + name)
    return frame, before - len(frame)


def write_table(frame, name, out):
    require_hosted_runner()
    frame, duplicates_removed = prepare_table(frame, name)
    path = out / (name + ".parquet")
    frame.to_parquet(path, index=False, compression="zstd")
    fields = {c: {"dtype": str(frame[c].dtype), "null_rows": int(frame[c].isna().sum()),
                  "verified_asof": False, "meaning": field_meaning(c, name)} for c in frame}
    info = {
        "file": path.name, "rows": len(frame), "columns": len(frame.columns),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
        "exact_duplicate_rows_removed": duplicates_removed, "verified_asof": False,
        "pregame_feature_enabled": False,
        "season_counts": {str(k): int(v) for k, v in frame.groupby("season", dropna=False).size().items()},
        "distinct_players": int(frame["player_id"].nunique()),
        "rows_without_valid_gsis": int(frame["player_id"].isna().sum()),
        "rows_with_source_snapshot_timestamp": int(frame["source_snapshot_at_utc"].notna().sum()),
        "availability_counts": {str(k): int(v) for k, v in frame["availability_status"].value_counts().items()},
    }
    if "game_id" in frame:
        info["rows_without_schedule_match"] = int(frame["game_id"].isna().sum())
        info["rows_by_game_date_split"] = {str(k): int(v) for k, v in frame["split_by_game_date"].value_counts().items()}
    if "modified_before_kickoff" in frame:
        info["rows_modified_before_exact_kickoff"] = int(frame["modified_before_kickoff"].sum())
        info["rows_with_missing_or_invalid_modification_timestamp"] = int(frame["source_snapshot_at_utc"].isna().sum())
        info["modification_timestamp_status_counts"] = {str(k): int(v) for k, v in frame["modification_timestamp_status"].value_counts(dropna=False).items()}
        info["weeks_by_season"] = {str(k): sorted(int(w) for w in part["week"].dropna().unique()) for k, part in frame.groupby("season")}
        for column in ["report_status", "practice_status"]:
            if column in frame:
                info[column + "_counts"] = {str(k): int(v) for k, v in frame[column].value_counts(dropna=False).items()}
    return info, fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-season", type=int, default=2001)
    parser.add_argument("--end-season", type=int, default=2026)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "context" / "nfl")
    args = parser.parse_args()
    require_hosted_runner()
    if not 2001 <= args.start_season <= args.end_season <= datetime.now(timezone.utc).year:
        raise SystemExit("Require 2001 <= start-season <= end-season <= current year")
    if not os.environ.get("RUNNER_TEMP"):
        raise SystemExit("RUNNER_TEMP is required")
    cache = Path(os.environ["RUNNER_TEMP"]) / "nfl-context"
    out = args.output_dir
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    budget = [0]
    downloads = []
    outputs = {}
    dictionary = {}
    schedule_path, meta = get_file(SCHEDULE_URL, cache / "games.csv", budget)
    downloads.append(dict(meta, category="schedule_lookup"))
    schedule = schedule_lookup(pd.read_csv(schedule_path, low_memory=False), args.start_season, args.end_season)
    license_path, meta = get_file(LICENSE_URL, out / "NFLVERSE_DATA_LICENSE.txt", budget)
    downloads.append(dict(meta, category="license"))

    def save(frame, name):
        outputs[name], dictionary[name] = write_table(frame, name, out)
        print(json.dumps({"table": name, "rows": outputs[name]["rows"], "verified_asof": False}), flush=True)

    for kind in ["passing", "receiving", "rushing"]:
        name = "ngs_" + kind
        filename = name + ".parquet"
        path, meta = get_file(BASE + "nextgen_stats/" + filename, cache / filename, budget)
        downloads.append(dict(meta, category=name, source_file=filename))
        frame = normalize(pd.read_parquet(path), name)
        frame = frame.loc[frame["season"].between(args.start_season, args.end_season)].copy()
        frame["source_file"] = filename
        excluded = frame.loc[~frame["identity_valid"]].copy()
        frame = frame.loc[frame["identity_valid"]].copy()
        frame["record_level"] = "weekly"
        weekly = frame.loc[frame["week"].gt(0)].copy()
        summary = frame.loc[frame["week"].eq(0)].copy()
        summary["record_level"] = "completed_season_summary_not_pregame"
        save(attach_games(weekly, schedule), name + "_weekly")
        save(summary, name + "_season_summaries")
        if len(excluded):
            save(excluded, name + "_excluded_identity_rows")

    depth = {"depth_charts_weekly_2001_2024": [], "depth_charts_snapshots_2025_onward": []}
    excluded_depth = []
    for year in range(args.start_season, args.end_season + 1):
        filename = "depth_charts_%d.parquet" % year
        path, meta = get_file(BASE + "depth_charts/" + filename, cache / filename, budget, allow_missing=True)
        downloads.append(dict(meta, category="depth_charts", season=year, source_file=filename))
        if path is None:
            continue
        frame = normalize(pd.read_parquet(path), "depth_charts", source_season=year)
        frame["source_file"] = filename
        excluded_depth.append(frame.loc[~frame["identity_valid"]].copy())
        frame = frame.loc[frame["identity_valid"]].copy()
        if year <= 2024:
            frame["record_level"] = "weekly_depth_snapshot_unverified"
            depth["depth_charts_weekly_2001_2024"].append(attach_games(frame, schedule))
        else:
            frame["record_level"] = "timestamped_depth_snapshot_unverified"
            depth["depth_charts_snapshots_2025_onward"].append(frame)
    for name, pieces in depth.items():
        if pieces:
            save(pd.concat(pieces, ignore_index=True), name)
    if excluded_depth and any(len(p) for p in excluded_depth):
        save(pd.concat(excluded_depth, ignore_index=True), "depth_charts_excluded_identity_rows")
    if not any(depth.values()):
        raise RuntimeError("No depth-chart source files available")

    injury_frames = []
    injury_excluded = []
    for year in range(max(2025, args.start_season), args.end_season + 1):
        filename = "injuries_%d.parquet" % year
        path, meta = get_file(BASE + "injuries/" + filename, cache / filename, budget, allow_missing=True)
        downloads.append(dict(meta, category="injuries", season=year, source_file=filename))
        if path is None:
            continue
        frame = normalize_injuries(pd.read_parquet(path), year, schedule)
        frame["source_file"] = filename
        injury_excluded.append(frame.loc[~frame["identity_valid"]].copy())
        injury_frames.append(frame.loc[frame["identity_valid"]].copy())
    if injury_frames:
        save(pd.concat(injury_frames, ignore_index=True), "injuries_2025_onward")
    if injury_excluded and any(len(p) for p in injury_excluded):
        save(pd.concat(injury_excluded, ignore_index=True), "injuries_excluded_identity_rows")

    manifest = {
        "created_at_utc": utc_now(), "requested_seasons": [args.start_season, args.end_season],
        "execution_environment": "GitHub-hosted Actions only; no sports data on owner workstation",
        "attribution": "NFL Next Gen Stats and NFL injury reports via nflverse; NFL Data Exchange historical depth charts; ESPN depth charts from 2025; nflverse contributors; Lee Sharpe nflverse schedule lookup.",
        "licenses": [{"source": "nflverse/nflverse-data", "license": "CC-BY-4.0", "url": LICENSE_URL,
                      "local_text": license_path.name},
                     {"source": "nflverse/nfldata schedule lookup", "license": "No blanket third-party license asserted", "url": "https://github.com/nflverse/nfldata"}],
        "rights_notice": "nflverse notes NFL data belong to their respective owners and their terms apply. The repository license does not grant rights beyond its licensors' authority. No upstream warranty is provided.",
        "terms_url": "https://nflverse.nflverse.com/#terms-of-use",
        "documentation": {"nextgen_stats": DOCS + "reference/load_nextgen_stats.html", "depth_charts": DOCS + "reference/load_depth_charts.html", "availability": DOCS + "articles/nflverse_data_schedule.html", "depth_chart_revision_code": "https://github.com/nflverse/nflverse-rosters/blob/main/exec/update-depth-charts.R"},
        "dictionary_urls": {"ngs": DOCS + "articles/dictionary_nextgen_stats.html", "depth_charts": DOCS + "articles/dictionary_depth_charts.html", "injuries": DOCS + "articles/dictionary_injuries.html"},
        "downloads": downloads, "outputs": outputs,
        "transformations": ["Normalized GSIS field names and franchise abbreviations; preserved original identifiers.", "Quarantined missing/malformed GSIS rows instead of guessing identities.", "Removed exact duplicate rows; sorted by season/game/snapshot/week/team/GSIS/role.", "Separated NGS week-zero season summaries from weekly realized outcomes.", "Joined weekly rows only to unambiguous season/type/week/team schedule keys; unmatched rows preserved and counted.", "Kept distinct historical and timestamped depth-chart source eras; preserved formation/position/slot/rank rows.", "Preserved missing values; no absence or low-sample NGS exclusion is interpreted as zero.", "Kept all tables out of training; verified_asof and pregame_feature_enabled are false for every field/table."],
        "caveats": ["NGS covers players meeting attempt thresholds, not every active player, since 2016.", "NGS measurements include coverage separation/cushion, stacked boxes, expected yards, completion expectation and throw timing, but are realized outcomes requiring earlier-game lagging.", "Historical NGS downloads can be revised; a lagged statistic still needs a historical availability/revision audit before claiming a tradable backtest.", "Depth charts before 2025 are weekly snapshots with no verified publication timestamp. From 2025 the source supplies dt loaded timestamps; those are preserved, not independently verified.", "Snapshot depth charts intentionally have no forced week/game assignment; future consumers must choose a snapshot strictly before their decision time.", "Current-season files are incomplete as of retrieval. Absence may mean no published data, threshold exclusions, missing GSIS or other source gaps.", "Development ends 2024-12-31 UTC; 2024-season games in calendar 2025 are holdout. Season summaries have no pregame eligibility.", "No market prices, fills, fees or betting profitability are inferred."],
        "deferred_sources": ["PFR advanced statistics, combine and draft datasets: additional third-party reuse terms require source-specific review.", "Participation/FTN bulk play-level data: pre-2023 source died; 2023+ supplied after season end with CC-BY-SA-4.0 obligations. Not included in this bounded context update."],
    }
    summary = {
        "created_at_utc": manifest["created_at_utc"], "source_files_downloaded": sum(m["status"] == "downloaded" for m in downloads),
        "source_files_unavailable": [m.get("source_file") for m in downloads if m["status"] != "downloaded"],
        "raw_download_bytes": budget[0], "total_rows": sum(x["rows"] for x in outputs.values()),
        "total_canonical_rows": sum(x["rows"] for k, x in outputs.items() if "excluded_identity_rows" not in k),
        "tables": outputs, "verified_asof": False, "model_changes": False,
        "split": "Development game dates through 2024; holdout 2025 onward. New context is not used for training.",
    }
    manifest["caveats"].append("The 2025+ depth-chart publisher reapplies current ESPN-to-GSIS mappings to archived rows. Old dt values do not establish that the current GSIS mapping was available at that time.")
    manifest["caveats"].append("Injury release assets for 2025 and 2026 supersede earlier source-unavailable notes. These are report snapshots, not a complete history of report revisions. date_modified and modified_before_kickoff do not prove pregame availability. Published weeks, null timestamps, unmatched games and status counts are reported.")
    manifest["documentation"]["injuries"] = DOCS + "reference/load_injuries.html"
    manifest["documentation"]["injury_release_inventory"] = "https://api.github.com/repos/nflverse/nflverse-data/releases/tags/injuries"
    manifest["documentation"]["injury_missing_timestamps"] = "https://github.com/nflverse/nflverse-rosters/issues/100"
    manifest["caveats"].append("The new 2025+ injury exporter omits per-row modification timestamps (upstream issue100). Missing timestamps are explicit nulls with source_column_not_provided status, never substituted from file upload time.")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "DATA_DICTIONARY.json").write_text(json.dumps(dictionary, indent=2))
    (out / "context_summary.json").write_text(json.dumps(summary, indent=2))
    total_bytes = sum(p.stat().st_size for p in cache.glob("*")) + sum(p.stat().st_size for p in out.glob("*"))
    if total_bytes > MAX_TOTAL_BYTES:
        raise RuntimeError("NFL context total disk budget exceeded")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
