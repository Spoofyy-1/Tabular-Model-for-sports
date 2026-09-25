"""Cloud-only NBA team style measurements and strictly lagged matchup tables."""
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

REPO = "Spoofyy-1/Tabular-Model-for-sports"
TAG = "csv-36170200182-1"
ASSET = "csv-base-nba.tar.gz"
PINNED_SHA256 = "5642335016aca6e0d39d7cb1c7c9f74b989de74dd2fcb2217fed4046e1bef3b7"
MAX_BYTES = 500_000_000
WINDOWS = (5, 10, 20)
MIN_HISTORY_LAG_HOURS = 12
KEYS = ["game_id", "team_id", "opponent_id", "game_date", "season", "season_type", "is_home", "evaluation_split", "source_game_date_precision"]
COUNTS = ["team_score", "opponent_score", "field_goals_made", "field_goals_attempted", "threes", "threes_attempted", "free_throws_made", "free_throws_attempted", "offensive_rebounds", "defensive_rebounds", "rebounds", "assists", "steals", "blocks", "turnovers", "total_turnovers"]
METRICS = ["three_attempt_share", "free_throw_attempt_rate", "effective_field_goal_pct", "offensive_rebound_share", "defensive_rebound_share", "turnover_rate_proxy", "assist_per_made_field_goal", "possession_proxy_per_game", "points_per_100_possession_proxy", "opponent_three_attempt_share", "opponent_effective_field_goal_pct", "opponent_turnover_rate_proxy", "opponent_points_per_100_possession_proxy"]


def numeric(frame, name):
    return pd.to_numeric(frame[name], errors="coerce").astype("float64") if name in frame else pd.Series(np.nan, index=frame.index)


def ratio(numerator, denominator):
    return numerator / denominator.where(denominator.gt(0))


def split(dates, precision):
    result = pd.Series("date_unresolved", index=dates.index, dtype="string")
    result.loc[dates.lt(pd.Timestamp("2024-01-01", tz="UTC"))] = "fit_pre_2024"
    result.loc[dates.ge(pd.Timestamp("2024-01-01", tz="UTC")) & dates.lt(pd.Timestamp("2025-01-01", tz="UTC"))] = "calibration_2024"
    result.loc[dates.ge(pd.Timestamp("2025-01-01", tz="UTC"))] = "holdout_2025_plus"
    boundary = dates.dt.strftime("%Y-%m-%d").isin(["2023-12-31", "2024-01-01", "2024-12-31", "2025-01-01"])
    result.loc[boundary & precision.ne("source_timestamp")] = "date_precision_unresolved"
    return result


def profiles(raw, flagged_games=()):
    required = {"game_id", "team_id", "opponent_id", "game_date", "season", "season_type", "is_home", "team_score", "opponent_score"}
    if not required.issubset(raw):
        raise ValueError("Required normalized team fields missing: " + str(sorted(required - set(raw))))
    out = pd.DataFrame(index=raw.index)
    for name in ["game_id", "team_id", "opponent_id"]:
        out[name] = raw[name].astype("string")
    out["game_date"] = pd.to_datetime(raw.game_date, utc=True, errors="coerce")
    out["source_game_date_precision"] = np.where(raw.game_date.astype("string").str.contains(r"\d{2}:\d{2}", na=False), "source_timestamp", "source_calendar_date")
    out["season"] = numeric(raw, "season").astype("Int64")
    out["season_type"] = numeric(raw, "season_type").astype("Int64")
    out["is_home"] = raw.is_home.astype("string").str.lower().map({"true": True, "false": False, "1": True, "0": False}).astype("boolean")
    out["evaluation_split"] = split(out.game_date, out.source_game_date_precision)
    for name in COUNTS:
        out["out_" + name] = numeric(raw, name)
    out["profile_identity_valid"] = out[["game_id", "team_id", "opponent_id", "game_date", "season"]].notna().all(axis=1)
    out["profile_identity_valid"] &= out.team_id.ne(out.opponent_id).fillna(False)
    for name in ["game_id", "team_id", "opponent_id"]:
        out["profile_identity_valid"] &= out[name].str.len().gt(0).fillna(False)
    if out.loc[out.profile_identity_valid].duplicated(["game_id", "team_id"]).any():
        raise ValueError("Duplicate team-game keys; no arbitrary reconciliation")
    source_turnovers = out.out_total_turnovers.combine_first(out.out_turnovers)
    out["turnover_source_basis"] = np.where(out.out_total_turnovers.notna(), "total_turnovers", np.where(out.out_turnovers.notna(), "turnovers", "unavailable"))
    out["out_selected_turnovers"] = source_turnovers
    count_columns = ["out_" + name for name in COUNTS]
    bad_counts = ((out[count_columns] < 0) | (out[count_columns].mod(1).ne(0) & out[count_columns].notna())).any(axis=1)
    bad_shooting = out.out_field_goals_made.gt(out.out_field_goals_attempted) | out.out_threes.gt(out.out_threes_attempted) | out.out_threes_attempted.gt(out.out_field_goals_attempted) | out.out_free_throws_made.gt(out.out_free_throws_attempted)
    known_identity = out[["out_field_goals_made", "out_threes", "out_free_throws_made", "out_team_score"]].notna().all(axis=1)
    bad_identity = known_identity & out.out_team_score.ne(2 * out.out_field_goals_made + out.out_threes + out.out_free_throws_made)
    out["quality_invalid_box_counts"] = bad_counts | bad_shooting | bad_identity
    out["quality_source_reconciliation_excluded"] = out.game_id.isin(set(flagged_games))
    pair_fields = ["game_id", "team_id", "opponent_id", "game_date", "season", "out_team_score", "out_opponent_score", "out_offensive_rebounds", "out_defensive_rebounds", "profile_identity_valid"]
    opponent = out.loc[out.profile_identity_valid, pair_fields].rename(columns={name: "paired_" + name for name in pair_fields if name != "game_id"})
    out = out.merge(opponent, left_on=["game_id", "opponent_id"], right_on=["game_id", "paired_team_id"], how="left", validate="many_to_one")
    group_size = out.groupby("game_id", dropna=False).team_id.transform("size")
    out["quality_pair_valid"] = (group_size.eq(2) & out.profile_identity_valid & out.team_id.eq(out.paired_opponent_id)
        & out.game_date.eq(out.paired_game_date) & out.season.eq(out.paired_season)
        & out.out_team_score.eq(out.paired_out_opponent_score) & out.out_opponent_score.eq(out.paired_out_team_score)).fillna(False)
    out["outcome_final_score_valid"] = (out.quality_pair_valid & out.out_team_score.gt(0) & out.out_opponent_score.gt(0) & out.out_team_score.ne(out.out_opponent_score)).fillna(False)
    out["style_history_eligible"] = (out.outcome_final_score_valid & ~out.quality_invalid_box_counts & ~out.quality_source_reconciliation_excluded
        & out.source_game_date_precision.eq("source_timestamp") & out.season_type.isin([2, 3])).fillna(False)
    # Both sides of a game must pass its retrospective quality screen before
    # either side contributes historical metrics.
    paired_quality = out.groupby("game_id", dropna=False).style_history_eligible.transform("all")
    out["style_history_eligible"] &= paired_quality
    fga, fgm, fta, oreb = out.out_field_goals_attempted, out.out_field_goals_made, out.out_free_throws_attempted, out.out_offensive_rebounds
    possession = fga + 0.44 * fta - oreb + out.out_selected_turnovers
    out["out_three_attempt_share"] = ratio(out.out_threes_attempted, fga)
    out["out_free_throw_attempt_rate"] = ratio(fta, fga)
    out["out_effective_field_goal_pct"] = ratio(fgm + 0.5 * out.out_threes, fga)
    out["out_offensive_rebound_share"] = ratio(oreb, oreb + out.paired_out_defensive_rebounds)
    out["out_defensive_rebound_share"] = ratio(out.out_defensive_rebounds, out.out_defensive_rebounds + out.paired_out_offensive_rebounds)
    out["out_turnover_rate_proxy"] = ratio(out.out_selected_turnovers, fga + 0.44 * fta + out.out_selected_turnovers)
    out["out_assist_per_made_field_goal"] = ratio(out.out_assists, fgm)
    out["out_possession_proxy_per_game"] = possession.where(possession.gt(0))
    out["out_points_per_100_possession_proxy"] = 100 * ratio(out.out_team_score, possession)
    defense_metrics = ["three_attempt_share", "effective_field_goal_pct", "turnover_rate_proxy", "points_per_100_possession_proxy"]
    opposite_style = out.loc[out.profile_identity_valid, ["game_id", "team_id"] + ["out_" + name for name in defense_metrics]].rename(columns={"team_id": "opponent_id", **{"out_" + name: "out_opponent_" + name for name in defense_metrics}})
    out = out.merge(opposite_style, on=["game_id", "opponent_id"], how="left", validate="many_to_one")
    out = out.drop(columns=[name for name in out if name.startswith("paired_")])
    return out.sort_values(["game_date", "game_id", "team_id"], na_position="last").reset_index(drop=True)


def pregame_features(profile, windows=WINDOWS):
    """No current/future outcomes participate in the current feature record."""
    allowed = profile.profile_identity_valid & profile.season_type.isin([2, 3]) & profile.source_game_date_precision.eq("source_timestamp")
    source = profile.loc[allowed].sort_values(["team_id", "season", "game_date", "game_id"])
    rows = []
    for (_, _), team in source.groupby(["team_id", "season"], sort=False):
        team = team.reset_index(drop=True)
        history = team.loc[team.style_history_eligible].copy()
        history_times = history.game_date.astype("int64").to_numpy()
        listed_times = team.game_date.astype("int64").to_numpy()
        for _, current in team.iterrows():
            cutoff = min(current.game_date.floor("D"), current.game_date - pd.Timedelta(hours=MIN_HISTORY_LAG_HOURS))
            end = int(np.searchsorted(history_times, cutoff.value, side="left"))
            prior = history.iloc[:end]
            listed_end = int(np.searchsorted(listed_times, cutoff.value, side="left"))
            listed = team.iloc[:listed_end]
            row = {name: current[name] for name in KEYS}
            row.update({"pre_history_cutoff_utc": cutoff, "pre_eligible_history_games": end,
                "pre_history_latest_game_start_utc": prior.game_date.iloc[-1] if len(prior) else pd.NaT,
                "pre_previous_listed_game_start_utc": listed.game_date.iloc[-1] if len(listed) else pd.NaT,
                "pre_hours_since_previous_listed_game": (current.game_date - listed.game_date.iloc[-1]).total_seconds() / 3600 if len(listed) else np.nan,
                "pre_utc_calendar_rest_days": (current.game_date.floor("D") - listed.game_date.iloc[-1].floor("D")).days if len(listed) else np.nan,
                "historical_stat_publication_verified": False, "schedule_publication_time_verified": False,
                "automatic_training_join_allowed": False, "history_completion_lag_assumption_hours": MIN_HISTORY_LAG_HOURS})
            for window in windows:
                recent = prior.tail(window)
                row[f"pre_history_games_last{window}"] = len(recent)
                for metric in METRICS:
                    values = recent["out_" + metric]
                    row[f"pre_{metric}_mean_last{window}"] = values.mean() if len(values) else np.nan
                    row[f"pre_{metric}_observed_last{window}"] = int(values.notna().sum())
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["game_date", "game_id", "team_id"]).reset_index(drop=True)


def matchups(features):
    """One row per nominal home/away pairing with numeric style differences."""
    metadata = ["game_id", "game_date", "season", "season_type", "evaluation_split"]
    selected = ["team_id", "opponent_id"] + [name for name in features if name.startswith("pre_")]
    home = features.loc[features.is_home.eq(True).fillna(False), metadata + selected].copy()
    away = features.loc[features.is_home.eq(False).fillna(False), ["game_id"] + selected].copy()
    home = home.rename(columns={name: "home_" + name for name in selected})
    away = away.rename(columns={name: "away_" + name for name in selected})
    result = home.merge(away, on="game_id", how="inner", validate="one_to_one")
    reciprocal = result.home_team_id.eq(result.away_opponent_id) & result.away_team_id.eq(result.home_opponent_id)
    result = result.loc[reciprocal].copy()
    for name in selected:
        if name.startswith("pre_") and ("_mean_last" in name or name in {"pre_hours_since_previous_listed_game", "pre_utc_calendar_rest_days"}):
            result["difference_home_minus_away_" + name] = result["home_" + name] - result["away_" + name]
    for window in WINDOWS:
        for side, opponent in [("home", "away"), ("away", "home")]:
            off_share = f"{side}_pre_three_attempt_share_mean_last{window}"
            def_share = f"{opponent}_pre_opponent_three_attempt_share_mean_last{window}"
            off_eff = f"{side}_pre_points_per_100_possession_proxy_mean_last{window}"
            def_eff = f"{opponent}_pre_opponent_points_per_100_possession_proxy_mean_last{window}"
            if {off_share, def_share, off_eff, def_eff}.issubset(result):
                result[f"interaction_{side}_three_attempt_share_times_opponent_allowed_last{window}"] = result[off_share] * result[def_share]
                result[f"interaction_{side}_offense_minus_opponent_allowed_points_per100_last{window}"] = result[off_eff] - result[def_eff]
    result["automatic_training_join_allowed"] = False
    result["historical_stat_publication_verified"] = False
    return result.sort_values(["game_date", "game_id"]).reset_index(drop=True)


def labels(profile):
    result = profile[KEYS].copy()
    valid = profile.outcome_final_score_valid & ~profile.quality_source_reconciliation_excluded & ~profile.quality_invalid_box_counts
    valid &= valid.groupby(profile.game_id, dropna=False).transform("all")
    result["label_quality_eligible"] = valid
    result["label_team_win"] = profile.out_team_score.gt(profile.out_opponent_score).astype("boolean").where(valid)
    result["label_team_margin"] = (profile.out_team_score - profile.out_opponent_score).where(valid)
    result["label_total_points"] = (profile.out_team_score + profile.out_opponent_score).where(valid)
    result["label_team_points"] = profile.out_team_score.where(valid)
    result["label_opponent_points"] = profile.out_opponent_score.where(valid)
    return result


def bounded_json(url, limit=3_000_000):
    require_github_hosted_runner()
    with requests.get(url, stream=True, timeout=(15, 60)) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(64 * 1024):
            content.extend(chunk)
            if len(content) > limit:
                raise ValueError("Metadata exceeds bounded size")
    return json.loads(content)


def download(cache):
    require_github_hosted_runner()
    release = bounded_json(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}")
    assets = {item["name"]: item for item in release["assets"]}
    asset = assets[ASSET]
    if release["tag_name"] != TAG or not 0 < asset["size"] <= MAX_BYTES:
        raise ValueError("Source tag/size mismatch")
    manifest = bounded_json(assets["base-nba_csv_asset_manifest.json"]["browser_download_url"])
    expected = next(item for item in manifest["assets"] if item["name"] == ASSET)
    if expected["bytes"] != asset["size"] or expected["sha256"] != PINNED_SHA256:
        raise ValueError("Published source manifest mismatch")
    path = cache / ASSET
    digest, size = hashlib.sha256(), 0
    with requests.get(asset["browser_download_url"], stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        with path.open("wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > min(MAX_BYTES, asset["size"]):
                    raise ValueError("Source transfer exceeded declared bound")
                digest.update(chunk)
                stream.write(chunk)
    if size != asset["size"] or digest.hexdigest() != PINNED_SHA256 or asset.get("digest") not in (None, "sha256:" + PINNED_SHA256):
        raise ValueError("Source archive integrity failed")
    return path, {"release": TAG, "url": asset["browser_download_url"], "bytes": size, "sha256": digest.hexdigest(), "asset_updated_at": asset["updated_at"], "asset_manifest_url": assets["base-nba_csv_asset_manifest.json"]["browser_download_url"]}


def archive_content(tar, name, limit):
    member = tar.getmember(name)
    if not member.isfile() or member.size > limit:
        raise ValueError("Required member has unexpected type or size")
    return tar.extractfile(member).read()


def load_source(path, output):
    require_github_hosted_runner()
    with tarfile.open(path, "r:gz") as tar:
        seen = set()
        for member in tar.getmembers():
            parsed = PurePosixPath(member.name)
            if parsed.is_absolute() or ".." in parsed.parts or member.issym() or member.islnk() or member.name in seen:
                raise ValueError("Unsafe or duplicate source member")
            seen.add(member.name)
        manifest = json.loads(archive_content(tar, "csv_manifest.json", 3_000_000))
        table_name = "data/nba/team_games.csv.gz"
        declared = next(item for item in manifest["tables"] if item["file"] == table_name)
        table = archive_content(tar, table_name, 40_000_000)
        if len(table) != declared["bytes"] or hashlib.sha256(table).hexdigest() != declared["sha256"]:
            raise ValueError("Team CSV checksum mismatch")
        frame = pd.read_csv(io.BytesIO(table), compression="gzip", dtype="string", keep_default_na=False, na_values=[r"\N"])
        if len(frame) != declared["rows"]:
            raise ValueError("Team CSV row count mismatch")
        quality = json.loads(archive_content(tar, "data/nba/quality_report.json", 10_000_000))
        for basename in ["hoopR-nba-data-LICENSE.txt", "sportsdataverse-data-LICENSE.txt", "source_manifest.json", "DATA_DICTIONARY.md"]:
            content = archive_content(tar, "data/nba/" + basename, 10_000_000)
            (output / ("SOURCE_" + basename)).write_bytes(content)
    return frame, quality.get("flagged_game_ids", []), declared


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "columns": {name: str(dtype) for name, dtype in frame.dtypes.items()}, "missing": {name: int(value) for name, value in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    output = ROOT / "data/matchup/nba_teams"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nba-team-style-", dir=os.environ["RUNNER_TEMP"]) as cache:
        path, provenance = download(Path(cache))
        raw, flagged_games, table_provenance = load_source(path, output)
        profile = profiles(raw, flagged_games)
        features = pregame_features(profile)
        games = matchups(features)
        targets = labels(profile)
        tables = {name: write(frame, output / (name + ".csv.gz")) for name, frame in {
            "team_game_profiles": profile, "team_pregame_features": features, "game_matchups": games, "team_game_labels": targets}.items()}
        summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "sport": "NBA", "source": provenance, "source_team_table": table_provenance,
            "source_team_rows": len(raw), "profile_rows": len(profile), "eligible_history_team_games": int(profile.style_history_eligible.sum()),
            "source_quality_excluded_rows": int(profile.quality_source_reconciliation_excluded.sum()), "invalid_box_rows": int(profile.quality_invalid_box_counts.sum()),
            "invalid_pair_rows": int((~profile.quality_pair_valid).sum()), "pregame_rows": len(features), "matchup_games": len(games),
            "pregame_rows_with_ten_prior_games": int(features.pre_history_games_last10.ge(10).sum()),
            "evaluation_split_counts": features.evaluation_split.value_counts().to_dict(), "turnover_source_basis_counts": profile.turnover_source_basis.value_counts().to_dict(),
            "date_min": profile.game_date.min().isoformat(), "date_max": profile.game_date.max().isoformat(), "tables": tables,
            "license": "CC-BY-4.0 producer data; MIT distribution repository; original notices included",
            "model_training_performed": False, "history_windows": list(WINDOWS), "history_completion_lag_assumption_hours": MIN_HISTORY_LAG_HOURS,
            "limitations": ["Styles are rolling boxscore measurements, not proprietary play types, coach philosophies, or causal archetypes.",
                "Possession count is FGA+0.44*FTA-OREB+TOV, a coarse proxy with no overtime or 48-minute normalization.",
                "All history starts before the current UTC date and more than 12 hours before current start; historical publication times/end times are unverified.",
                "History resets each season and includes only regular/postseason games. Missing measurements retain nulls and observed-sample counts.",
                "Pregame rest describes intervals between source-listed prior games, not confirmed rest, travel, sleep or training load.",
                "Later source corrections and retrospective quality exclusions may differ from information available in real time.",
                "Sequential holdout rows can use strictly earlier holdout game results; no holdout fitting, threshold selection or normalization occurs.",
                "Current game scores and win targets live in separate labels/profiles; future outcomes never enter the current pregame feature rows.",
                "Injuries, coaching personnel, nutritionists, private routines and executable market prices are not invented or automatically joined."]}
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        (output / "schema.json").write_text(json.dumps({"csv_null_encoding": r"\N", "tables": tables,
            "pregame_feature_columns": [name for name in features if name.startswith("pre_")],
            "temporal_roles": {"out_*": "same-game retrospective measurement", "label_*": "same-game outcome, not pregame feature", "pre_*": "strictly earlier history or source schedule context, subject to explicit availability assumptions"}}, indent=2))
        print(json.dumps({name: summary[name] for name in ["source_team_rows", "eligible_history_team_games", "pregame_rows", "matchup_games", "evaluation_split_counts"]}), flush=True)


if __name__ == "__main__":
    main()
