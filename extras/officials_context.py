#!/usr/bin/env python3
"""Cloud-only, licensed NBA/NFL officiating crew context; no model integration."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner
sys.path.insert(0, str(ROOT / "context"))
from nba_context import normalize_schedule

NBA_REPO = "sportsdataverse/sportsdataverse-data"
NFL_REPO = "nflverse/nflverse-data"
SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
LICENSES = {
    "NBA_PRODUCER_LICENSE.md": "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/LICENSE.md",
    "NBA_DISTRIBUTION_LICENSE.txt": "https://raw.githubusercontent.com/sportsdataverse/sportsdataverse-data/main/LICENSE",
    "NFL_OFFICIALS_LICENSE.md": "https://raw.githubusercontent.com/nflverse/nflverse-data/main/LICENSE.md",
}


def now():
    return datetime.now(timezone.utc).isoformat()


class Remote:
    def __init__(self):
        self.calls, self.bytes, self.sources = 0, 0, []

    def get(self, url, max_bytes=10_000_000, asset=None):
        require_github_hosted_runner()
        for attempt in range(2):
            self.calls += 1
            if self.calls > 100:
                raise RuntimeError("Officials collection exceeded 100 source request budget")
            try:
                with requests.get(url, headers={"User-Agent": "SportsPropsResearch/1.0 (https://github.com/Spoofyy-1/Tabular-Model-for-sports)"}, stream=True, timeout=(15, 90)) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for block in response.iter_content(1024 * 1024):
                        size += len(block)
                        self.bytes += len(block)
                        if size > max_bytes or self.bytes > 100_000_000:
                            raise RuntimeError("Officials collection exceeded 100 MB source budget")
                        chunks.append(block)
                    content = b"".join(chunks)
                    digest = hashlib.sha256(content).hexdigest()
                    if asset and (len(content) != asset["size"] or asset.get("digest") not in (None, "sha256:" + digest)):
                        raise ValueError("Release asset integrity changed after metadata retrieval")
                    record = {"url": url, "bytes": size, "sha256": digest, "retrieved_at_utc": now(),
                              "http_last_modified": response.headers.get("Last-Modified"),
                              "asset_updated_at_utc": asset.get("updated_at") if asset else None}
                    self.sources.append(record)
                    return content
            except requests.RequestException:
                if attempt == 1:
                    raise
                time.sleep(2)
        raise RuntimeError("Unreachable source fetch")

    def release(self, repo, tag):
        payload = json.loads(self.get("https://api.github.com/repos/" + repo + "/releases/tags/" + tag))
        assets = payload.get("assets", [])
        if len({item["name"] for item in assets}) != len(assets):
            raise ValueError("Duplicate release asset names")
        return {item["name"]: item for item in assets}

    def table(self, asset):
        if asset["size"] > 10_000_000:
            raise ValueError("Officials/schedule asset exceeds per-file bound")
        return pd.read_parquet(io.BytesIO(self.get(asset["browser_download_url"], asset=asset)))


def get_column(frame, name):
    return frame[name] if name in frame else pd.Series(pd.NA, index=frame.index, dtype="string")


def identity(values):
    if pd.api.types.is_numeric_dtype(values):
        values = pd.to_numeric(values, errors="coerce")
        return values.where(values.mod(1).eq(0)).astype("Int64").astype("string")
    return values.astype("string").str.strip().replace("", pd.NA)


def annotate(frame):
    frame["historical_publication_time_verified"] = False
    frame["assignment_known_before_game_verified"] = False
    frame["automatic_training_join_allowed"] = False
    frame["availability_class"] = "postgame_revised_officiating_snapshot"
    frame["source_license"] = "CC-BY-4.0"
    frame["evaluation_split"] = "unknown_game_date"
    valid = frame.game_date.notna()
    frame.loc[valid & frame.game_date.lt(pd.Timestamp("2025-01-01", tz="UTC")), "evaluation_split"] = "development_through_2024"
    frame.loc[valid & frame.game_date.ge(pd.Timestamp("2025-01-01", tz="UTC")), "evaluation_split"] = "holdout_2025_onward"
    if "game_date_time_known" in frame:
        uncertain = ~frame.game_date_time_known.astype("boolean").fillna(False) & frame.game_date.dt.strftime("%Y-%m-%d").isin(["2024-12-31", "2025-01-01"])
        frame.loc[uncertain, "evaluation_split"] = "date_precision_unresolved"
    return frame


def quarantine(frame, keys):
    frame = frame.drop_duplicates().reset_index(drop=True)
    frame["quality_exclusion"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    missing = frame[keys].isna().any(axis=1)
    conflict = frame.duplicated(keys, keep=False)
    frame.loc[missing, "quality_exclusion"] = "missing_game_or_official_identity"
    frame.loc[~missing & conflict, "quality_exclusion"] = "conflicting_game_official_records"
    if "schedule_match" in frame:
        frame.loc[frame.quality_exclusion.isna() & frame.schedule_match.eq("left_only"), "quality_exclusion"] = "unmatched_schedule_game"
    return frame.sort_values(["game_date"] + keys, na_position="last").reset_index(drop=True)


def nba_assignments(raw, schedule, season):
    required = {"game_id", "season", "official_full_name", "official_position", "official_order"}
    if not required.issubset(raw):
        raise ValueError("NBA official schema changed; columns=" + repr(sorted(raw.columns)))
    frame = raw.copy()
    for name in frame:
        if name.endswith("_id"):
            frame[name] = identity(frame[name])
    frame["official_name_source"] = raw.official_full_name.astype("string")
    frame["official_name"] = frame.official_name_source.str.strip().replace("", pd.NA)
    frame["sport"] = "NBA"
    frame["official_identity_type"] = "source_name_only_no_stable_person_id"
    frame["source_season_end_year"] = pd.to_numeric(raw.season, errors="coerce").astype("Int64")
    if not frame.source_season_end_year.eq(season).fillna(False).all():
        raise ValueError("Unexpected NBA officials season")
    keep = ["game_id", "game_date", "source_game_date_precision", "home_team_id", "away_team_id", "venue_id", "venue_full_name", "attendance_actual", "venue_capacity_source", "game_completed_actual"]
    lookup = schedule[keep].drop_duplicates()
    if lookup.game_id.duplicated().any():
        raise ValueError("Ambiguous NBA schedule game key")
    frame = frame.merge(lookup, on="game_id", how="left", validate="many_to_one", indicator="schedule_match")
    frame["game_date_time_known"] = frame.source_game_date_precision.eq("source_timestamp")
    return quarantine(annotate(frame), ["game_id", "official_name"])


def nfl_schedule(source, requested_source_game_ids=None):
    required = {"game_id", "old_game_id", "gameday", "gametime"}
    if not required.issubset(source):
        raise ValueError("NFL schedule schema changed; columns=" + repr(sorted(source.columns)))
    frame = source.copy()
    frame["game_id"] = identity(frame.game_id)
    frame["source_game_id"] = identity(frame.old_game_id)
    if requested_source_game_ids is not None:
        # Unrelated historical placeholders must not invalidate the requested
        # modern officiating panel. Preserve every candidate for requested IDs.
        requested = set(identity(pd.Series(list(requested_source_game_ids), dtype="string")).dropna())
        frame = frame.loc[frame.source_game_id.isin(requested)].copy()
    frame["schedule_game_key"] = identity(get_column(frame, "gsis"))
    times = frame.gametime.astype("string").replace("", pd.NA)
    local = pd.to_datetime(frame.gameday.astype("string") + " " + times.fillna("00:00"), errors="coerce", format="mixed")
    frame["game_date"] = local.dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    frame["game_date_time_known"] = times.notna() & frame.game_date.notna()
    columns = ["source_game_id", "game_id", "schedule_game_key", "game_date", "game_date_time_known", "home_team", "away_team", "stadium_id", "stadium", "roof", "surface", "temp", "wind", "referee"]
    frame = frame[[name for name in columns if name in frame]].drop_duplicates()
    frame = frame.loc[frame.source_game_id.notna()]
    ambiguous = frame.source_game_id.duplicated(keep=False)
    audit = frame.loc[ambiguous].copy()
    audit["mapping_exclusion"] = "conflicting_old_game_id_in_schedule"
    return frame.loc[~ambiguous].reset_index(drop=True), audit.reset_index(drop=True)


def nfl_assignments(raw, lookup, ambiguous_game_ids=()):
    required = {"game_id", "official_id", "official_name", "position", "season"}
    if not required.issubset(raw):
        raise ValueError("NFL official schema changed; columns=" + repr(sorted(raw.columns)))
    frame = raw.copy().rename(columns={"game_id": "source_game_id", "official_name": "official_name_source", "game_key": "source_game_key"})
    for name in ["source_game_id", "source_game_key", "official_id", "jersey_number"]:
        if name in frame:
            frame[name] = identity(frame[name])
    frame["official_name"] = frame.official_name_source.astype("string").str.strip().replace("", pd.NA)
    frame["sport"] = "NFL"
    frame["official_identity_type"] = "source_official_id"
    frame = frame.merge(lookup, on="source_game_id", how="left", validate="many_to_one", indicator="schedule_match")
    frame["source_game_key_agrees"] = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    if "source_game_key" in frame:
        comparable = frame.source_game_key.notna() & frame.schedule_game_key.notna()
        frame.loc[comparable, "source_game_key_agrees"] = frame.loc[comparable, "source_game_key"].eq(frame.loc[comparable, "schedule_game_key"])
    frame = quarantine(annotate(frame), ["source_game_id", "official_id"])
    frame.loc[frame.source_game_key_agrees.eq(False).fillna(False), "quality_exclusion"] = "source_game_key_disagrees_with_schedule"
    frame.loc[frame.source_game_id.isin(set(ambiguous_game_ids)), "quality_exclusion"] = "ambiguous_schedule_game_key"
    return frame


def write(frame, path):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"rows": len(frame), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "schema": {name: str(dtype) for name, dtype in frame.dtypes.items()}, "missing_values": {name: int(count) for name, count in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-season", type=int, default=2015)
    parser.add_argument("--end-season", type=int, default=2026)
    args = parser.parse_args()
    if not 2002 <= args.start_season <= args.end_season <= datetime.now(timezone.utc).year:
        raise ValueError("Invalid officials season range")
    if args.end_season - args.start_season > 25:
        raise ValueError("Season range exceeds request budget")
    out = ROOT / "data/extras/officials_context"
    out.mkdir(parents=True, exist_ok=True)
    remote, tables, coverage, gaps = Remote(), {}, [], []
    for name, url in LICENSES.items():
        (out / name).write_bytes(remote.get(url, 100_000))
    officials_assets = remote.release(NBA_REPO, "espn_nba_officials")
    schedule_assets = remote.release(NBA_REPO, "espn_nba_schedules")
    for season in range(args.start_season, args.end_season + 1):
        official_name, schedule_name = "officials_%s.parquet" % season, "nba_schedule_%s.parquet" % season
        if official_name not in officials_assets or schedule_name not in schedule_assets:
            gaps.append({"sport": "NBA", "season": season, "reason": "required_source_asset_not_published"})
            continue
        raw = remote.table(officials_assets[official_name])
        schedule = normalize_schedule(remote.table(schedule_assets[schedule_name]), season)
        frame = nba_assignments(raw, schedule, season)
        relative = "nba/" + str(season) + "/officials.csv.gz"
        tables[relative] = write(frame, out / relative)
        tables["nba/" + str(season) + "/source_audit.csv.gz"] = write(raw, out / "nba" / str(season) / "source_audit.csv.gz")
        coverage.append({"sport": "NBA", "season": season, "official_rows": len(frame), "games": int(frame.game_id.nunique()), "unmatched_schedule_rows": int(frame.schedule_match.eq("left_only").sum()), "quality_exclusions": int(frame.quality_exclusion.notna().sum()), "date_partitions": frame.evaluation_split.value_counts().to_dict()})
        print(json.dumps(coverage[-1]), flush=True)
    nfl_assets = remote.release(NFL_REPO, "officials")
    if "officials.parquet" not in nfl_assets:
        raise ValueError("NFL official release has no parquet asset")
    raw = remote.table(nfl_assets["officials.parquet"])
    source_years = pd.to_numeric(get_column(raw, "season"), errors="coerce")
    raw = raw.loc[source_years.between(args.start_season, args.end_season)].copy()
    schedule = pd.read_csv(io.BytesIO(remote.get(SCHEDULE_URL)), dtype="string", keep_default_na=False, na_values=[""])
    lookup, mapping_audit = nfl_schedule(schedule, identity(raw.game_id))
    frame = nfl_assignments(raw, lookup, mapping_audit.source_game_id)
    tables["nfl/ambiguous_schedule_mapping_audit.csv.gz"] = write(mapping_audit, out / "nfl/ambiguous_schedule_mapping_audit.csv.gz")
    tables["nfl/source_audit.csv.gz"] = write(raw, out / "nfl/source_audit.csv.gz")
    for season, group in frame.groupby("season", dropna=False):
        relative = "nfl/" + str(int(season)) + "/officials.csv.gz"
        tables[relative] = write(group, out / relative)
        coverage.append({"sport": "NFL", "season": int(season), "official_rows": len(group), "games": int(group.source_game_id.nunique()), "unmatched_schedule_rows": int(group.schedule_match.eq("left_only").sum()), "quality_exclusions": int(group.quality_exclusion.notna().sum()), "date_partitions": group.evaluation_split.value_counts().to_dict()})
        print(json.dumps(coverage[-1]), flush=True)
    present = set(pd.to_numeric(frame.season, errors="coerce").dropna().astype(int))
    for season in range(max(2015, args.start_season), args.end_season + 1):
        if season not in present:
            gaps.append({"sport": "NFL", "season": season, "reason": "source_contains_no_official_rows"})
    summary = {"created_at_utc": now(), "dataset": "nba_nfl_official_assignments", "collection_status": "partial" if gaps else "completed",
               "nfl_ambiguous_schedule_keys": int(mapping_audit.source_game_id.nunique()),
               "source_requests": remote.calls, "source_bytes": remote.bytes, "official_rows": sum(row["official_rows"] for row in coverage), "partitions": coverage,
               "gaps": gaps, "tables": tables, "sources": remote.sources,
               "attribution": "NBA ESPN-derived officials compiled by hoopR/SportsDataverse; NFL officials via nflverse. Both official datasets publish CC BY 4.0. NFL schedule lookup: Lee Sharpe and nflverse/nfldata contributors; no blanket third-party license asserted.",
               "source_schema_reference": {"nba": "https://github.com/sportsdataverse/hoopR-nba-data/blob/4a640c7f04f139c33198b8c22e0f2a5b48fc1d28/R/espn_nba_10_officials_creation.R", "nfl": "https://nflreadr.nflverse.com/reference/load_officials.html"},
               "limitations": ["These are postgame revised assignment snapshots, not verified pregame referee announcements. All automatic model joins remain disabled.",
                   "NBA source contains official names/roles/orders, not stable official person IDs; similarly named people are not merged across games by a fabricated identity.",
                   "NFL source game_id is its numeric legacy game ID; canonical nflverse game ID is obtained only by exact old_game_id schedule mapping. Independent game_key conflicts are quarantined.",
                   "Attendance and observed NFL weather remain retrospective fields; venue capacity and crew membership can be revised. Existing schedule context is repeated only to make new officiating rows interpretable.",
                   "Missing seasons, officials, names or game mappings are explicit gaps, not zero officiating influence. No causal referee effects or betting-return claims are made.",
                   "NBA season is an ending year; NFL season spans January games of the next calendar year. Split assignment follows game date, not source season."]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "schema.json").write_text(json.dumps({"null_token": r"\N", "identifiers": "Source game/official/team/venue identifiers are strings; NBA names are not stable person IDs", "timestamps": "game_date in UTC; source publication time unknown", "tables": tables}, indent=2))
    (out / "source_manifest.json").write_text(json.dumps({"sources": remote.sources, "attribution": summary["attribution"], "changes": "Exact-key schedule joins, identifier normalization, quality quarantine and chronological partitions"}, indent=2))
    print(json.dumps({key: summary[key] for key in ["collection_status", "official_rows", "source_requests", "source_bytes"]}), flush=True)


if __name__ == "__main__":
    main()
