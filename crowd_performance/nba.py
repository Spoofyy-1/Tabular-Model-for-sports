"""Hosted-only descriptive NBA attendance/player-outcome joins; no training."""
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner
from context.nba_context import normalize_schedule, split

RELEASE_ROOT = "https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/"
META_URL = RELEASE_ROOT + "context-36094215424-1/nba_context_summary.json"
META_HASH = "83bc304e188265c7d74517b73f9cee2706260020c578e141715ac10f8eab0f14"
META_BYTES = 219867
BASE_URL = RELEASE_ROOT + "csv-36170200182-1/csv-base-nba.tar.gz"
BASE_HASH = "5642335016aca6e0d39d7cb1c7c9f74b989de74dd2fcb2217fed4046e1bef3b7"
BASE_BYTES = 103971052
MAX_SOURCE_BYTES = 120_000_000
SCHEDULE_BYTES = 2979582
NULL = r"\N"
STATS = ["points", "rebounds", "assists", "threes", "steals", "blocks", "turnovers", "minutes", "field_goals_attempted", "free_throws_attempted"]
PLAYER_COLUMNS = ["game_id", "player_id", "team_id", "opponent_id", "player_name", "game_date", "game_local_date", "season", "season_type", "is_home", "did_not_play", "boxscore_observed"] + STATS
GAME_COLUMNS = ["game_id", "game_date", "game_local_date", "season", "season_type", "home_team_id", "away_team_id"]
GROUPS = ["evaluation_split", "season", "season_type", "is_home", "neutral_site", "player_id", "reported_attendance_band"]
GAME_GROUPS = ["evaluation_split", "season", "season_type", "neutral_site", "reported_attendance_band"]
MIN_PLAYER_JOIN_SHARE = 0.90
UA = "SportsPropsCrowdResearch/1.0 (https://github.com/kennynakao/Tabular-Model-for-sports)"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


class Remote:
    """Strict byte accounting and trusted, bounded redirects; no retries."""
    def __init__(self, cache):
        require_github_hosted_runner()
        if not cache.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
            raise RuntimeError("source_cache_outside_runner_temp")
        self.cache, self.bytes, self.http_requests, self.sources = cache, 0, 0, []

    def get(self, url, filename, size, expected_hash):
        require_github_hosted_runner()
        if Path(filename).name != filename or not 0 < size <= MAX_SOURCE_BYTES - self.bytes:
            raise ValueError("invalid_source_size_or_cache_name")
        original, path = url, self.cache / filename
        for hop in range(4):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"} or parsed.port not in (None, 443) or parsed.username or parsed.password:
                raise ValueError("untrusted_source_destination")
            if self.http_requests >= 108:
                raise ValueError("http_request_budget_exceeded")
            self.http_requests += 1
            with requests.get(url, stream=True, timeout=(10, 90), allow_redirects=False, headers={"User-Agent": UA}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if hop == 3 or not response.headers.get("Location"):
                        raise ValueError("source_redirect_bound_exceeded")
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status_code != 200:
                    raise ValueError("source_http_status_" + str(response.status_code))
                received, sha = 0, hashlib.sha256()
                with path.open("wb") as target:
                    for block in response.iter_content(65536):
                        received += len(block)
                        self.bytes += len(block)
                        if received > size or self.bytes > MAX_SOURCE_BYTES:
                            raise ValueError("source_byte_budget_exceeded")
                        target.write(block)
                        sha.update(block)
                if received != size or sha.hexdigest() != expected_hash:
                    raise ValueError("source_hash_or_size_mismatch")
                self.sources.append({"source_url": original, "bytes": received, "sha256": expected_hash,
                    "retrieved_at_utc": now(), "http_last_modified": response.headers.get("Last-Modified")})
                return path
        raise ValueError("source_redirect_bound_exceeded")


def schedule_pins(metadata):
    """Read only the exact previously collected schedule asset inventory."""
    entries = [item for item in metadata["source_assets"] if item.get("dataset") == "schedules"]
    if len(entries) != 25 or sorted(item["season"] for item in entries) != list(range(2002, 2027)):
        raise ValueError("schedule_season_inventory_mismatch")
    if sum(item["bytes"] for item in entries) != SCHEDULE_BYTES:
        raise ValueError("schedule_source_size_inventory_mismatch")
    for item in entries:
        expected = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_" + str(item["season"]) + ".parquet"
        if item["source_url"] != expected or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) or not 0 < item["bytes"] <= 1_000_000:
            raise ValueError("invalid_schedule_source_pin")
    return sorted(entries, key=lambda item: item["season"])


def read_member(archive, name, bound):
    member = archive.getmember(name)
    if not member.isfile() or not 0 <= member.size <= bound:
        raise ValueError("archive_member_contract_failure")
    value = archive.extractfile(member).read(bound + 1)
    if len(value) != member.size:
        raise ValueError("archive_member_size_mismatch")
    return value


def validate_csv_contract(compressed, schema, declared, columns):
    if len(compressed) != declared["bytes"] or digest(compressed) != declared["sha256"] or schema.get("null_token") != NULL:
        raise ValueError("csv_integrity_or_null_contract_failure")
    names = [item["name"] for item in schema["columns"]]
    if len(names) != len(set(names)) or len(names) != declared["columns"] or schema["rows"] != declared["rows"] or not set(columns).issubset(names):
        raise ValueError("csv_declared_schema_mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
        with io.TextIOWrapper(source, encoding="utf-8", newline="") as text:
            header = next(csv.reader(text))
    if header != names:
        raise ValueError("csv_header_schema_mismatch")
    return names


def load_base(path, output):
    require_github_hosted_runner()
    frames, records = {}, []
    with tarfile.open(path, "r:gz") as archive:
        seen = set()
        for member in archive.getmembers():
            parts = PurePosixPath(member.name)
            if parts.is_absolute() or ".." in parts.parts or "\\" in member.name or member.name in seen or not (member.isfile() or member.isdir()):
                raise ValueError("unsafe_or_duplicate_archive_member")
            seen.add(member.name)
        manifest = json.loads(read_member(archive, "csv_manifest.json", 3_000_000))
        if manifest.get("dataset") != "base-nba" or manifest.get("null_token") != NULL:
            raise ValueError("unexpected_base_csv_manifest")
        for name, columns, bound in [("games", GAME_COLUMNS, 2_000_000), ("player_games", PLAYER_COLUMNS, 40_000_000)]:
            member = "data/nba/" + name + ".csv.gz"
            declared = [item for item in manifest["tables"] if item["file"] == member]
            if len(declared) != 1:
                raise ValueError("ambiguous_csv_manifest_table")
            declared = declared[0]
            schema = json.loads(read_member(archive, "data/nba/" + name + ".schema.json", 100_000))
            if schema.get("source_member") != "data/nba/" + name + ".parquet":
                raise ValueError("unexpected_csv_source_member")
            validate_timestamp_schema(schema)
            compressed = read_member(archive, member, bound)
            validate_csv_contract(compressed, schema, declared, columns)
            frame = pd.read_csv(io.BytesIO(compressed), compression="gzip", usecols=columns, dtype="string", keep_default_na=False, na_values=[NULL])
            if len(frame) != declared["rows"]:
                raise ValueError("csv_row_count_mismatch")
            frames[name] = frame
            records.append({**declared, "source_schema": schema, "selected_columns": columns})
        quality = json.loads(read_member(archive, "data/nba/quality_report.json", 10_000_000))
        for name in ["hoopR-nba-data-LICENSE.txt", "sportsdataverse-data-LICENSE.txt", "source_manifest.json", "DATA_DICTIONARY.md"]:
            content = read_member(archive, "data/nba/" + name, 10_000_000)
            (output / ("SOURCE_" + name)).write_bytes(content)
    return frames["games"], frames["player_games"], quality.get("flagged_game_ids", []), records


def ids(values):
    text = values.astype("string")
    return text.where(text.str.fullmatch(r"[1-9][0-9]*", na=False))


def boolean(values):
    return values.astype("string").str.lower().map({"true": True, "false": False, "1": True, "0": False}).astype("boolean")


def validate_timestamp_schema(schema):
    columns = [item for item in schema["columns"] if item["name"] == "game_date"]
    declared = str(columns[0].get("arrow_type", "")).replace(" ", "") if len(columns) == 1 else ""
    if not declared.startswith("timestamp[") or "tz=UTC" not in declared:
        raise ValueError("base_utc_timestamp_schema_unverified")


def calendar_dates(values):
    """Retain the provider calendar date; never shift it through UTC."""
    text = values.astype("string").str.extract(r"^(\d{4}-\d{2}-\d{2})(?:$|[T ])", expand=False)
    return pd.to_datetime(text, format="%Y-%m-%d", errors="coerce").dt.strftime("%Y-%m-%d").astype("string")


def add_reason(frame, mask, reason):
    selected = mask.fillna(False)
    current = frame.loc[selected, "exclusion_reasons"]
    frame.loc[selected, "exclusion_reasons"] = current + current.ne("").map({True: "|", False: ""}) + reason


def attendance_context(schedule):
    out = schedule.copy()
    attendance = pd.to_numeric(out.attendance_actual, errors="coerce").astype("Float64")
    capacity = pd.to_numeric(out.venue_capacity_source, errors="coerce").astype("Float64")
    out["attendance_actual"], out["venue_capacity_source"] = attendance, capacity
    finite_att = pd.Series(np.isfinite(attendance), index=out.index).fillna(False)
    finite_capacity = pd.Series(np.isfinite(capacity), index=out.index).fillna(False)
    out["attendance_missing"] = attendance.isna()
    out["attendance_nonfinite_or_negative"] = (attendance.notna() & (~finite_att | attendance.lt(0))).fillna(False)
    out["attendance_fractional_count"] = attendance.where(finite_att).mod(1).ne(0).fillna(False)
    out["attendance_zero_unverified"] = attendance.eq(0).fillna(False)
    out["capacity_missing"] = capacity.isna()
    out["capacity_nonfinite_or_nonpositive"] = (capacity.notna() & (~finite_capacity | capacity.le(0))).fillna(False)
    out["capacity_fractional_count"] = capacity.where(finite_capacity).mod(1).ne(0).fillna(False)
    denominator = capacity.where(finite_capacity & capacity.gt(0) & ~out.capacity_fractional_count)
    ratio = (attendance.where(finite_att & attendance.ge(0) & ~out.attendance_fractional_count) / denominator).astype("Float64")
    out["reported_attendance_capacity_ratio"] = ratio.where(np.isfinite(ratio))
    out["reported_ratio_exceeds_one"] = out.reported_attendance_capacity_ratio.gt(1)
    band = pd.Series("attendance_missing", index=out.index, dtype="string")
    band.loc[out.attendance_nonfinite_or_negative | out.attendance_fractional_count] = "invalid_reported_attendance"
    band.loc[out.attendance_zero_unverified] = "zero_reported_unverified"
    valid = finite_att & attendance.gt(0) & ~out.attendance_fractional_count
    for lower, upper, name in [(0, 10000, "positive_below_10000"), (10000, 15000, "10000_to_14999"), (15000, 20000, "15000_to_19999"), (20000, float("inf"), "20000_or_more")]:
        band.loc[valid & attendance.ge(lower) & attendance.lt(upper)] = name
    out["reported_attendance_band"] = band
    out["historical_capacity_validity_verified"] = False
    out["actual_occupancy_verified"] = False
    out["crowd_noise_measured"] = False
    out["automatic_training_join_allowed"] = False
    out["same_game_retrospective_only"] = True
    return out


def validate_game_links(schedule, base_games):
    """Quarantine whole duplicated games before any one-to-one identity join."""
    out = attendance_context(schedule).reset_index(drop=True)
    out["source_schedule_row"] = np.arange(1, len(out) + 1)
    out["exclusion_reasons"] = pd.Series("", index=out.index, dtype="string")
    for key in ["game_id", "home_team_id", "away_team_id"]:
        out[key] = ids(out[key])
    duplicate = out.game_id.notna() & out.game_id.duplicated(keep=False)
    add_reason(out, duplicate, "duplicate_schedule_game")
    add_reason(out, out[["game_id", "home_team_id", "away_team_id", "game_date"]].isna().any(axis=1), "missing_schedule_identity")
    add_reason(out, out.home_team_id.eq(out.away_team_id), "same_schedule_opponents")
    add_reason(out, out.source_game_date_precision.ne("source_timestamp"), "schedule_time_precision_unresolved")

    base = base_games.copy().reset_index(drop=True)
    base["exclusion_reasons"] = pd.Series("", index=base.index, dtype="string")
    for key in ["game_id", "home_team_id", "away_team_id"]:
        base[key] = ids(base[key])
    base["game_date"] = pd.to_datetime(base.game_date, utc=True, errors="coerce", format="mixed")
    base["provider_calendar_date"] = calendar_dates(base.game_local_date)
    base["timestamp_precision_independently_verified"] = False
    out["provider_calendar_date"] = calendar_dates(out.game_local_date)
    for key in ["season", "season_type"]:
        base[key] = pd.to_numeric(base[key], errors="coerce").astype("Int64")
    add_reason(base, base.game_id.notna() & base.game_id.duplicated(keep=False), "duplicate_base_game")
    add_reason(base, base[["game_id", "game_date", "home_team_id", "away_team_id", "season", "season_type"]].isna().any(axis=1), "missing_base_game_identity")
    add_reason(base, base.home_team_id.eq(base.away_team_id), "same_base_opponents")
    good_base = base.loc[base.exclusion_reasons.eq(""), ["game_id", "game_date", "home_team_id", "away_team_id", "season", "season_type", "provider_calendar_date"]]
    good_base = good_base.rename(columns={key: "base_" + key for key in good_base if key != "game_id"})
    out = out.merge(good_base, on="game_id", how="left", validate="many_to_one", indicator="base_join")
    found = out.base_join.eq("both")
    add_reason(out, ~found, "no_unique_valid_base_game")
    for key in ["game_date", "home_team_id", "away_team_id", "season", "season_type"]:
        add_reason(out, found & ~out[key].eq(out["base_" + key]).fillna(False), "base_schedule_" + key + "_mismatch")
    known_calendar = found & out.provider_calendar_date.notna() & out.base_provider_calendar_date.notna()
    add_reason(out, known_calendar & out.provider_calendar_date.ne(out.base_provider_calendar_date), "base_schedule_calendar_date_mismatch")
    out["base_schedule_calendar_date_checked"] = known_calendar
    out["canonical_game_join_valid"] = out.exclusion_reasons.eq("")
    base["has_valid_schedule_join"] = base.game_id.isin(out.loc[out.canonical_game_join_valid, "game_id"])
    return out.drop(columns="base_join"), base


def join_players(raw, game_context, flagged_games=()):
    out = raw.copy().reset_index(drop=True)
    for key in ["game_id", "player_id", "team_id", "opponent_id"]:
        out[key] = ids(out[key])
    out["player_timestamp_precision_independently_verified"] = False
    out["player_provider_calendar_date"] = calendar_dates(out.game_local_date)
    out["game_date"] = pd.to_datetime(out.game_date, utc=True, errors="coerce", format="mixed")
    for key in ["season", "season_type"]:
        out[key] = pd.to_numeric(out[key], errors="coerce").astype("Int64")
    for key in ["did_not_play", "boxscore_observed", "is_home"]:
        out[key] = boolean(out[key])
    for key in STATS:
        out[key] = pd.to_numeric(out[key], errors="coerce").astype("Float64")
    duplicate_games = set(out.loc[out.duplicated(["game_id", "player_id"], keep=False) & out.game_id.notna(), "game_id"])
    excluded_columns = {"source_schedule_row", "exclusion_reasons"}
    links = game_context.loc[game_context.canonical_game_join_valid, [key for key in game_context if not key.startswith("base_") and key not in excluded_columns]].copy()
    links = links.rename(columns={key: "schedule_" + key for key in ["game_date", "game_local_date", "season", "season_type"]})
    out = out.merge(links, on="game_id", how="left", validate="many_to_one", indicator="schedule_join")
    out["exclusion_reasons"] = pd.Series("", index=out.index, dtype="string")
    found = out.schedule_join.eq("both")
    add_reason(out, ~found, "no_validated_schedule_link")
    add_reason(out, out[["game_id", "player_id", "team_id", "opponent_id", "game_date", "is_home"]].isna().any(axis=1), "missing_player_identity")
    for key in ["game_date", "season", "season_type"]:
        add_reason(out, found & ~out[key].eq(out["schedule_" + key]).fillna(False), "player_schedule_" + key + "_mismatch")
    known_calendar = found & out.player_provider_calendar_date.notna() & out.provider_calendar_date.notna()
    add_reason(out, known_calendar & out.player_provider_calendar_date.ne(out.provider_calendar_date), "player_schedule_calendar_date_mismatch")
    out["player_schedule_calendar_date_checked"] = known_calendar
    expected_team = out.home_team_id.where(out.is_home.fillna(False), out.away_team_id)
    expected_opponent = out.away_team_id.where(out.is_home.fillna(False), out.home_team_id)
    add_reason(out, found & ~out.team_id.eq(expected_team).fillna(False), "player_team_mismatch")
    add_reason(out, found & ~out.opponent_id.eq(expected_opponent).fillna(False), "player_opponent_mismatch")
    conflict_games = set(out.loc[found & out.exclusion_reasons.ne(""), "game_id"].dropna()) | duplicate_games
    add_reason(out, out.game_id.isin(conflict_games), "whole_game_player_identity_quarantine")
    out["quality_source_reconciliation_excluded"] = out.game_id.isin({str(value) for value in flagged_games})
    out["participation_status"] = "unknown_source_participation"
    out.loc[out.did_not_play.eq(True).fillna(False), "participation_status"] = "explicit_dnp"
    out.loc[out.did_not_play.eq(False).fillna(False), "participation_status"] = "reported_played"
    finite_nonnegative = pd.DataFrame({key: (np.isfinite(out[key]) & out[key].ge(0)).fillna(False) for key in STATS})
    invalid = pd.DataFrame({key: out[key].notna() & ~finite_nonnegative[key] for key in STATS})
    fractional = pd.DataFrame({key: out[key].where(finite_nonnegative[key]).mod(1).ne(0).fillna(False) for key in STATS if key != "minutes"})
    out["fractional_count_outcome_present"] = fractional.any(axis=1)
    out["invalid_numeric_outcome_present"] = invalid.any(axis=1) | out.fractional_count_outcome_present
    out["dnp_with_positive_stat_conflict"] = (out.did_not_play.eq(True) & out[STATS].gt(0).any(axis=1)).fillna(False)
    expected_observed = ~out.did_not_play & out.points.notna()
    out["source_boxscore_flag_conflict"] = (out.boxscore_observed.notna() & expected_observed.notna() & out.boxscore_observed.ne(expected_observed)).fillna(False)
    out["descriptive_outcome_eligible"] = (out.did_not_play.eq(False) & out.boxscore_observed.eq(True) & out.game_completed_actual.eq(True)
        & ~out.quality_source_reconciliation_excluded & ~out.invalid_numeric_outcome_present & ~out.source_boxscore_flag_conflict).fillna(False)
    good = out.exclusion_reasons.eq("")
    joined = out.loc[good].drop(columns=["exclusion_reasons", "schedule_join"]).copy()
    # Recompute source-player date partitions and insist the linked schedule agrees.
    joined["evaluation_split"] = split(joined.game_date, joined.source_game_date_precision)
    audit_columns = ["game_id", "player_id", "team_id", "opponent_id", "game_date", "game_local_date", "player_provider_calendar_date", "provider_calendar_date", "player_schedule_calendar_date_checked", "season", "season_type", "is_home", "did_not_play", "boxscore_observed", "exclusion_reasons"]
    excluded = out.loc[~good, audit_columns].copy()
    return joined.sort_values(["game_date", "game_id", "player_id"]), excluded.sort_values(["game_date", "game_id", "player_id"], na_position="last")


def descriptive_summary(joined):
    """Fixed bins and explicit source/participation/nonmissing denominators."""
    frame = joined[GROUPS + ["game_id", "did_not_play", "boxscore_observed", "descriptive_outcome_eligible", "invalid_numeric_outcome_present", "fractional_count_outcome_present", "quality_source_reconciliation_excluded"] + STATS].copy()
    flags = {
        "explicit_dnp_games": frame.did_not_play.eq(True), "reported_played_games": frame.did_not_play.eq(False),
        "unknown_participation_games": frame.did_not_play.isna(), "source_boxscore_observed_games": frame.boxscore_observed.eq(True),
        "descriptive_eligible_games": frame.descriptive_outcome_eligible,
        "source_quality_excluded_games": frame.quality_source_reconciliation_excluded,
        "invalid_numeric_outcome_games": frame.invalid_numeric_outcome_present,
        "fractional_count_outcome_games": frame.fractional_count_outcome_present,
        "reported_played_with_missing_minutes": frame.did_not_play.eq(False) & frame.minutes.isna(),
        "reported_played_with_zero_minutes": frame.did_not_play.eq(False) & frame.minutes.eq(0),
    }
    aggregations = {"source_listed_player_games": ("game_id", "size"), "distinct_games": ("game_id", "nunique")}
    for name, values in flags.items():
        frame[name] = values.fillna(False).astype("int64")
        aggregations[name] = (name, "sum")
    for key in STATS:
        frame[key] = frame[key].where(frame.descriptive_outcome_eligible)
        for suffix, function in [("nonmissing_games", "count"), ("sum", "sum"), ("mean", "mean"), ("std", "std")]:
            aggregations[key + "_" + suffix] = (key, function)
    result = frame.groupby(GROUPS, dropna=False, as_index=False).agg(**aggregations)
    for key in STATS:
        result[key + "_sum"] = result[key + "_sum"].where(result[key + "_nonmissing_games"].gt(0))
        result[key + "_missing_eligible_games"] = result.descriptive_eligible_games - result[key + "_nonmissing_games"]
    result["same_game_retrospective_only"] = True
    result["causal_effect_estimated"] = False
    result["automatic_training_join_allowed"] = False
    return result.sort_values(GROUPS).reset_index(drop=True)


def attendance_denominators(game_context, joined):
    """Count distinct schedule games separately from listed player observations."""
    games = game_context.loc[game_context.canonical_game_join_valid].copy()
    players = joined[["game_id", "player_id", "did_not_play", "descriptive_outcome_eligible"]].copy()
    flags = {
        "reported_played_player_rows": players.did_not_play.eq(False),
        "explicit_dnp_player_rows": players.did_not_play.eq(True),
        "unknown_participation_player_rows": players.did_not_play.isna(),
        "eligible_player_rows": players.descriptive_outcome_eligible,
    }
    aggregations = {"source_listed_player_rows": ("player_id", "size")}
    for name, values in flags.items():
        players[name] = values.fillna(False).astype("int64")
        aggregations[name] = (name, "sum")
    per_game = players.groupby("game_id", as_index=False).agg(**aggregations)
    games = games.merge(per_game, how="left", on="game_id", validate="one_to_one")
    # These are observed row counts, not imputed player outcomes or attendance.
    for name in aggregations:
        games[name] = games[name].fillna(0).astype("int64")
    game_flags = {
        "completed_source_games": games.game_completed_actual.eq(True),
        "games_with_validated_player_rows": games.source_listed_player_rows.gt(0),
        "games_with_eligible_player_rows": games.eligible_player_rows.gt(0),
        "games_with_missing_attendance": games.attendance_missing,
        "games_with_zero_attendance_unverified": games.attendance_zero_unverified,
        "games_with_observed_ratio": games.reported_attendance_capacity_ratio.notna(),
        "games_with_reported_ratio_above_one": games.reported_ratio_exceeds_one,
    }
    metrics = {"distinct_schedule_games": ("game_id", "nunique")}
    for name in aggregations:
        metrics[name] = (name, "sum")
    for name, values in game_flags.items():
        games[name] = values.fillna(False).astype("int64")
        metrics[name] = (name, "sum")
    result = games.groupby(GAME_GROUPS, as_index=False, dropna=False).agg(**metrics)
    result["attendance_unit"] = "game"
    result["player_rows_are_independent_crowd_samples"] = False
    return result.sort_values(GAME_GROUPS).reset_index(drop=True)


def quality_gates(game_context, joined, excluded):
    """Publication checks fixed before accessing outcomes; never tune thresholds."""
    total = len(joined) + len(excluded)
    share = len(joined) / total if total else 0.0
    eligible = joined.loc[joined.descriptive_outcome_eligible]
    positive_attendance = (eligible.attendance_actual.gt(0) & np.isfinite(eligible.attendance_actual)
        & eligible.attendance_actual.mod(1).eq(0)).fillna(False)
    partitions = {str(key): int(value) for key, value in eligible.evaluation_split.value_counts(dropna=False).items()}
    canonical = int(game_context.loc[game_context.canonical_game_join_valid, "game_id"].nunique())
    checks = {
        "nonzero_canonical_games": canonical > 0,
        "nonzero_eligible_player_rows": len(eligible) > 0,
        "nonzero_eligible_positive_attendance_rows": bool(positive_attendance.any()),
        "minimum_player_join_share": share >= MIN_PLAYER_JOIN_SHARE,
        "eligible_fit_pre_2024_present": partitions.get("fit_pre_2024", 0) > 0,
        "eligible_calibration_2024_present": partitions.get("calibration_2024", 0) > 0,
    }
    return {"passed": all(checks.values()), "checks": checks, "failed_checks": [key for key, value in checks.items() if not value],
        "minimum_player_join_share": MIN_PLAYER_JOIN_SHARE, "observed_player_join_share": share,
        "canonical_distinct_games": canonical, "base_player_rows": total, "eligible_player_rows": len(eligible),
        "eligible_positive_attendance_player_rows": int(positive_attendance.sum()),
        "eligible_positive_attendance_distinct_games": int(eligible.loc[positive_attendance, "game_id"].nunique()),
        "eligible_partition_player_rows": partitions,
        "eligible_date_min_utc": str(eligible.game_date.min()) if len(eligible) else None,
        "eligible_date_max_utc": str(eligible.game_date.max()) if len(eligible) else None,
        "complete_roster_or_complete_historical_coverage_verified": False}


def write_table(frame, path):
    require_github_hosted_runner()
    for key in frame.select_dtypes(include=["object", "string"]).columns:
        if frame[key].eq(NULL).fillna(False).any():
            raise ValueError("literal_null_token_collision")
    frame.to_csv(path, index=False, na_rep=NULL, compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "bytes": path.stat().st_size, "sha256": file_hash(path),
        "columns": {key: str(dtype) for key, dtype in frame.dtypes.items()},
        "missing_values": {key: int(value) for key, value in frame.isna().sum().items()}}


def package(output, dist):
    require_github_hosted_runner()
    dist.mkdir(parents=True, exist_ok=True)
    inventory = []
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise ValueError("output_symlink_disallowed")
        if path.is_file():
            inventory.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": file_hash(path)})
    target = dist / "csv-nba-crowd-performance.tar.gz"
    with tarfile.open(target, "w:gz", compresslevel=1) as archive:
        archive.add(output, arcname="data/crowd_performance/nba")
    manifest = {"archive": target.name, "bytes": target.stat().st_size, "sha256": file_hash(target), "files": inventory,
        "licenses": ["CC-BY-4.0 producer data", "MIT distribution repository"], "model_training": False}
    (dist / "nba_crowd_performance_asset_manifest.json").write_text(json.dumps(manifest, indent=2))
    (dist / "nba_crowd_performance_summary.json").write_text((output / "summary.json").read_text())


def main():
    require_github_hosted_runner()
    output = ROOT / "data/crowd_performance/nba"
    if output.exists() and any(output.iterdir()):
        raise ValueError("output_must_be_empty")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nba-crowd-", dir=os.environ["RUNNER_TEMP"]) as temporary:
        remote = Remote(Path(temporary))
        metadata = json.loads(remote.get(META_URL, "schedule_metadata.json", META_BYTES, META_HASH).read_text())
        schedules = []
        for item in schedule_pins(metadata):
            path = remote.get(item["source_url"], "schedule_" + str(item["season"]) + ".parquet", item["bytes"], item["sha256"])
            raw = pd.read_parquet(path)
            if {key: str(dtype) for key, dtype in raw.dtypes.items()} != item["source_schema"] or len(raw) != item["raw_rows"]:
                raise ValueError("schedule_schema_or_row_count_mismatch")
            schedule = normalize_schedule(raw, item["season"])
            schedule["source_schedule_sha256"] = item["sha256"]
            schedules.append(schedule)
            del raw
            path.unlink()
        all_schedules = pd.concat(schedules, ignore_index=True).sort_values(["game_date", "game_id"], na_position="last").reset_index(drop=True)
        archive = remote.get(BASE_URL, "nba_base.tar.gz", BASE_BYTES, BASE_HASH)
        games, players, flagged, declarations = load_base(archive, output)
        game_context, base_audit = validate_game_links(all_schedules, games)
        joined, excluded = join_players(players, game_context, flagged)
        del players, games, all_schedules, schedules
        aggregate = descriptive_summary(joined)
        denominators = attendance_denominators(game_context, joined)
        gates = quality_gates(game_context, joined, excluded)
        tables = {}
        for name, frame in [("game_context_audit", game_context), ("base_game_join_audit", base_audit), ("player_game_context", joined), ("excluded_player_game_audit", excluded), ("player_attendance_descriptive_summary", aggregate), ("attendance_band_denominators", denominators)]:
            tables[name] = write_table(frame, output / (name + ".csv.gz"))
        sources = {"sources": remote.sources, "selected_base_tables": declarations,
            "schedule_pin_report_url": META_URL, "schedule_pin_report_sha256": META_HASH,
            "attribution": "ESPN-derived NBA records compiled by hoopR/SportsDataverse. Normalized, joined, quality-screened and descriptively grouped by this project.",
            "rights": "Retain producer CC BY 4.0 and distribution MIT notices. These do not confer rights to third-party marks or article content."}
        summary = {"status": "complete" if gates["passed"] else "quality_gate_failed", "quality_gates": gates,
            "usable_for_descriptive_analysis": gates["passed"], "created_at_utc": now(), "source_bytes": remote.bytes, "source_byte_limit": MAX_SOURCE_BYTES,
            "http_requests": remote.http_requests, "schedule_source_rows": len(game_context), "base_game_rows": len(base_audit),
            "canonical_valid_games": int(game_context.canonical_game_join_valid.sum()),
            "quarantined_or_unmatched_schedule_rows": int((~game_context.canonical_game_join_valid).sum()),
            "base_games_without_valid_schedule": int((~base_audit.has_valid_schedule_join).sum()),
            "base_player_rows": len(joined) + len(excluded), "joined_player_rows": len(joined), "excluded_player_rows": len(excluded),
            "descriptive_eligible_player_rows": int(joined.descriptive_outcome_eligible.sum()), "descriptive_summary_rows": len(aggregate),
            "distinct_joined_games": int(joined.game_id.nunique()), "distinct_joined_players": int(joined.player_id.nunique()),
            "distinct_eligible_games": int(joined.loc[joined.descriptive_outcome_eligible, "game_id"].nunique()),
            "fractional_count_outcome_player_rows": int(joined.fractional_count_outcome_present.sum()),
            "invalid_numeric_outcome_player_rows": int(joined.invalid_numeric_outcome_present.sum()),
            "source_reported_zero_attendance_schedule_rows": int(game_context.attendance_zero_unverified.sum()),
            "missing_capacity_schedule_rows": int(game_context.capacity_missing.sum()),
            "fractional_attendance_schedule_rows": int(game_context.attendance_fractional_count.sum()),
            "fractional_capacity_schedule_rows": int(game_context.capacity_fractional_count.sum()),
            "reported_ratio_above_one_schedule_rows": int(game_context.reported_ratio_exceeds_one.fillna(False).sum()),
            "partition_player_rows": {str(key): int(value) for key, value in joined.evaluation_split.value_counts(dropna=False).items()},
            "participation_player_rows": {str(key): int(value) for key, value in joined.participation_status.value_counts(dropna=False).items()},
            "player_exclusion_reasons": {str(key): int(value) for key, value in excluded.exclusion_reasons.value_counts(dropna=False).items()},
            "model_training": False, "causal_effect_estimated": False, "automatic_training_join_allowed": False,
            "actual_occupancy_verified": False, "historical_capacity_validity_verified": False,
            "tables": tables}
        schema = {"csv_null_encoding": NULL, "empty_text_distinct_from_null": True, "tables": tables,
            "identifiers": "ESPN numeric identifiers retained as strings; exact identity/date/team checks required",
            "temporal_role": "All attendance, capacity ratios and player outcomes are retrospective descriptive context, never same-game pregame features.",
            "ratio_denominator": "Finite positive integral provider capacity and finite nonnegative integral attendance; historical basketball seating configuration unverified. Ratios above one remain unmodified. Fractional reported people/seats are retained but never rounded into valid ratios or bands.",
            "zero_attendance": "Reported zero; actual empty-stadium status not independently established.",
            "descriptive_denominator": "Each statistic uses eligible reported-played rows with finite nonnegative nonmissing values. Count statistics must be integral; minutes may be fractional and exceed 48. Any invalid observed statistic excludes that player row from all primary metric means. DNP and unknown participation are separately counted.",
            "crowd_denominator": "Attendance is a game-level report. attendance_band_denominators counts distinct games independently of repeated source-listed player rows; these player rows are not independent crowd samples.",
            "timestamp_precision": "Base/player schemas declare normalized UTC timestamps, not independently verified source precision. Exact UTC comparisons and the original linked schedule precision govern acceptance. Known provider calendar-date mismatches are quarantined without UTC shifting; missing/unparseable local calendar dates remain unverified.",
            "minutes": "Provider-rounded boxscore minutes. Zero or missing minutes never by itself establishes DNP; metrics with other valid outcomes retain separate denominators.",
            "grouping": GROUPS, "game_denominator_grouping": GAME_GROUPS, "grouping_bins": "Fixed reported attendance count bands; not occupancy/noise classes or outcome-optimized bins.",
            "source_sample": "Only source-listed player-game rows. Missing rows do not establish DNP or player eligibility. No roster universe was invented.",
            "confounding": "Raw descriptive groups do not adjust for player role, opponent, era, venue, selection, injuries or other causes. No crowd treatment effect or profit claim."}
        for filename, value in [("summary.json", summary), ("schema.json", schema), ("sources.json", sources)]:
            (output / filename).write_text(json.dumps(value, indent=2))
        package(output, ROOT / "dist")
    print(json.dumps({key: summary[key] for key in ["status", "source_bytes", "canonical_valid_games", "joined_player_rows", "excluded_player_rows", "descriptive_summary_rows"]}), flush=True)


if __name__ == "__main__":
    main()
