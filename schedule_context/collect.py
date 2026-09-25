"""Hosted-only schedule density and observed context continuity; no outcomes."""
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

REPO = "kennynakao/Tabular-Model-for-sports"
SOURCES = {
    "nba": {"tag": "matchup-36178068271-1", "asset": "csv-matchup-nba-teams.tar.gz", "bytes": 66075389,
            "sha256": "341c925a4c7bb03bdf05c8bc45b418d0ebef8ec0d50bc612285f6f831c710d35",
            "member": "data/matchup/nba_teams/team_game_profiles.csv.gz",
            "member_sha256": "c13e80d1cab3ae2fb2dc266d03597cdcca21cdfb668a56280bac4cb97b1e20f8"},
    "nfl": {"tag": "matchup-36179718171-1", "asset": "csv-matchup-nfl-teams.tar.gz", "bytes": 8653720,
            "sha256": "4ecdcf9533346d99c5c121ebd87085b41d041667adbac7074e08a714ea1eaeb8",
            "member": "data/matchup/nfl_teams/schedule_context_audit.csv.gz",
            "member_sha256": "c91a2f24949085036e5fc2664ef0aa3172f70c6fff34d540152ea46b164c3cb8"},
}
WINDOW_HOURS = (72, 168, 336)


def boolean(values):
    return values.astype("string").str.lower().map({"true": True, "false": False, "1": True, "0": False}).astype("boolean")


def normalize(raw, sport):
    """Explicit whitelist prevents observed scores or statistics entering output."""
    names = {"team_id": "team", "opponent_id": "opponent"} if sport == "nba" else {}
    raw = raw.rename(columns=names)
    required = ["game_id", "team", "opponent", "game_date", "season", "is_home"]
    if not set(required).issubset(raw):
        raise ValueError("Required schedule identity fields missing")
    out = raw[required].copy()
    for col in ["game_id", "team", "opponent", "season"]:
        out[col] = out[col].astype("string").replace(r"^\s*$", pd.NA, regex=True)
    out["game_date"] = pd.to_datetime(out.game_date, utc=True, errors="coerce", format="mixed")
    out["is_home"] = boolean(out.is_home)
    out["sport"] = sport.upper()
    for col in ["source_coach_name", "source_stadium", "source_stadium_id", "source_roof", "source_surface"]:
        out[col] = raw[col].astype("string").replace(r"^\s*$", pd.NA, regex=True) if col in raw else pd.NA
    if sport == "nba":
        out["source_start_time_known"] = raw.source_game_date_precision.eq("source_timestamp")
        # Pair/style flags in the parent archive use current-game scores.
        # Independently validate schedule identity without those flags.
        source_valid = pd.to_numeric(raw.season_type, errors="coerce").isin([2, 3])
    else:
        out["source_start_time_known"] = boolean(raw.source_game_date_time_known).fillna(False)
        source_valid = pd.Series(True, index=out.index)
    valid = out[required].notna().all(axis=1) & out.team.ne(out.opponent) & out.source_start_time_known & source_valid
    out["quality_exclusion"] = pd.Series(pd.NA, index=out.index, dtype="string")
    out.loc[~valid, "quality_exclusion"] = "missing_or_invalid_identity_start_or_source_pair"
    if out.duplicated(["game_id", "team"]).any():
        raise ValueError("Duplicate game/team identity; refusing arbitrary selection")
    ambiguous = out.duplicated(["team", "game_date"], keep=False) & valid
    out.loc[ambiguous, "quality_exclusion"] = "team_has_multiple_games_at_same_start"
    # Exclude the entire game if either side is invalid or non-reciprocal.
    pair_ok = out.groupby("game_id", dropna=False).apply(
        lambda g: len(g) == 2 and g.quality_exclusion.isna().all()
        and g.game_date.nunique() == 1 and g.season.nunique() == 1
        and set(g.team) == set(g.opponent) and set(g.is_home) == {True, False}, include_groups=False)
    bad_pair = ~out.game_id.map(pair_ok).fillna(False)
    out.loc[bad_pair & out.quality_exclusion.isna(), "quality_exclusion"] = "paired_game_excluded_or_inconsistent"
    good = out.loc[out.quality_exclusion.isna()].drop(columns="quality_exclusion").copy()
    good["evaluation_split"] = np.where(good.game_date.lt(pd.Timestamp("2024-01-01", tz="UTC")), "fit_pre_2024",
        np.where(good.game_date.lt(pd.Timestamp("2025-01-01", tz="UTC")), "calibration_2024", "holdout_2025_plus"))
    return good.sort_values(["game_date", "game_id", "team"]).reset_index(drop=True), out.loc[out.quality_exclusion.notna()].copy()


def same_known(first, second):
    return bool(first == second) if pd.notna(first) and pd.notna(second) else pd.NA


def features(schedule):
    rows = []
    for (_, _, _), team in schedule.groupby(["sport", "team", "season"], sort=False):
        team = team.sort_values(["game_date", "game_id"]).reset_index(drop=True)
        times = team.game_date.astype("int64").to_numpy()
        for i, current in team.iterrows():
            previous = team.iloc[i - 1] if i else None
            record = {name: current[name] for name in ["sport", "game_id", "team", "opponent", "season", "game_date", "is_home", "evaluation_split"]}
            record.update({"pre_prior_listed_games_in_source_season": i,
                "pre_previous_listed_start_utc": previous.game_date if i else pd.NaT,
                "pre_hours_since_previous_listed_start": (current.game_date - previous.game_date).total_seconds() / 3600 if i else np.nan,
                "pre_nominal_side_changed_from_previous": (bool(current.is_home) != bool(previous.is_home)) if i else pd.NA,
                "schedule_publication_time_verified": False, "actual_rest_travel_sleep_verified": False,
                "neutral_site_status_verified": False, "automatic_training_join_allowed": False,
                "history_resets_each_source_season": True, "schedule_history_completeness_verified": False})
            for hours in WINDOW_HOURS:
                left = int(np.searchsorted(times, (current.game_date - pd.Timedelta(hours=hours)).value, side="left"))
                record[f"pre_listed_starts_previous_{hours}h"] = i - left
                record[f"pre_{hours}h_window_reaches_first_listed_season_start"] = current.game_date - pd.Timedelta(hours=hours) <= team.game_date.iloc[0]
            streak = 1
            j = i - 1
            while j >= 0 and bool(team.iloc[j].is_home) == bool(current.is_home):
                streak += 1
                j -= 1
            record["pre_nominal_same_side_run_including_current"] = streak
            record["pre_nominal_side_run_reaches_source_season_boundary"] = j < 0
            # Source-listed coach/roof/surface may be retrospective. Retain them
            # in explicit audit fields instead of verified pregame predictors.
            for field in ["coach_name", "stadium", "stadium_id", "roof", "surface"]:
                col = "source_" + field
                record["audit_current_" + field] = current[col]
                record["audit_previous_listed_" + field] = previous[col] if i else pd.NA
                equal = same_known(current[col], previous[col]) if i else pd.NA
                record["audit_" + field + "_changed_from_previous"] = not equal if pd.notna(equal) else pd.NA
            coach = current.source_coach_name
            coach_streak = 0
            j = i - 1
            if pd.notna(coach):
                while j >= 0 and same_known(coach, team.iloc[j].source_coach_name) is True:
                    coach_streak += 1
                    j -= 1
            record["audit_prior_consecutive_listed_games_same_coach_name"] = coach_streak if pd.notna(coach) else pd.NA
            record["audit_coach_run_reaches_source_season_boundary"] = j < 0 if pd.notna(coach) else pd.NA
            record["coach_identity_and_tenure_verified"] = False
            rows.append(record)
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("No eligible schedule rows")
    out["pre_previous_start_within_12h"] = out.pre_hours_since_previous_listed_start.le(12).astype("boolean").where(out.pre_hours_since_previous_listed_start.notna())
    return out.sort_values(["game_date", "game_id", "team"]).reset_index(drop=True)


def opponent_comparisons(frame):
    keys = ["sport", "game_id", "team", "opponent", "game_date", "season", "evaluation_split"]
    counts = [f"pre_listed_starts_previous_{h}h" for h in WINDOW_HOURS] + ["pre_hours_since_previous_listed_start"]
    opposite = frame[["sport", "game_id", "team"] + counts].rename(columns={"team": "opponent", **{c: "opponent_" + c for c in counts}})
    out = frame[keys + counts].merge(opposite, on=["sport", "game_id", "opponent"], validate="one_to_one", how="left")
    for col in counts:
        out["difference_team_minus_opponent_" + col] = out[col] - out["opponent_" + col]
    out["automatic_training_join_allowed"] = False
    return out


def fetch(sport, cache, output):
    require_github_hosted_runner()
    source = SOURCES[sport]
    url = f"https://github.com/{REPO}/releases/download/{source['tag']}/{source['asset']}"
    path = cache / source["asset"]
    digest, size = hashlib.sha256(), 0
    with requests.get(url, stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > source["bytes"]:
                    raise ValueError("Source exceeded pinned transfer bound")
                digest.update(chunk)
                handle.write(chunk)
    if size != source["bytes"] or digest.hexdigest() != source["sha256"]:
        raise ValueError("Archive integrity failed")
    with tarfile.open(path, "r:gz") as archive:
        seen = set()
        for member in archive.getmembers():
            parsed = PurePosixPath(member.name)
            if parsed.is_absolute() or ".." in parsed.parts or member.issym() or member.islnk() or member.name in seen:
                raise ValueError("Unsafe/duplicate archive member")
            seen.add(member.name)
        member = archive.getmember(source["member"])
        if not member.isfile() or member.size > 12_000_000:
            raise ValueError("Unexpected source table type/size")
        content = archive.extractfile(member).read()
        if hashlib.sha256(content).hexdigest() != source["member_sha256"]:
            raise ValueError("Table integrity failed")
        # Limit decompression before pandas allocates its frame.
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            uncompressed = stream.read(120_000_001)
        if len(uncompressed) > 120_000_000:
            raise ValueError("CSV decompression limit exceeded")
        raw = pd.read_csv(io.BytesIO(uncompressed), dtype="string", keep_default_na=False, na_values=[r"\N"])
        for member in archive.getmembers():
            if member.isfile() and "LICENSE" in Path(member.name).name and member.size < 100_000:
                (output / (sport + "_" + Path(member.name).name)).write_bytes(archive.extractfile(member).read())
    return raw, {**source, "url": url, "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "columns": {c: str(t) for c, t in frame.dtypes.items()}, "missing": {c: int(n) for c, n in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    output = ROOT / "data/schedule_context"
    output.mkdir(parents=True, exist_ok=True)
    tables, sources, coverage = {}, [], {}
    if sum(s["bytes"] for s in SOURCES.values()) > 100_000_000:
        raise ValueError("New pilot transfer cap exceeded")
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"], prefix="schedule-context-") as temp:
        for sport in SOURCES:
            raw, source = fetch(sport, Path(temp), output)
            sources.append(source)
            clean, excluded = normalize(raw, sport)
            result = features(clean)
            comparisons = opponent_comparisons(result)
            observed = result.pre_hours_since_previous_listed_start.notna()
            if len(clean) < 0.5 * len(raw) or observed.mean() < 0.5:
                raise ValueError("Schedule coverage unexpectedly low")
            if result.loc[observed, "pre_hours_since_previous_listed_start"].le(0).any():
                raise ValueError("Nonpositive prior start interval")
            for suffix, frame in [("team_schedule_candidates", result), ("opponent_comparisons", comparisons), ("excluded_rows_audit", excluded)]:
                name = sport + "_" + suffix
                tables[name] = write(frame, output / (name + ".csv.gz"))
            coverage[sport] = {"source_rows": len(raw), "candidate_rows": len(result), "excluded_rows": len(excluded),
                "games": int(result.game_id.nunique()), "rows_with_prior_start": int(observed.sum()),
                "rows_with_prior_start_in_72h": int(result.pre_listed_starts_previous_72h.gt(0).sum()),
                "source_coach_names_present": int(result.audit_current_coach_name.notna().sum()),
                "observed_coach_name_changes": int(result.audit_coach_name_changed_from_previous.eq(True).sum()),
                "same_start_exclusion_rows": int(excluded.quality_exclusion.eq("team_has_multiple_games_at_same_start").sum()),
                "evaluation_splits": result.evaluation_split.value_counts().to_dict()}
            print(json.dumps({sport: coverage[sport]}), flush=True)
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "coverage": coverage, "sources": sources,
        "source_bytes": sum(s["bytes"] for s in sources), "tables": tables, "training_performed": False,
        "limitations": ["Schedules are retrospective source snapshots; historical publication times and completeness are unverified.",
            "Intervals measure listed starts, not completed games, sleep, recovery, travel distance or local calendar back-to-backs.",
            "Density counts use inclusive window beginning and strictly earlier current start; same-start ambiguous games are excluded with both opponents.",
            "Histories reset each source season, including coaching name streaks. A streak is not a complete employment tenure.",
            "Home/away streaks are nominal designations. Neutral sites and actual home-court location are unverified.",
            "Coach name, stadium, roof and surface changes remain retrospective audits, not verified pregame features. No coach philosophy or causal effect is inferred.",
            "Missing or excluded games can interrupt true histories. No current-game score or result is present; no model joining or fitting performed."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    (output / "schema.json").write_text(json.dumps({"csv_null_encoding": r"\N", "timestamps": "UTC", "identifiers": "strings", "tables": tables,
        "roles": {"pre_*": "prior listed starts or nominal current schedule; availability not verified", "audit_*": "retrospective context requiring separate validation"}}, indent=2))
    (output / "SOURCE_RIGHTS.md").write_text("Original source rights remain attached. NBA: hoopR and SportsDataverse sources, source release notices retained. NFL: Lee Sharpe/nfldata schedule context via nflverse; source release LICENSE retained. Derived schedule arithmetic does not grant additional rights or prove historical availability. Archive and member hashes and original source URLs appear in summary.json.\n")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive_path = dist / "csv-schedule-context.tar.gz"
    with tarfile.open(archive_path, "w:gz", compresslevel=1) as archive:
        archive.add(output, arcname="data/schedule_context")
    manifest = {"archive": archive_path.name, "bytes": archive_path.stat().st_size, "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                "files": [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(output.iterdir()) if p.is_file()]}
    (dist / "schedule_context_asset_manifest.json").write_text(json.dumps(manifest, indent=2))
    (dist / "schedule_context_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
