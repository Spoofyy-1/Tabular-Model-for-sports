#!/usr/bin/env python3
"""Cloud-only NFL team styles from prior games; labels and current stats separate."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
import time

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

REPO = "Spoofyy-1/Tabular-Model-for-sports"
MIN_HISTORY_LAG_HOURS = 12
ARCHIVES = {
    "plays": ("enrichment-36170742592-1", "csv-nfl_plays.tar.gz", 72422446, "85ca1d6ff08f90316ef85feb01764ab68a63f62a1e0361e6bbb447c7cc72feb0"),
    "schedule": ("csv-36170200182-1", "csv-base-nfl.tar.gz", 88715353, "eb1c3168d370d02e7497486f5ba9999e7c886f2bd7644da2280184ad116a2647"),
}
ALIASES = {"LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV", "JAC": "JAX", "WSH": "WAS", "AZ": "ARI"}
RATIOS = {
    "pass_play_rate": ("called_passes", "classified_plays"), "designed_run_rate": ("designed_runs", "classified_plays"),
    "early_down_pass_rate": ("early_passes", "early_plays"), "neutral_early_down_pass_rate": ("neutral_early_passes", "neutral_early_plays"),
    "shotgun_rate": ("shotgun_yes", "shotgun_known"), "no_huddle_rate": ("no_huddle_yes", "no_huddle_known"),
    "yards_per_play": ("yards", "yards_known"), "yards_per_dropback": ("pass_yards", "pass_yards_known"),
    "yards_per_designed_run": ("run_yards", "run_yards_known"), "explosive_20_rate": ("explosive_20", "yards_known"),
    "sack_rate": ("sacks", "sack_known"), "qb_hit_rate": ("qb_hits", "qb_hit_known"),
    "interception_rate": ("interceptions", "interception_known"), "air_yards_per_targeted_pass": ("air_yards", "air_yards_known"),
    "deep_target_rate": ("deep_targets", "air_yards_known"), "red_zone_pass_rate": ("red_zone_passes", "red_zone_plays"),
    "fourth_down_go_rate": ("fourth_down_go", "fourth_down_decisions"),
}
NUMERIC = {"down", "qtr", "score_differential", "yardline_100", "pass_attempt", "rush_attempt", "qb_dropback", "qb_scramble", "qb_kneel", "qb_spike", "no_play", "shotgun", "no_huddle", "yards_gained", "air_yards", "sack", "qb_hit", "interception"}
BINARY = {"pass_attempt", "rush_attempt", "qb_dropback", "qb_scramble", "qb_kneel", "qb_spike", "no_play", "shotgun", "no_huddle", "sack", "qb_hit", "interception"}


def numeric(frame, name):
    if name not in frame:
        return pd.Series(np.nan, index=frame.index)
    values = pd.to_numeric(frame[name], errors="coerce")
    if name in BINARY:
        booleans = frame[name].astype("string").str.lower().map({"true": 1, "false": 0})
        values = values.fillna(booleans)
    return values


def game_metrics(raw):
    """Aggregate retrospective counts; caller must lag before using as features."""
    if not {"game_id", "posteam", "defteam", "play_type", "play_id"}.issubset(raw):
        raise ValueError("Required NFL PBP identity/type columns are absent")
    frame = raw.copy()
    for name in ["game_id", "play_id", "posteam", "defteam"]:
        frame[name] = frame[name].astype("string")
    for name in ["posteam", "defteam"]:
        frame[name] = frame[name].replace(ALIASES)
    if frame.duplicated(["game_id", "play_id"]).any():
        raise ValueError("Input PBP game/play keys must be unique")
    for name in NUMERIC:
        frame[name] = numeric(frame, name)
    identity_valid = (frame.posteam.notna() & frame.defteam.notna() & frame.posteam.ne(frame.defteam)
                      & frame.posteam.str.strip().ne("") & frame.defteam.str.strip().ne(""))
    # nflfastR documents no_play as a play_type category. The pinned archive
    # has no standalone no_play column, so require explicitly eligible types.
    # If an optional flag exists, its value must also explicitly be zero.
    eligible_type = frame.play_type.isin(["pass", "run", "punt", "field_goal"])
    eligible_no_play = frame.no_play.eq(0) if "no_play" in raw else eligible_type
    known_non_kneel_spike = frame.qb_kneel.eq(0) & frame.qb_spike.eq(0)
    live = identity_valid & eligible_type & eligible_no_play & known_non_kneel_spike
    scrimmage = live & frame.play_type.isin(["pass", "run"]) & frame.down.between(1, 4)
    called_pass = scrimmage & (frame.play_type.eq("pass") | frame.qb_dropback.eq(1).fillna(False) | frame.qb_scramble.eq(1).fillna(False) | frame.pass_attempt.eq(1).fillna(False))
    designed_run = scrimmage & frame.play_type.eq("run") & frame.qb_dropback.eq(0) & frame.qb_scramble.eq(0) & frame.pass_attempt.eq(0)
    classified = (called_pass | designed_run).fillna(False)
    early = classified & frame.down.isin([1, 2])
    neutral_early = early & frame.qtr.between(1, 3) & frame.score_differential.abs().le(7)
    red_zone = classified & frame.yardline_100.between(0, 20)
    fourth = live & frame.down.eq(4) & frame.play_type.isin(["pass", "run", "punt", "field_goal"])
    counters = {"plays": scrimmage, "classified_plays": classified, "called_passes": called_pass, "designed_runs": designed_run,
                "early_plays": early, "early_passes": early & called_pass, "neutral_early_plays": neutral_early,
                "neutral_early_passes": neutral_early & called_pass, "red_zone_plays": red_zone,
                "red_zone_passes": red_zone & called_pass, "fourth_down_decisions": fourth, "fourth_down_go": fourth & scrimmage}
    counters["eligible_type_rows_with_unknown_kneel_or_spike"] = eligible_type & (~frame.qb_kneel.isin([0, 1]) | ~frame.qb_spike.isin([0, 1]))
    for name in ["shotgun", "no_huddle"]:
        counters[name + "_known"] = scrimmage & frame[name].isin([0, 1])
        counters[name + "_yes"] = scrimmage & frame[name].eq(1)
    for source, label in [("sack", "sacks"), ("qb_hit", "qb_hits"), ("interception", "interceptions")]:
        counters[source + "_known"] = called_pass & frame[source].isin([0, 1])
        counters[label] = called_pass & frame[source].eq(1)
    counters["yards_known"] = scrimmage & frame.yards_gained.notna()
    counters["pass_yards_known"] = called_pass & frame.yards_gained.notna()
    counters["run_yards_known"] = designed_run & frame.yards_gained.notna()
    counters["air_yards_known"] = called_pass & frame.air_yards.notna()
    counters["explosive_20"] = counters["yards_known"] & frame.yards_gained.ge(20)
    counters["deep_targets"] = counters["air_yards_known"] & frame.air_yards.ge(20)
    rows = frame[["game_id", "posteam", "defteam"]].copy()
    for name, values in counters.items():
        rows[name] = values.fillna(False).astype(int)
    for name, source, mask in [("yards", "yards_gained", counters["yards_known"]), ("pass_yards", "yards_gained", counters["pass_yards_known"]),
                              ("run_yards", "yards_gained", counters["run_yards_known"]), ("air_yards", "air_yards", counters["air_yards_known"])]:
        rows[name] = frame[source].where(mask, 0).fillna(0)
    rows = rows.loc[identity_valid]
    grouped = rows.groupby(["game_id", "posteam", "defteam"], as_index=False).sum(numeric_only=True)
    if grouped.duplicated(["game_id", "posteam"]).any():
        raise ValueError("One offense has multiple defensive opponents in a source game")
    off = grouped.rename(columns={"posteam": "team", "defteam": "opponent"})
    counts = [name for name in off if name not in {"game_id", "team", "opponent"}]
    defense = off.rename(columns={"team": "opponent", "opponent": "team", **{name: "observed_def_" + name for name in counts}})
    off = off.rename(columns={name: "observed_off_" + name for name in counts})
    return off.merge(defense, on=["game_id", "team", "opponent"], how="outer", validate="one_to_one")


def team_schedule(raw):
    required = {"game_id", "game_date", "home_team", "away_team", "season"}
    if not required.issubset(raw):
        raise ValueError("Schedule lacks required game/team/date fields")
    if raw.game_id.duplicated().any():
        raise ValueError("Schedule game IDs are not unique")
    parts = []
    for side, other in [("home", "away"), ("away", "home")]:
        row = pd.DataFrame({"game_id": raw.game_id.astype("string"), "team": raw[side + "_team"].astype("string").replace(ALIASES),
                            "opponent": raw[other + "_team"].astype("string").replace(ALIASES), "season": numeric(raw, "season").astype("Int64"),
                            "game_date": pd.to_datetime(raw.game_date, utc=True, errors="coerce", format="mixed"), "is_home": side == "home"})
        for name in ["week", "game_type", "stadium", "stadium_id", "roof", "surface", "game_date_time_known"]:
            row["source_" + name] = raw[name] if name in raw else pd.NA
        row["source_coach_name"] = raw[side + "_coach"].astype("string") if side + "_coach" in raw else pd.NA
        row["source_coach_assignment_time_verified"] = False
        row["label_team_score"] = numeric(raw, side + "_score")
        row["label_opponent_score"] = numeric(raw, other + "_score")
        known = row.label_team_score.notna() & row.label_opponent_score.notna()
        tied = row.label_team_score.eq(row.label_opponent_score)
        row["label_score_margin"] = row.label_team_score - row.label_opponent_score
        row["label_team_win"] = row.label_score_margin.gt(0).astype("boolean").where(known & ~tied)
        row["label_result"] = pd.Series(pd.NA, index=row.index, dtype="string")
        row.loc[known & tied, "label_result"] = "tie"
        row.loc[known & ~tied & row.label_score_margin.gt(0), "label_result"] = "win"
        row.loc[known & ~tied & row.label_score_margin.lt(0), "label_result"] = "loss"
        row["evaluation_split"] = "unknown_date"
        row.loc[row.game_date.lt(pd.Timestamp("2025-01-01", tz="UTC")), "evaluation_split"] = "development_through_2024"
        row.loc[row.game_date.ge(pd.Timestamp("2025-01-01", tz="UTC")), "evaluation_split"] = "holdout_2025_onward"
        precision = row.source_game_date_time_known.astype("string").str.lower().isin(["true", "1"])
        boundary = row.game_date.dt.strftime("%Y-%m-%d").isin(["2024-12-31", "2025-01-01"])
        row.loc[~precision & boundary, "evaluation_split"] = "date_precision_unresolved"
        parts.append(row)
    return pd.concat(parts, ignore_index=True).sort_values(["game_date", "game_id", "team"]).reset_index(drop=True)


def reconcile_metrics(frame):
    """Collapse identical payloads; quarantine whole games with conflicting keys."""
    required = {"game_id", "team", "opponent", "source_partition_member"}
    if not required.issubset(frame):
        raise ValueError("Metric reconciliation requires game/team and source provenance")
    payload = [name for name in frame if name != "source_partition_member"]
    distinct = frame.drop_duplicates(payload).copy()
    conflicts = distinct.duplicated(["game_id", "team"], keep=False)
    bad_games = set(distinct.loc[conflicts, "game_id"])
    # Both sides of the game are excluded: a conflict can also contaminate the
    # opponent's defensive counts. Do not choose an arbitrary source season.
    audit = frame.loc[frame.game_id.isin(bad_games)].copy()
    audit["quality_exclusion"] = "game_has_conflicting_team_metrics_or_opponent_identity"
    clean = distinct.loc[~distinct.game_id.isin(bad_games)].drop(columns="source_partition_member")
    provenance = frame.groupby(["game_id", "team"], as_index=False).agg(
        source_partition_members=("source_partition_member", lambda values: json.dumps(sorted(set(values.dropna())), separators=(",", ":"))))
    clean = clean.merge(provenance, on=["game_id", "team"], how="left", validate="one_to_one")
    report = {"source_metric_rows": len(frame), "identical_metric_rows_collapsed": len(frame)-len(distinct),
              "conflicting_game_team_keys": int(distinct.loc[conflicts, ["game_id", "team"]].drop_duplicates().shape[0]),
              "quarantined_games": len(bad_games), "quarantined_source_metric_rows": len(audit), "canonical_team_game_rows": len(clean)}
    return clean.reset_index(drop=True), audit.reset_index(drop=True), report


def lagged_features(team_games):
    """Only completed observed games on strictly earlier UTC calendar dates."""
    identity_columns = ["game_id", "team", "opponent", "game_date", "season", "is_home", "evaluation_split"]
    counters = [name for name in team_games if name.startswith("observed_off_") or name.startswith("observed_def_")]
    results = []
    for _, group in team_games.groupby("team", sort=True):
        group = group.sort_values(["game_date", "game_id"], na_position="last")
        history = group.loc[group.game_date.notna() & group.label_result.notna() & group.observed_off_plays.gt(0)].copy()
        history_times = history.game_date.astype("int64").to_numpy()
        values = history[counters].apply(pd.to_numeric, errors="coerce").fillna(0).to_numpy(dtype=float)
        sums = np.vstack([np.zeros(len(counters)), values.cumsum(axis=0)])
        positions = {name: i for i, name in enumerate(counters)}
        listed_days = group.loc[group.game_date.notna(), "game_date"].dt.normalize().drop_duplicates().sort_values()
        for _, row in group.iterrows():
            result = {name: row[name] for name in identity_columns}
            day = row.game_date.normalize() if pd.notna(row.game_date) else pd.NaT
            cutoff = min(day, row.game_date - pd.Timedelta(hours=MIN_HISTORY_LAG_HOURS)) if pd.notna(day) else pd.NaT
            end = int(np.searchsorted(history_times, cutoff.value, side="left")) if pd.notna(cutoff) else 0
            result["pre_history_cutoff_utc"] = cutoff
            result["history_completion_lag_assumption_hours"] = MIN_HISTORY_LAG_HOURS
            result["pre_history_latest_game_date"] = history.game_date.iloc[end-1] if end else pd.NaT
            prior_listed = listed_days.loc[listed_days.lt(day)] if pd.notna(day) else listed_days.iloc[:0]
            result["pre_calendar_days_since_previous_listed_game"] = (day - prior_listed.iloc[-1]).days if len(prior_listed) else np.nan
            for window in [5, 10]:
                start = max(0, end-window)
                totals = sums[end] - sums[start]
                result["pre_observed_games_last" + str(window)] = end-start
                for side in ["off", "def"]:
                    prefix = "observed_" + side + "_"
                    result["pre_" + side + "_plays_last" + str(window)] = totals[positions[prefix + "plays"]]
                    for metric, (numerator, denominator) in RATIOS.items():
                        n, d = totals[positions[prefix + numerator]], totals[positions[prefix + denominator]]
                        result["pre_" + side + "_" + metric + "_last" + str(window)] = n/d if d > 0 else np.nan
            p = result["pre_off_pass_play_rate_last10"]
            sufficient = result["pre_observed_games_last10"] >= 3 and result["pre_off_plays_last10"] >= 100 and pd.notna(p)
            result["pre_rule_based_offense_style_last10"] = ("pass_heavy" if p >= 0.62 else "run_heavy" if p <= 0.45 else "balanced") if sufficient else "insufficient_history"
            result["historical_source_asof_verified"] = False
            result["automatic_model_use_allowed"] = False
            result["source_is_revised_snapshot"] = True
            results.append(result)
    features = pd.DataFrame(results)
    pre_columns = [name for name in features if name.startswith("pre_")]
    other = features[["game_id", "team"] + pre_columns].rename(columns={"team": "opponent", **{name: "opponent_" + name for name in pre_columns}})
    features = features.merge(other, on=["game_id", "opponent"], how="left", validate="many_to_one")
    features["pre_offense_minus_opponent_defense_yards_per_play_last10"] = features.pre_off_yards_per_play_last10 - features.opponent_pre_def_yards_per_play_last10
    features["pre_pass_share_times_opponent_pass_yards_allowed_last10"] = features.pre_off_pass_play_rate_last10 * features.opponent_pre_def_yards_per_dropback_last10
    features["pre_run_share_times_opponent_run_yards_allowed_last10"] = features.pre_off_designed_run_rate_last10 * features.opponent_pre_def_yards_per_designed_run_last10
    return features.sort_values(["game_date", "game_id", "team"]).reset_index(drop=True)


def validate_coverage(joined, features):
    """Fail publication when schema/filter errors leave an unusable panel."""
    eligible = joined.label_result.notna() & joined.pbp_join.eq("both")
    positive = joined.observed_off_plays.gt(0).fillna(False)
    denominator = int(eligible.sum())
    fraction = float((eligible & positive).sum() / denominator) if denominator else 0.0
    if not denominator or fraction < 0.5:
        raise ValueError("NFL coverage gate failed: fewer than half of matched completed team-games contain observed offensive plays")
    season_counts = joined.loc[eligible, ["season"]].assign(positive=positive.loc[eligible]).groupby("season").positive.sum()
    if season_counts.eq(0).any():
        raise ValueError("NFL coverage gate failed: a matched completed source season has no positive-play team-games")
    feature_rows = int(features.pre_off_pass_play_rate_last10.notna().sum())
    if feature_rows == 0:
        raise ValueError("NFL coverage gate failed: no team-game has a nonmissing lagged passing-style feature")
    return {"matched_completed_team_games": denominator, "positive_play_fraction": fraction,
            "lagged_pass_style_rows": feature_rows, "minimum_positive_play_fraction": 0.5, "passed": True}


def download(name, directory, budget):
    require_github_hosted_runner()
    tag, asset, size, digest = ARCHIVES[name]
    url = "https://github.com/" + REPO + "/releases/download/" + tag + "/" + asset
    target = directory / asset
    for attempt in range(3):
        try:
            actual, sha = 0, hashlib.sha256()
            with requests.get(url, stream=True, timeout=(20, 150)) as response:
                response.raise_for_status()
                with target.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        actual += len(chunk)
                        budget[0] += len(chunk)
                        if actual > size or budget[0] > 500_000_000:
                            raise ValueError("NFL team source budget exceeded")
                        sha.update(chunk)
                        stream.write(chunk)
            if actual != size or sha.hexdigest() != digest:
                raise ValueError("Pinned NFL source archive hash/size mismatch")
            return target, {"release": tag, "asset": asset, "url": url, "bytes": actual, "sha256": digest}
        except requests.RequestException:
            target.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt+1))
    raise RuntimeError("Unreachable download")


def csv_members(path, predicate):
    require_github_hosted_runner()
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in member.name:
                raise ValueError("Unsafe source archive member")
            if member.isfile() and predicate(member.name):
                if member.size > 100_000_000:
                    raise ValueError("Compressed CSV member exceeds bounded input size")
                with archive.extractfile(member) as stream, gzip.GzipFile(fileobj=stream) as csv:
                    yield member.name, pd.read_csv(csv, dtype="string", keep_default_na=False, na_values=[r"\N"], low_memory=False)


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"rows": len(frame), "columns": {name: str(dtype) for name, dtype in frame.dtypes.items()}, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
            "missing": {name: int(count) for name, count in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    output = ROOT / "data/matchup/nfl_teams"
    output.mkdir(parents=True, exist_ok=True)
    budget, sources, parts, partitions = [0], [], [], []
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"], prefix="nfl-team-styles-") as cache:
        cache = Path(cache)
        schedule_path, source = download("schedule", cache, budget)
        sources.append(source)
        schedules = list(csv_members(schedule_path, lambda name: name.endswith("/nfl/schedules.csv.gz")))
        if len(schedules) != 1:
            raise ValueError("Pinned archive must contain exactly one NFL schedules CSV")
        schedule = team_schedule(schedules[0][1])
        del schedules
        schedule_path.unlink()
        play_path, source = download("plays", cache, budget)
        sources.append(source)
        for member, raw in csv_members(play_path, lambda name: bool(re.search(r"/pbp/season=\d{4}/data\.csv\.gz$", name))):
            metrics = game_metrics(raw)
            metrics["source_partition_member"] = member
            partitions.append({"source_member": member, "source_play_rows": len(raw), "team_game_metric_rows": len(metrics),
                               "observed_offensive_plays": int(metrics.observed_off_plays.sum()),
                               "no_play_filter": "explicit_flag_and_documented_play_type" if "no_play" in raw else "documented_play_type_no_standalone_flag",
                               "unknown_kneel_or_spike_rows": int(metrics.observed_off_eligible_type_rows_with_unknown_kneel_or_spike.sum())})
            parts.append(metrics)
            print(json.dumps(partitions[-1]), flush=True)
    if not parts:
        raise ValueError("Pinned PBP archive contained no recognized season partitions")
    metrics, metric_conflicts, reconciliation = reconcile_metrics(pd.concat(parts, ignore_index=True))
    print(json.dumps({"metric_reconciliation": reconciliation}), flush=True)
    joined = schedule.merge(metrics, on=["game_id", "team", "opponent"], how="left", validate="one_to_one", indicator="pbp_join")
    features = lagged_features(joined)
    coverage_checks = validate_coverage(joined, features)
    keys = ["game_id", "team", "opponent", "game_date", "season", "evaluation_split"]
    labels = joined[keys + [name for name in joined if name.startswith("label_")]].copy()
    observed = joined[keys + [name for name in joined if name.startswith("observed_")] + ["pbp_join", "source_partition_members"]].copy()
    context = joined[[name for name in schedule if not name.startswith("label_")]].copy()
    tables = {name: write(frame, output / (name + ".csv.gz")) for name, frame in [("pregame_team_features", features), ("win_labels", labels), ("observed_team_game_metrics", observed), ("schedule_context_audit", context)]}
    tables["conflicting_team_game_metrics_audit"] = write(metric_conflicts, output / "conflicting_team_game_metrics_audit.csv.gz")
    matching = metrics.merge(schedule[["game_id", "team", "opponent"]], on=["game_id", "team", "opponent"], how="left", validate="one_to_one", indicator="schedule_join")
    unmapped_metrics = matching.loc[matching.schedule_join.eq("left_only")].copy()
    tables["pbp_games_outside_schedule_audit"] = write(unmapped_metrics, output / "pbp_games_outside_schedule_audit.csv.gz")
    summary = {"dataset": "nfl_team_styles", "created_at_utc": datetime.now(timezone.utc).isoformat(), "sources": sources, "source_bytes_including_retries": budget[0],
               "team_game_rows": len(joined), "games": int(joined.game_id.nunique()), "team_games_with_observed_plays": int(joined.observed_off_plays.gt(0).sum()),
               "team_games_without_pbp": int(joined.pbp_join.eq("left_only").sum()), "pbp_team_games_outside_schedule": len(unmapped_metrics),
               "date_partitions": features.evaluation_split.value_counts().to_dict(), "coach_name_rows": int(context.source_coach_name.notna().sum()), "tables": tables, "partitions": partitions,
               "metric_reconciliation": reconciliation,
               "coverage_checks": coverage_checks,
               "training_performed": False, "strict_prior_utc_calendar_day_lag": True, "source_historical_publication_verified": False,
               "limitations": ["Latest revised PBP snapshots do not establish their historical publication or model vintage; lagged values are candidates, not verified as-of features.",
                   "Pinned PBP selection has no EPA or win-probability columns. Efficiency means observed yards per play/dropback/run, not EPA or causal skill.",
                   "Only earlier UTC calendar dates and game starts more than twelve hours earlier enter the preceding 5/10-game windows. Actual completion and publication times are unverified. No target-game outcome or same-day game enters a feature.",
                   "Holdout features can use earlier held-out games as a chronological online protocol; holdout outcomes do not affect development rows. No fitting or tuning occurs.",
                   "Styles are pooled-count ratios with explicit denominators. Missing fields stay unknown, not zero. Neutral early downs are first/second down in quarters 1–3 with score differential within seven points.",
                   "Pass calls include dropbacks/sacks/scrambles; designed runs exclude scrambles. Kneels/spikes/no-plays and unrecognized downs are excluded. Fourth-down rate is go attempts among recognized go/punt/field-goal decisions, not fourth-down optimality.",
                   "The pinned PBP has no standalone no_play flag: source play_type explicitly selects pass/run/punt/field_goal and excludes no_play/qb_kneel/qb_spike. Missing kneel/spike flags remain unknown and exclude affected rows, with counts retained.",
                   "Offense style labels use declared fixed descriptive thresholds: pass share >=0.62 pass-heavy; <=0.45 run-heavy; otherwise balanced, requiring 3 prior games and 100 plays. They are not learned or validated archetypes.",
                   "Defense metrics describe opponents' realized offense against the defense. Opponent interactions are arithmetic candidate comparisons, not matchup outcome probabilities.",
                   "Coach names appear only in the source schedule audit with unverified publication timing; no invented coaching philosophy, roster injury effect or private routine is inferred.",
                   "Schedule gaps are calendar intervals, not physiological rest or verified travel. Ties have null binary win labels and an explicit tie result."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    (output / "schema.json").write_text(json.dumps({"null_token": r"\N", "identifiers": "strings", "timestamps": "UTC", "tables": tables, "ratio_definitions": RATIOS,
        "temporal_roles": {"pre_*": "strictly earlier-calendar-day candidate features", "opponent_pre_*": "opponent's equally lagged features", "observed_*": "current-game outcomes, excluded from feature table", "label_*": "current-game results, separate table", "source_coach_name": "retrospective schedule assignment, audit only"}}, indent=2))
    (output / "LICENSE.txt").write_text("PBP-derived team metrics: CC BY 4.0; nflverse/nflfastR contributors and underlying NFL source owners. Source: https://github.com/nflverse/nflverse-data ; license: https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md . Changes: bounded PBP aggregation, strictly earlier-date rolling styles, exact team/opponent joins. Schedule/context: Lee Sharpe and nflverse/nfldata contributors; separate upstream terms, no blanket third-party rights asserted. No FTN or participation data used.\n")
    print(json.dumps({key: summary[key] for key in ["team_game_rows", "games", "team_games_with_observed_plays", "team_games_without_pbp", "pbp_team_games_outside_schedule"]}), flush=True)


if __name__ == "__main__":
    main()
