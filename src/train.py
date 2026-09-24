"""Two sport-specific model bundles, fitted before a strict time cutoff."""
from pathlib import Path
import argparse
import json
import math
import warnings
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from runtime import require_github_hosted_runner

ROOT = Path(__file__).resolve().parents[1]
MODEL_TARGETS = {
    "nba": ["points", "rebounds", "assists", "threes"],
    "nfl": ["passing_yards", "passing_tds", "rushing_yards", "receiving_yards", "receptions"],
}
NFL_ROLES = {
    "passing_yards": {"QB"}, "passing_tds": {"QB"},
    "rushing_yards": {"QB", "RB", "FB", "WR"},
    "receiving_yards": {"RB", "FB", "WR", "TE"},
    "receptions": {"RB", "FB", "WR", "TE"},
}


def metrics(y, pred):
    err = np.asarray(y) - np.asarray(pred)
    return {"n": int(len(err)), "mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2))), "mean_error_actual_minus_prediction": float(np.mean(err))}


def scale_for(frame, target, fallback):
    return frame["f_" + target + "_std10"].fillna(fallback).clip(lower=max(0.5, fallback * 0.25)).to_numpy()


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=MODEL_TARGETS)
    parser.add_argument("--test-start", default="2025-01-01")
    parser.add_argument("--iterations", type=int, default=90)
    args = parser.parse_args()
    test_start = pd.Timestamp(args.test_start, tz="UTC")
    calibration_start = test_start - pd.DateOffset(years=1)
    d = ROOT / "data" / args.sport
    schema = json.loads((d / "feature_schema.json").read_text())
    if pd.Timestamp(schema["test_start"]) != test_start:
        raise ValueError("Feature split and training split differ; rebuild features with the same --test-start")
    frame = pd.concat([pd.read_parquet(d / "development.parquet"), pd.read_parquet(d / "holdout.parquet")], ignore_index=True)
    frame["game_date"] = pd.to_datetime(frame.game_date, utc=True)
    frame = frame.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    features = schema["features"]
    base_train = frame.game_date < calibration_start
    base_cal = (frame.game_date >= calibration_start) & (frame.game_date < test_start)
    base_test = frame.game_date >= test_start
    # A ten-appearance history is a declared coverage constraint, not an
    # outcome filter. Cold starts are preserved in the dataset, not scored.
    enough_history = frame.f_prior_appearances >= 10
    bundle = {"sport": args.sport, "features": features, "test_start": str(test_start), "calibration_start": str(calibration_start), "heads": {}, "cohort": "Player appearances with >=10 prior appearances, and target-specific NFL roles"}
    report = {"sport": args.sport, "test_start": str(test_start), "calibration_start": str(calibration_start), "model_fit_end_exclusive": str(calibration_start),
              "rows_all": len(frame), "feature_count": len(features), "rows_by_period": {"fit": int(base_train.sum()), "calibration": int(base_cal.sum()), "test": int(base_test.sum())},
              "heads": {}, "betting_backtest": "NOT RUN: verified timestamped historical lines and product-specific payouts are required", "test_policy": "Frozen parameters; lag features update using only earlier observed games. No retraining or threshold tuning on holdout."}
    prediction_parts = []
    with threadpool_limits(limits=3):
        for target in MODEL_TARGETS[args.sport]:
            target_col = "target_" + target
            if target_col not in frame:
                warnings.warn("Missing target " + target)
                continue
            eligible = enough_history & frame[target_col].notna() & frame["f_" + target + "_mean10"].notna()
            if args.sport == "nfl":
                eligible &= frame.position.isin(NFL_ROLES[target])
            train = frame.loc[base_train & eligible]
            cal = frame.loc[base_cal & eligible]
            test = frame.loc[base_test & eligible]
            if min(len(train), len(cal), len(test)) < 50:
                warnings.warn("Insufficient split for " + target)
                continue
            model = HistGradientBoostingRegressor(loss="squared_error", max_iter=args.iterations, max_leaf_nodes=15,
                learning_rate=0.07, min_samples_leaf=50, l2_regularization=5.0, max_bins=127,
                early_stopping=False, random_state=41)
            model.fit(train[features], train[target_col])
            nonnegative = not (args.sport == "nfl" and target.endswith("_yards"))
            cal_pred = model.predict(cal[features])
            test_pred = model.predict(test[features])
            if nonnegative:
                cal_pred = np.maximum(0, cal_pred)
                test_pred = np.maximum(0, test_pred)
            fallback = max(0.5, float(train[target_col].std()))
            residuals = np.sort((cal[target_col].to_numpy() - cal_pred) / scale_for(cal, target, fallback))
            low_q, high_q = np.quantile(residuals, [0.1, 0.9])
            scales = scale_for(test, target, fallback)
            lower = test_pred + low_q * scales
            if nonnegative:
                lower = np.maximum(0, lower)
            upper = np.maximum(lower, test_pred + high_q * scales)
            baseline = test["f_" + target + "_mean10"].to_numpy()
            result = {"train_n": len(train), "calibration_n": len(cal), "test": metrics(test[target_col], test_pred),
                      "cohort": sorted(NFL_ROLES[target]) if args.sport == "nfl" else "Observed NBA appearances",
                      "minimum_prior_appearances": 10,
                      "last10_baseline": metrics(test[target_col], baseline),
                      "interval80_coverage": float(np.mean((test[target_col] >= lower) & (test[target_col] <= upper))),
                      "interval80_mean_width": float(np.mean(upper - lower)),
                      "years": {str(year): metrics(part[target_col], test_pred[test.game_date.dt.year.to_numpy() == year]) for year, part in test.groupby(test.game_date.dt.year)}}
            result["mae_improvement_pct"] = 100 * (1 - result["test"]["mae"] / result["last10_baseline"]["mae"])
            report["heads"][target] = result
            bundle["heads"][target] = {"model": model, "residuals": residuals, "scale_fallback": fallback,
                "distribution_method": "Empirical standardized residuals from calibration year; continuous approximation, not calibrated against betting-line outcomes"}
            cols = [c for c in ["game_date", "game_id", "player_id", "player_name", "position", "team", "opponent"] if c in test]
            out = test[cols].copy()
            out["stat"] = target
            out["actual"] = test[target_col].to_numpy()
            out["prediction"] = test_pred
            out["last10_baseline"] = baseline
            out["interval80_low"] = lower
            out["interval80_high"] = upper
            prediction_parts.append(out)
            print(json.dumps({"sport": args.sport, "target": target, "train": len(train), "test": len(test), "mae": result["test"]["mae"], "baseline_mae": result["last10_baseline"]["mae"]}), flush=True)
    if not bundle["heads"]:
        raise RuntimeError("No target heads trained")
    (ROOT / "models").mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    joblib.dump(bundle, ROOT / "models" / (args.sport + ".joblib"), compress=3)
    (ROOT / "reports" / (args.sport + "_evaluation.json")).write_text(json.dumps(report, indent=2, allow_nan=False))
    pd.concat(prediction_parts, ignore_index=True).to_parquet(ROOT / "reports" / (args.sport + "_holdout_predictions.parquet"), index=False, compression="zstd")


if __name__ == "__main__":
    main()
