"""Chronological, pregame features. Outcomes remain separate from model inputs."""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from runtime import require_github_hosted_runner

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "nba": ["points", "rebounds", "assists", "threes", "minutes", "steals", "blocks", "turnovers"],
    "nfl": ["passing_yards", "passing_tds", "completions", "attempts", "rushing_yards", "carries", "receiving_yards", "receptions", "targets"],
}
POSITIONS = ["PG", "SG", "SF", "PF", "C", "G", "F", "G-F", "F-C", "QB", "RB", "FB", "WR", "TE", "K", "P", "OTHER"]


def build_features(frame, sport):
    """Every outcome-derived feature is shifted before rolling/merging.

    Prediction time is immediately before scheduled kickoff/tipoff. Evaluation
    is conditional on appearing in the provider's player-game table, not on
    being on the roster. Do not interpret absent rows as zero outcomes.
    """
    df = frame.copy()
    if "boxscore_observed" in df:
        df = df.loc[df.boxscore_observed.fillna(False)].copy()
    df["game_date"] = pd.to_datetime(df["game_date"], utc=True)
    df["player_id"] = df["player_id"].astype(str)
    df["game_id"] = df["game_id"].astype(str)
    if df.duplicated(["game_id", "player_id"]).any():
        raise ValueError("Duplicate game/player keys")
    if df[["game_date", "team", "opponent"]].isna().any().any():
        raise ValueError("Missing dates or team identities")
    df = df.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)
    available = [x for x in TARGETS[sport] if x in df]
    if not available:
        raise ValueError("No canonical target columns found")
    for col in available:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # No target-game participation, minutes, injury outcome, attendance,
    # final score, closing odds, or current-season final aggregates in X.
    x = pd.DataFrame(index=df.index)
    x["is_home"] = pd.to_numeric(df.get("is_home", pd.Series(np.nan, index=df.index)), errors="coerce")
    x["calendar_month"] = df.game_date.dt.month
    x["calendar_year"] = df.game_date.dt.year
    if "season_type" in df:
        x["is_postseason"] = df.season_type.astype(str).str.upper().isin(["3", "5", "POST"]).astype(float)
    if "week" in df:
        x["schedule_week"] = pd.to_numeric(df["week"], errors="coerce")
    position = df.get("position", pd.Series("OTHER", index=df.index)).fillna("OTHER").astype(str).str.upper()
    position = position.where(position.isin(POSITIONS), "OTHER")
    # NBA source profile positions are not proven historical as-of metadata.
    if sport == "nfl":
        # Use the prior appearance's role, including for model cohort selection.
        # The source archive itself is retrospectively compiled; this is not
        # a claim to possess historical roster publication timestamps.
        position = position.groupby(df.player_id, sort=False).shift(1).fillna("OTHER")
        df["position"] = position
        for p in POSITIONS:
            x["position_" + p.replace("-", "_")] = (position == p).astype(float)
    groups = df.groupby("player_id", sort=False)
    x["prior_appearances"] = groups.cumcount()
    prev_date = groups.game_date.shift(1)
    x["days_since_last_appearance"] = (df.game_date - prev_date).dt.total_seconds().div(86400).clip(upper=180)
    # Date-only sources cannot provide precise hours. This flag uses dates.
    days = (df.game_date.dt.normalize() - prev_date.dt.normalize()).dt.days
    x["appeared_previous_calendar_day"] = (days == 1).astype(float)
    for n in [2, 3, 5]:
        prior_date = groups.game_date.shift(n)
        x["days_since_appearance_%d" % n] = (df.game_date - prior_date).dt.total_seconds().div(86400).clip(upper=365)
    for stat in available:
        lag = groups[stat].shift(1)
        x[stat + "_lag1"] = lag
        lg = lag.groupby(df.player_id, sort=False)
        for window in [3, 10, 20]:
            x[stat + "_mean%d" % window] = lg.transform(lambda v: v.rolling(window, min_periods=1).mean())
        x[stat + "_std10"] = lg.transform(lambda v: v.rolling(10, min_periods=3).std())
    # Compute what each defense allowed in earlier games. Current-game
    # sums are labels for the *next* game; they are shifted before joining.
    contextual = [s for s in available if s not in {"minutes", "steals", "blocks", "turnovers", "targets", "carries", "attempts", "completions"}]
    allowed = df.groupby(["game_id", "opponent", "game_date"], observed=True)[contextual].sum(min_count=1).reset_index()
    allowed = allowed.sort_values(["opponent", "game_date", "game_id"])
    context_cols = []
    for stat in contextual:
        prior = allowed.groupby("opponent", sort=False)[stat].shift(1)
        name = "opponent_allowed_" + stat + "_mean10"
        allowed[name] = prior.groupby(allowed.opponent, sort=False).transform(lambda v: v.rolling(10, min_periods=1).mean())
        context_cols.append(name)
    allowed["opponent_prior_games"] = allowed.groupby("opponent", sort=False).cumcount()
    context_cols.append("opponent_prior_games")
    joined = df[["game_id", "opponent"]].merge(allowed[["game_id", "opponent"] + context_cols], on=["game_id", "opponent"], how="left", validate="many_to_one")
    for name in context_cols:
        x[name] = joined[name].to_numpy()
    x = x.replace([np.inf, -np.inf], np.nan).astype("float32")
    metadata = [k for k in ["sport", "game_id", "player_id", "player_name", "team", "opponent", "game_date", "season", "week", "position"] if k in df]
    result = pd.concat([df[metadata], x.add_prefix("f_"), df[available].add_prefix("target_")], axis=1)
    return result, list(x.add_prefix("f_").columns), available


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=TARGETS)
    parser.add_argument("--test-start", default="2025-01-01")
    args = parser.parse_args()
    directory = ROOT / "data" / args.sport
    frame = pd.read_parquet(directory / "player_games.parquet")
    raw_count = len(frame)
    exclusions = {}
    if args.sport == "nba":
        quality = json.loads((directory / "quality_report.json").read_text())
        bad = set(quality.get("flagged_game_ids", []))
        frame = frame.loc[~frame.game_id.astype(str).isin(bad)].copy()
        exclusions["unreconciled_game_rows"] = raw_count - len(frame)
    if args.sport == "nfl":
        valid_era = frame.season >= 2001
        exclusions["legacy_1999_2000_rows"] = int((~valid_era).sum())
        frame = frame.loc[valid_era].copy()
    result, features, targets = build_features(frame, args.sport)
    cutoff = pd.Timestamp(args.test_start, tz="UTC")
    development = result.game_date < cutoff
    result.loc[development].to_parquet(directory / "development.parquet", index=False, compression="zstd")
    result.loc[~development].to_parquet(directory / "holdout.parquet", index=False, compression="zstd")
    schema = {"sport": args.sport, "rows": len(result), "features": features, "targets": targets,
              "raw_rows": raw_count, "exclusions": exclusions,
              "test_start": str(cutoff), "development_rows": int(development.sum()), "holdout_rows": int((~development).sum()),
              "prediction_time": "Immediately before scheduled start; some source dates have date-only precision",
              "cohort": "Observed player-game appearances; participation is not predicted",
              "position_policy": "NBA position excluded from features; NFL features and role cohorts use the prior recorded appearance's position",
              "leakage_exclusions": ["target-game statistics", "target-game snaps", "final season statistics", "unversioned injuries", "actual attendance", "closing prices after prediction time"]}
    (directory / "feature_schema.json").write_text(json.dumps(schema, indent=2))
    print(json.dumps({"sport": args.sport, "rows": len(result), "features": len(features), "targets": targets}))


if __name__ == "__main__":
    main()
