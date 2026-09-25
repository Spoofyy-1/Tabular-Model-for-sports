#!/usr/bin/env python3
"""Cloud-only, season-partitioned NFL play context as compressed CSV.

Real source data is downloaded/read/written only on GitHub-hosted Actions.
--self-test uses tiny synthetic in-memory frames, with no network or data files.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://github.com/nflverse/nflverse-data/releases/download/"
NULL = r"\N"
ALIASES = {"LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV", "JAC": "JAX", "WSH": "WAS", "AZ": "ARI"}
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_DISK_BYTES = 512 * 1024 * 1024
SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
LICENSE_URLS = {
    "CC-BY-4.0": "https://raw.githubusercontent.com/nflverse/nflverse-data/main/LICENSE.md",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/legalcode.txt",
}
PBP_COLUMNS = [
    "game_id", "old_game_id", "play_id", "season", "season_type", "week", "game_date",
    "posteam", "defteam", "home_team", "away_team", "posteam_type", "drive", "fixed_drive",
    "play_type", "down", "ydstogo", "yardline_100", "game_seconds_remaining", "half_seconds_remaining", "qtr",
    "shotgun", "no_huddle", "pass_attempt", "rush_attempt", "qb_dropback", "qb_scramble", "qb_kneel", "qb_spike",
    "sack", "qb_hit", "complete_pass", "interception", "fumble_lost", "touchdown", "pass_touchdown", "rush_touchdown",
    "air_yards", "yards_after_catch", "yards_gained", "passing_yards", "rushing_yards", "receiving_yards",
    "score_differential", "posteam_timeouts_remaining", "defteam_timeouts_remaining", "pass_length", "pass_location",
    "run_location", "run_gap", "penalty", "penalty_yards", "no_play", "first_down", "third_down_converted", "fourth_down_converted",
    "passer_player_id", "passer_player_name", "rusher_player_id", "rusher_player_name", "receiver_player_id", "receiver_player_name",
    "lateral_rusher_player_id", "lateral_receiver_player_id", "lateral_sack_player_id", "lateral_interception_player_id",
    "fumbled_1_player_id", "fumbled_2_player_id", "sack_player_id", "half_sack_1_player_id", "half_sack_2_player_id",
]
DOCS = {
    "pbp": "https://nflreadr.nflverse.com/reference/load_pbp.html",
    "participation": "https://nflreadr.nflverse.com/reference/load_participation.html",
    "ftn_charting": "https://nflreadr.nflverse.com/reference/load_ftn_charting.html",
}
DICTIONARIES = {name: "https://nflreadr.nflverse.com/articles/dictionary_%s.html" % name for name in DOCS}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def require_hosted_runner():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise SystemExit("NFL play collection is restricted to GitHub-hosted Actions; local data download, reads and writes are prohibited.")
    if not os.environ.get("GITHUB_WORKSPACE") or not Path(os.environ["GITHUB_WORKSPACE"]).is_dir() or not os.environ.get("RUNNER_TEMP"):
        raise SystemExit("A valid hosted workspace and RUNNER_TEMP are required.")


def download(url, path, budget, optional=False):
    require_hosted_runner()
    for attempt in range(4):
        partial = path.with_suffix(path.suffix + ".part")
        try:
            with requests.get(url, stream=True, timeout=(15, 120)) as response:
                if response.status_code == 404 and optional:
                    return None, {"url": url, "status": "not_available", "checked_at_utc": utc_now()}
                response.raise_for_status()
                sha, size = hashlib.sha256(), 0
                with partial.open("wb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        budget[0] += len(chunk)
                        if size > MAX_FILE_BYTES or budget[0] > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("NFL play download budget exceeded")
                        sha.update(chunk)
                        output.write(chunk)
                partial.replace(path)
                return path, {"url": url, "status": "downloaded", "bytes": size, "sha256": sha.hexdigest(),
                              "retrieved_at_utc": utc_now(), "http_last_modified": response.headers.get("Last-Modified")}
        except requests.RequestException:
            partial.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(1 + attempt * 2)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    raise RuntimeError("Download exhausted")


def make_calendar(source):
    frame = source[["game_id", "gameday", "gametime"]].copy()
    frame["game_id"] = frame["game_id"].astype("string")
    local = pd.to_datetime(frame["gameday"] + " " + frame["gametime"].fillna("00:00"), errors="coerce")
    frame["game_date"] = local.dt.tz_localize("America/New_York", ambiguous="raise", nonexistent="raise").dt.tz_convert("UTC")
    frame["game_date_time_known"] = frame["gametime"].notna() & local.notna()
    if frame["game_id"].duplicated().any():
        raise ValueError("Schedule game IDs must be unique")
    return frame[["game_id", "game_date", "game_date_time_known"]]


def identity_string(series):
    if pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        if (numeric.notna() & numeric.mod(1).ne(0)).any():
            raise ValueError("A numeric identifier contains a non-integer value")
        return numeric.astype("Int64").astype("string")
    return series.astype("string")


def nested_json(value):
    if isinstance(value, np.ndarray):
        value = value.tolist()
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False) if isinstance(value, (list, tuple, dict)) else value


def normalize(source, category, season, calendar):
    """Pure transformation; all local tests supply synthetic source frames."""
    frame = source.copy()
    source_columns = list(frame.columns)
    if category == "pbp":
        game_col, play_col = "game_id", "play_id"
    else:
        game_col = "nflverse_game_id"
        play_col = "nflverse_play_id" if category == "ftn_charting" else "play_id"
    if not {game_col, play_col}.issubset(frame.columns):
        raise ValueError("Missing %s game/play identity fields; columns=%s" % (category, sorted(frame.columns)))
    for col in frame.select_dtypes(include=["object"]).columns:
        frame[col] = frame[col].map(nested_json)
    for col in frame.columns:
        if col.endswith("_id"):
            frame[col] = identity_string(frame[col])
    frame["game_id_source"] = frame[game_col]
    frame["play_id_source"] = frame[play_col]
    frame["game_id"] = frame[game_col].astype("string")
    numeric_play = pd.to_numeric(frame[play_col], errors="coerce")
    good_play = numeric_play.notna() & numeric_play.ge(0) & numeric_play.mod(1).eq(0)
    frame["play_id"] = numeric_play.where(good_play).astype("Int64").astype("string")
    if "season" in frame and not pd.to_numeric(frame["season"], errors="coerce").eq(season).all():
        raise ValueError("Unexpected season in %s source file" % category)
    frame["season"] = season
    for col in ["posteam", "defteam", "home_team", "away_team", "possession_team"]:
        if col in frame:
            frame[col + "_source"] = frame[col].astype("string")
            frame[col] = frame[col].astype("string").replace(ALIASES)
    if "game_date" in frame:
        frame["game_date_source"] = frame.pop("game_date")
    frame = frame.merge(calendar, on="game_id", how="left", validate="many_to_one")
    frame["split_by_game_date"] = "unmatched_game_date"
    frame.loc[frame["game_date"].lt(pd.Timestamp("2025-01-01", tz="UTC")), "split_by_game_date"] = "development_through_2024"
    frame.loc[frame["game_date"].ge(pd.Timestamp("2025-01-01", tz="UTC")), "split_by_game_date"] = "holdout_2025_onward"
    frame["verified_asof"] = False
    frame["pregame_feature_enabled"] = False
    frame["source_license"] = "CC-BY-4.0" if category == "pbp" else "CC-BY-SA-4.0"
    frame["source_record_observed_at_utc"] = pd.to_datetime(frame["date_pulled"], utc=True, errors="coerce") if "date_pulled" in frame else pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    frame["availability_class"] = (
        "after_entire_season_ends_not_available_during_season" if category == "participation" and season >= 2023 else
        "charted_postgame_latest_revised_snapshot" if category == "ftn_charting" else "postgame_latest_revised_snapshot"
    )
    frame["exclusion_reason"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    valid_game = frame["game_id"].str.fullmatch(r"\d{4}_\d{2}_[A-Z0-9]+_[A-Z0-9]+", na=False)
    frame.loc[~valid_game | frame["play_id"].isna(), "exclusion_reason"] = "missing_or_invalid_game_play_key"
    before = len(frame)
    frame = frame.drop_duplicates().reset_index(drop=True)
    candidate = frame["exclusion_reason"].isna()
    conflict = frame.loc[candidate].duplicated(["game_id", "play_id"], keep=False)
    frame.loc[conflict.index[conflict], "exclusion_reason"] = "conflicting_game_play_key"
    frame = frame.sort_values(["game_date", "game_id", "play_id"], na_position="last", kind="stable",
                              key=lambda s: pd.to_numeric(s, errors="coerce") if s.name == "play_id" else s).reset_index(drop=True)
    canonical = frame.loc[frame["exclusion_reason"].isna()].copy()
    excluded = frame.loc[frame["exclusion_reason"].notna()].copy()
    metadata = {
        "source_columns": source_columns, "source_rows": before, "exact_duplicate_rows_removed": before - len(frame),
        "canonical_rows": len(canonical), "excluded_rows": len(excluded),
        "exclusion_counts": {str(k): int(v) for k, v in excluded["exclusion_reason"].value_counts().items()},
        "rows_without_calendar_match": int(canonical["game_date"].isna().sum()),
    }
    return canonical, excluded, metadata


def player_roles(plays):
    """Explicit event actors only; participation/route running is not inferred."""
    roles = []
    invalid = {}
    for role in ["passer", "rusher", "receiver"]:
        col = role + "_player_id"
        if col not in plays:
            continue
        values = plays[col].astype("string")
        valid = values.str.fullmatch(r"00-\d{7}", na=False)
        invalid[role] = int((values.notna() & ~valid).sum())
        selected = plays.loc[valid, ["game_id", "play_id", "season", "game_date", "split_by_game_date"]].copy()
        selected["player_id"] = values.loc[valid]
        selected["role"] = role
        selected["verified_asof"] = False
        selected["pregame_feature_enabled"] = False
        roles.append(selected)
    if not roles:
        return pd.DataFrame(columns=["game_id", "play_id", "season", "game_date", "split_by_game_date", "player_id", "role", "verified_asof", "pregame_feature_enabled"]), invalid
    return pd.concat(roles, ignore_index=True).sort_values(["game_date", "game_id", "play_id", "role", "player_id"],
                key=lambda s: pd.to_numeric(s, errors="coerce") if s.name == "play_id" else s).reset_index(drop=True), invalid


def schema_for(frame, category):
    fields = {}
    for col in frame:
        is_id = col.endswith("_id") or col in ["game_id_source", "play_id_source"]
        utc = isinstance(frame[col].dtype, pd.DatetimeTZDtype)
        fields[col] = {"pandas_dtype": str(frame[col].dtype), "read_as_string": is_id or str(frame[col].dtype) in ("object", "string"),
                       "datetime_timezone": "UTC" if utc else None, "null_rows": int(frame[col].isna().sum()), "verified_asof": False}
    return {"format": "csv.gz", "encoding": "UTF-8", "delimiter": ",", "header": True,
            "null_sentinel": NULL, "empty_string_is_null": False, "bool_encoding": ["True", "False"],
            "timestamp_encoding": "UTC ISO8601 with numeric offset", "keep_default_na": False,
            "nested_values": "Lists/dictionaries are JSON strings when present; source delimited player lists remain unchanged.",
            "dictionary_url": DICTIONARIES.get(category, DICTIONARIES["pbp"]), "fields": fields}


def write_csv(frame, category, season, out, relative):
    require_hosted_runner()
    destination = out / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Sentinel collisions must fail; a literal source string must not become null.
    for col in frame.select_dtypes(include=["object", "string"]).columns:
        if frame[col].eq(NULL).fillna(False).any():
            raise ValueError("CSV null sentinel collides with literal source string in " + col)
    with gzip.open(destination, "wt", encoding="utf-8", newline="") as handle:
        frame.to_csv(handle, index=False, na_rep=NULL, chunksize=10000)
    schema_path = destination.with_name("schema.json")
    schema_path.write_text(json.dumps(schema_for(frame, category), indent=2))
    return {"file": str(relative), "schema": str(schema_path.relative_to(out)), "rows": len(frame),
            "columns": len(frame.columns), "season": season, "bytes": destination.stat().st_size,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "verified_asof": False,
            "rows_by_split": {str(k): int(v) for k, v in frame["split_by_game_date"].value_counts().items()},
            "distinct_games": int(frame["game_id"].nunique())}


def self_test():
    """Synthetic values only; does not invoke download/read/write functions."""
    import unittest
    class Tests(unittest.TestCase):
        def calendar(self):
            return make_calendar(pd.DataFrame({"game_id": ["2024_18_SEA_LA", "2025_01_SEA_LA"], "gameday": ["2025-01-05", "2025-09-07"], "gametime": ["13:00", "13:00"]}))

        def source(self):
            return pd.DataFrame({"game_id": ["2024_18_SEA_LA"], "play_id": [41.0], "season": [2024], "posteam": ["LA"], "passer_player_id": ["00-1234567"], "receiver_player_id": [None], "air_yards": [None]})

        def test_calendar_split_ids_missing_and_actors(self):
            frame, excluded, meta = normalize(self.source(), "pbp", 2024, self.calendar())
            self.assertEqual(frame.loc[0, "split_by_game_date"], "holdout_2025_onward")
            self.assertEqual(frame.loc[0, "play_id"], "41")
            self.assertEqual(frame.loc[0, "posteam"], "LAR")
            self.assertTrue(pd.isna(frame.loc[0, "air_yards"]))
            self.assertFalse(frame["verified_asof"].any())
            roles, _ = player_roles(frame)
            self.assertEqual(roles["role"].tolist(), ["passer"])
            self.assertEqual(len(excluded), 0)

        def test_participation_season_end_and_nested_values(self):
            source = pd.DataFrame({"nflverse_game_id": ["2025_01_SEA_LA"], "play_id": ["41"], "offense_players": [["00-1234567"]], "was_pressure": [None]})
            frame, _, _ = normalize(source, "participation", 2025, self.calendar())
            self.assertEqual(frame.loc[0, "availability_class"], "after_entire_season_ends_not_available_during_season")
            self.assertEqual(json.loads(frame.loc[0, "offense_players"]), ["00-1234567"])
            self.assertTrue(pd.isna(frame.loc[0, "was_pressure"]))

        def test_conflicts_are_quarantined_exact_duplicates_removed(self):
            same = self.source()
            duplicate = pd.concat([same, same], ignore_index=True)
            frame, _, meta = normalize(duplicate, "pbp", 2024, self.calendar())
            self.assertEqual(meta["exact_duplicate_rows_removed"], 1)
            changed = same.copy()
            changed["air_yards"] = 12
            frame, excluded, _ = normalize(pd.concat([same, changed], ignore_index=True), "pbp", 2024, self.calendar())
            self.assertEqual((len(frame), len(excluded)), (0, 2))

        def test_ftn_timestamp_does_not_establish_historical_availability(self):
            source = pd.DataFrame({"nflverse_game_id": ["2025_01_SEA_LA"], "nflverse_play_id": [41], "date_pulled": ["2026-09-25T00:00:00Z"]})
            frame, _, _ = normalize(source, "ftn_charting", 2025, self.calendar())
            self.assertEqual(str(frame["source_record_observed_at_utc"].dtype), "datetime64[ns, UTC]")
            self.assertFalse(frame.loc[0, "verified_asof"])
            self.assertEqual(frame.loc[0, "source_license"], "CC-BY-SA-4.0")

        def test_csv_preserves_ids_nulls_and_literal_na(self):
            frame = pd.DataFrame({"player_id": pd.Series(["00-1234567", "001"], dtype="string"), "text": ["NA", ""], "measurement": [None, 2]})
            encoded = frame.to_csv(index=False, na_rep=NULL)
            recovered = pd.read_csv(io.StringIO(encoded), dtype={"player_id": "string"}, keep_default_na=False, na_values=[NULL])
            self.assertEqual(recovered["player_id"].tolist(), ["00-1234567", "001"])
            self.assertEqual(recovered["text"].tolist(), ["NA", ""])
            self.assertTrue(pd.isna(recovered.loc[0, "measurement"]))

        def test_season_mismatch_rejected(self):
            with self.assertRaises(ValueError):
                normalize(self.source(), "pbp", 2025, self.calendar())
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    if not result.wasSuccessful():
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-season", type=int, default=1999)
    parser.add_argument("--end-season", type=int, default=2026)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "enrichment" / "nfl_plays")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    require_hosted_runner()
    import pyarrow.parquet as pq
    if not 1999 <= args.start_season <= args.end_season <= datetime.now(timezone.utc).year:
        raise SystemExit("Require 1999 <= start <= end <= current year")
    out, cache = args.output_dir, Path(os.environ["RUNNER_TEMP"]) / "nfl-play-enrichment"
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    budget, downloads, partitions, quality = [0], [], {}, []
    for name, url in LICENSE_URLS.items():
        _, meta = download(url, out / (name + ".txt"), budget)
        downloads.append(dict(meta, category="license", license=name))
    path, meta = download(SCHEDULE_URL, cache / "games.csv", budget)
    downloads.append(dict(meta, category="calendar_lookup"))
    calendar = make_calendar(pd.read_csv(path, low_memory=False))
    path.unlink()
    for season in range(args.start_season, args.end_season + 1):
        jobs = [("pbp", "pbp/play_by_play_%d.parquet" % season)]
        if season >= 2016:
            jobs.append(("participation", "pbp_participation/pbp_participation_%d.parquet" % season))
        if season >= 2022:
            jobs.append(("ftn_charting", "ftn_charting/ftn_charting_%d.parquet" % season))
        for category, suffix in jobs:
            path, meta = download(BASE + suffix, cache / (category + ".parquet"), budget, optional=category != "pbp")
            downloads.append(dict(meta, category=category, season=season))
            if path is None:
                continue
            require_hosted_runner()
            schema_names = pq.ParquetFile(path).schema_arrow.names
            if category == "pbp":
                requested = [col for col in PBP_COLUMNS if col in schema_names]
                source = pd.read_parquet(path, columns=requested)
            else:
                source = pd.read_parquet(path)
            frame, excluded, audit = normalize(source, category, season, calendar)
            audit.update(category=category, season=season, missing_selected_source_columns=[c for c in PBP_COLUMNS if c not in schema_names] if category == "pbp" else [])
            quality.append(audit)
            relative = Path(category) / ("season=%d" % season) / "data.csv.gz"
            info = write_csv(frame, category, season, out, relative)
            partitions.setdefault(category, []).append(info)
            if len(excluded):
                info_excluded = write_csv(excluded, category, season, out, Path(category + "_excluded") / ("season=%d" % season) / "data.csv.gz")
                partitions.setdefault(category + "_excluded", []).append(info_excluded)
            if category == "pbp":
                actors, invalid = player_roles(frame)
                audit["invalid_nonnull_actor_ids_by_role"] = invalid
                actors_info = write_csv(actors, "pbp", season, out, Path("player_play_roles") / ("season=%d" % season) / "data.csv.gz")
                partitions.setdefault("player_play_roles", []).append(actors_info)
            path.unlink()
            print(json.dumps({"category": category, "season": season, "rows": len(frame), "excluded_rows": len(excluded)}), flush=True)
            del source, frame, excluded
            if sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) > MAX_DISK_BYTES:
                raise RuntimeError("NFL play output disk budget exceeded")
    manifest = {
        "created_at_utc": utc_now(), "cloud_only": True, "requested_seasons": [args.start_season, args.end_season],
        "downloads": downloads, "partitions": partitions, "quality": quality,
        "source_documentation": DOCS, "source_dictionaries": DICTIONARIES,
        "licenses": {
            "pbp_and_player_play_roles": {"license": "CC-BY-4.0", "attribution": "nflverse contributors; play-by-play prepared by nflfastR; underlying NFL data owners.", "license_url": LICENSE_URLS["CC-BY-4.0"]},
            "participation": {"license": "CC-BY-SA-4.0", "attribution": "NFL NextGenStats via nflverse for 2016–2022; FTN Data via nflverse from 2023 onward.", "license_url": LICENSE_URLS["CC-BY-SA-4.0"]},
            "ftn_charting": {"license": "CC-BY-SA-4.0", "attribution": "FTN Data via nflverse (ftndata.com via nflverse).", "license_url": LICENSE_URLS["CC-BY-SA-4.0"]},
            "calendar_lookup": {"attribution": "Lee Sharpe and nflverse/nfldata schedule maintainers.", "notice": "No blanket third-party license asserted for the schedule lookup; upstream rights and terms remain applicable."},
        },
        "adaptations_notice": "Participation and FTN charting CSV adaptations, including normalized identifiers, schedule annotations, exclusion partitions and schemas, are released under CC-BY-SA-4.0. Retain attribution, license and changes notice when redistributing. Base PBP and role tables remain separately identified as CC-BY-4.0.",
        "terms_notice": "Upstream data are supplied without warranty. NFL/source-provider rights and terms continue to apply; source licenses extend only to rights the licensors can grant. See https://nflverse.nflverse.com/#terms-of-use.",
        "changes": ["Selected bounded PBP context columns; retained all published participation and FTN charting columns.", "Serialized nested values as JSON; identifiers are CSV strings; missing values use explicit \\N sentinel.", "Normalized franchise aliases while retaining original team and play/game identifiers.", "Joined game calendar by exact game ID; dates are UTC and missing kickoff precision is flagged.", "Removed exact duplicate rows and quarantined conflicting or invalid game/play keys.", "Separated explicit passer/rusher/receiver event actors into a role table; no routes or participation inferred from targets.", "Added availability, license and calendar split annotations; no new table enters model training."],
        "caveats": ["All observations describe plays that already occurred; target-game context would leak outcomes into a pregame model.", "2023+ participation is published only after the complete season/postseason ends. It was not available for earlier games within that season.", "FTN charting is described as charted within48hours postgame, but that is not a verified historical publication timestamp. date_pulled reflects source retrieval and may be revised.", "Files are current revised snapshots. Historical availability and revision histories are unverified even when using lagged games.", "2022 FTN read_thrown differs: missing primary-read codes must not be imputed as other reads; the primary-read0 code begins2023.", "Participation ngs_air_yards is absent from2024 onward; preserve nulls and use the separate PBP air_yards definition where appropriate.", "Participation players identify who was on the field, not one-to-one defensive assignments. A primary receiver route is not every receiver's route.", "PBP includes administrative/no-play events. Use explicit play_type/no_play/attempt fields to define denominators.", "Development uses game dates before2025-01-01 UTC; 2024-season postseason games in2025 are held out. No training or ROI assertion is made.", "Current-season coverage is incomplete; missing source seasons, column gaps, excluded keys and unmatched schedules are reported."],
    }
    summary = {"created_at_utc": manifest["created_at_utc"], "requested_seasons": manifest["requested_seasons"],
               "format": "csv.gz", "raw_download_bytes": budget[0], "verified_asof": False, "model_changes": False,
               "tables": {name: {"rows": sum(p["rows"] for p in files), "bytes": sum(p["bytes"] for p in files), "seasons": [p["season"] for p in files], "partitions": files} for name, files in partitions.items()},
               "missing_sources": [m for m in downloads if m["status"] != "downloaded"],
               "quality": quality}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "enrichment_summary.json").write_text(json.dumps(summary, indent=2))
    (out / "ATTRIBUTION_AND_LICENSES.txt").write_text(manifest["adaptations_notice"] + "\n\n" + json.dumps(manifest["licenses"], indent=2) + "\n\n" + manifest["terms_notice"] + "\n")
    print(json.dumps({"summary": str(out / "enrichment_summary.json"), "rows_by_table": {name: d["rows"] for name, d in summary["tables"].items()}, "missing_sources": len(summary["missing_sources"])}), flush=True)


if __name__ == "__main__":
    main()
