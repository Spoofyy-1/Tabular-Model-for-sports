"""Frozen tabular experiment; real data/training only on Azure A100 jobs.

Importing this module performs no network, file, GPU or training operations.
The user explicitly authorized Azure execution in addition to hosted ingestion.
"""
import argparse
import csv
import ctypes
import gc
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import quote, urlparse
import warnings

import numpy as np
import pandas as pd

FIT_END = pd.Timestamp("2024-01-01T00:00:00Z")
TEST_START = pd.Timestamp("2025-01-01T00:00:00Z")
XGB_VERSION = "3.2.0"
ROUNDS = 180
PARAMS = {"objective": "reg:squarederror", "tree_method": "hist",
          "device": "cuda:0", "max_depth": 4, "eta": 0.05,
          "min_child_weight": 50, "lambda": 5.0, "max_bin": 128,
          "subsample": 1.0, "colsample_bytree": 1.0,
          "seed": 41, "nthread": 4, "verbosity": 1}
TARGETS = {"nba": ["points", "rebounds", "assists", "threes"],
           "nfl": ["passing_yards", "passing_tds", "rushing_yards", "receiving_yards", "receptions"]}
NFL_ROLES = {"passing_yards": {"QB"}, "passing_tds": {"QB"},
             "rushing_yards": {"QB", "RB", "FB", "WR"},
             "receiving_yards": {"RB", "FB", "WR", "TE"},
             "receptions": {"RB", "FB", "WR", "TE"}}
META = ["sport", "game_date", "game_id", "player_id", "player_name", "position", "team", "opponent"]
MAX_ROWS = 2_000_000
MAX_ASSET_BYTES = 1_000_000_000
MAX_TOTAL_BYTES = 3_000_000_000
MAX_MEMBER_BYTES = 2_000_000_000


def require_azure_job():
    """Reject owner machine/self-hosted substitutes before data I/O.

    Runtime flags are a guard, not cryptographic attestation. Deployment must
    supply Azure's real built-in variables; never set them to bypass this.
    """
    if platform.system() != "Linux" or not all(os.environ.get(k) for k in
            ["CONTAINER_APP_JOB_NAME", "CONTAINER_APP_JOB_EXECUTION_NAME"]):
        raise RuntimeError("Real data and GPU training require an actual Azure Container Apps Job")
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        raise RuntimeError("Training is authorized on the existing Azure A100, not GitHub runners")


def check_cuda_a100():
    require_azure_job()
    result = subprocess.run(["nvidia-smi", "--query-gpu=name,uuid,driver_version,memory.total",
                             "--format=csv,noheader,nounits"], capture_output=True, text=True,
                            timeout=20, check=True)
    inventory = list(csv.reader(io.StringIO(result.stdout)))
    if not inventory or not any("A100" in row[0] for row in inventory):
        raise RuntimeError("nvidia-smi did not identify an A100 GPU")
    driver = ctypes.CDLL("libcuda.so.1")
    def cuda_call(name, *args):
        status = getattr(driver, name)(*args)
        if status != 0:
            raise RuntimeError("CUDA driver call %s failed: %s" % (name, status))
    cuda_call("cuInit", 0)
    count, version, device = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    cuda_call("cuDeviceGetCount", ctypes.byref(count))
    cuda_call("cuDriverGetVersion", ctypes.byref(version))
    if count.value < 1 or version.value < 12000:
        raise RuntimeError("CUDA 12+ driver and a visible GPU are required")
    cuda_call("cuDeviceGet", ctypes.byref(device), 0)
    name = ctypes.create_string_buffer(256)
    cuda_call("cuDeviceGetName", name, 256, device)
    major, minor = ctypes.c_int(), ctypes.c_int()
    cuda_call("cuDeviceComputeCapability", ctypes.byref(major), ctypes.byref(minor), device)
    if "A100" not in name.value.decode() or major.value != 8 or minor.value != 0:
        raise RuntimeError("CUDA device 0 must be the authorized A100 (compute capability 8.0)")
    return {"gpu_name": name.value.decode(), "compute_capability": "%d.%d" % (major.value, minor.value),
            "cuda_driver_version": version.value, "visible_gpu_count": count.value,
            "nvidia_smi": [{"name": r[0].strip(), "uuid": r[1].strip(),
                            "driver": r[2].strip(), "memory_mib": r[3].strip()} for r in inventory]}


def verify_gpu_config(config):
    """Inspect the fitted runtime configuration, not just requested params."""
    obj = json.loads(config) if isinstance(config, str) else config
    learner = obj.get("learner", {})
    device = learner.get("generic_param", {}).get("device", "")
    booster = learner.get("gradient_booster", {})
    tree = booster.get("gbtree_train_param", {})
    serialized = json.dumps(booster, sort_keys=True)
    if device != "cuda:0" or tree.get("tree_method") != "hist" or "grow_gpu_hist" not in serialized:
        raise RuntimeError("GPU training not verified; device=%r, tree_method=%r" % (device, tree.get("tree_method")))
    return {"device": device, "tree_method": tree["tree_method"], "gpu_hist_updater_verified": True}


def strict_train(xgb, matrix, rounds):
    # XGBoost can warn and silently switch to CPU when CUDA is unavailable.
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=".*(No visible GPU|not compiled with CUDA|Falling back|falling back).*")
        model = xgb.train(dict(PARAMS), matrix, num_boost_round=rounds)
    verify_gpu_config(model.save_config())
    return model


def gpu_smoke(xgb):
    if xgb.__version__ != XGB_VERSION:
        raise RuntimeError("Expected frozen XGBoost %s, found %s" % (XGB_VERSION, xgb.__version__))
    build = xgb.build_info()
    if build.get("USE_CUDA") is not True:
        raise RuntimeError("XGBoost is not a CUDA build")
    rng = np.random.default_rng(41)
    x = rng.normal(size=(512, 8)).astype("float32")
    y = (x[:, 0] * 2 + x[:, 1]).astype("float32")
    matrix = xgb.DMatrix(x, label=y, nthread=4)
    model = strict_train(xgb, matrix, 3)
    prediction = model.predict(matrix)
    if len(prediction) != 512 or not np.isfinite(prediction).all():
        raise RuntimeError("GPU smoke prediction failed")
    return {"status": "passed", "synthetic_rows": 512, "rounds": 3,
            "xgboost_version": xgb.__version__, "build": build,
            "runtime": verify_gpu_config(model.save_config())}


def validate_schema(schema, sport):
    features = schema.get("features", [])
    if schema.get("sport") != sport or pd.Timestamp(schema.get("test_start")) != TEST_START:
        raise ValueError("Source sport or temporal cutoff does not match the frozen experiment")
    if not isinstance(features, list) or not 1 <= len(features) <= 256 or len(set(features)) != len(features):
        raise ValueError("Invalid feature schema")
    if any(not isinstance(f, str) or not f.startswith("f_") or "target_" in f for f in features):
        raise ValueError("Only source-schema pregame f_ features are permitted")
    required = ["f_prior_appearances"] + ["f_" + t + s for t in TARGETS[sport] for s in ["_mean10", "_std10"]]
    if not set(required).issubset(features) or not set(TARGETS[sport]).issubset(schema.get("targets", [])):
        raise ValueError("Source is missing required targets or baseline/scale features")
    return features


def prepare_frame(frame, schema, sport, partition):
    features = validate_schema(schema, sport)
    needed = ["game_date", "game_id", "player_id"] + features + ["target_" + t for t in TARGETS[sport]]
    if sport == "nfl":
        needed.append("position")
    if not set(needed).issubset(frame.columns) or not 1 <= len(frame) <= MAX_ROWS:
        raise ValueError("Missing required columns or unsupported row count")
    frame = frame.copy()
    if frame[["game_id", "player_id", "game_date"]].isna().any().any():
        raise ValueError("Missing game/player/date identity")
    if not pd.api.types.is_datetime64_any_dtype(frame.game_date):
        if not frame.game_date.astype(str).str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True).all():
            raise ValueError("Source event times must include an explicit timezone")
    elif getattr(frame.game_date.dt, "tz", None) is None:
        raise ValueError("Naive event times are not accepted")
    frame["game_date"] = pd.to_datetime(frame.game_date, utc=True, errors="raise")
    for col in ["game_id", "player_id"]:
        frame[col] = frame[col].astype("string")
        if frame[col].str.strip().eq("").any():
            raise ValueError("Blank identity")
    if frame.duplicated(["game_id", "player_id"]).any():
        raise ValueError("Duplicate game/player keys")
    if "sport" in frame and (frame.sport.isna().any() or not frame.sport.astype(str).str.strip().str.lower().eq(sport).all()):
        raise ValueError("Mixed sport source")
    if partition == "development":
        valid = frame.game_date < TEST_START
    elif partition == "holdout":
        valid = frame.game_date >= TEST_START
    else:
        raise ValueError("Unknown partition")
    if not valid.all():
        raise ValueError("Rows cross the declared temporal partition")
    for col in features + ["target_" + t for t in TARGETS[sport]]:
        frame[col] = pd.to_numeric(frame[col], errors="raise").astype("float32")
        if np.isinf(frame[col].to_numpy()).any():
            raise ValueError("Infinite numeric values")
    return frame.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)


def eligible_mask(frame, sport, target):
    valid = ((frame.f_prior_appearances >= 10) & frame["target_" + target].notna()
             & frame["f_" + target + "_mean10"].notna())
    if sport == "nfl":
        valid &= frame.position.isin(NFL_ROLES[target])
    return valid


def periods(frame):
    return {"fit": frame.game_date < FIT_END,
            "calibration": (frame.game_date >= FIT_END) & (frame.game_date < TEST_START),
            "test": frame.game_date >= TEST_START}


def scale_for(frame, target, fallback):
    return frame["f_" + target + "_std10"].fillna(fallback).clip(lower=max(0.5, fallback * 0.25)).to_numpy()


def nonnegative(sport, target):
    return not (sport == "nfl" and target.endswith("_yards"))


def metrics(y, prediction):
    error = np.asarray(y, dtype="float64") - np.asarray(prediction, dtype="float64")
    if not len(error) or not np.isfinite(error).all():
        raise ValueError("Metrics require finite nonempty labels and predictions")
    return {"n": int(len(error)), "mae": float(np.mean(np.abs(error))),
            "rmse": float(np.sqrt(np.mean(error ** 2))),
            "mean_error_actual_minus_prediction": float(error.mean())}


def evaluate(y, prediction, baseline, lower, upper):
    result = {"model": metrics(y, prediction), "last10_baseline": metrics(y, baseline),
              "interval80_coverage": float(np.mean((y >= lower) & (y <= upper))),
              "interval80_mean_width": float(np.mean(upper - lower))}
    base = result["last10_baseline"]["mae"]
    result["mae_improvement_pct"] = 100 * (1 - result["model"]["mae"] / base) if base else None
    return result


def json_write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


class ReleaseClient:
    def __init__(self, repo, tag, directory):
        require_azure_job()
        import requests
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Invalid GitHub repository")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "sports-props-azure-research/1"
        self.directory = directory
        self.total = 0
        self.sources = []
        self.repo = repo
        url = "https://api.github.com/repos/%s/releases/tags/%s" % (repo, quote(tag, safe=""))
        response = self.session.get(url, timeout=45)
        response.raise_for_status()
        release = response.json()
        if release.get("tag_name") != tag or release.get("draft"):
            raise ValueError("Release identity mismatch")
        self.release = {k: release.get(k) for k in ["id", "tag_name", "target_commitish", "published_at", "html_url"]}
        self.assets = {a["name"]: a for a in release["assets"]}

    def asset(self, name):
        require_azure_job()
        a = self.assets[name]
        if not 0 < a["size"] <= MAX_ASSET_BYTES or self.total + a["size"] > MAX_TOTAL_BYTES:
            raise ValueError("Release asset exceeds download budget")
        url = a["browser_download_url"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.startswith("/" + self.repo + "/releases/download/"):
            raise ValueError("Unexpected release download URL")
        path = self.directory / name
        digest = hashlib.sha256()
        count = 0
        with self.session.get(url, stream=True, timeout=(20, 120)) as response:
            response.raise_for_status()
            with path.open("wb") as output:
                for block in response.iter_content(1024 * 1024):
                    count += len(block)
                    self.total += len(block)
                    if count > a["size"] or self.total > MAX_TOTAL_BYTES:
                        raise ValueError("Actual download exceeds declared budget")
                    digest.update(block)
                    output.write(block)
        actual = digest.hexdigest()
        if count != a["size"] or ((a.get("digest") or "").startswith("sha256:") and a["digest"] != "sha256:" + actual):
            raise ValueError("Asset length/digest mismatch")
        self.sources.append({"asset": name, "asset_id": a["id"], "url": url, "bytes": count,
                             "sha256": actual, "github_digest": a.get("digest"), "updated_at": a.get("updated_at")})
        return path


def copy_archive_members(archive, sport, dest, csv_mode):
    """Copy only known regular files; never extract paths from an archive."""
    require_azure_job()
    suffix = ".csv.gz" if csv_mode else ".parquet"
    wanted = {"data/%s/%s" % (sport, name): dest / name for name in
              ["feature_schema.json", "development" + suffix, "holdout" + suffix]}
    found = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name not in wanted:
                continue
            if member.name in found or not member.isfile() or member.size > MAX_MEMBER_BYTES:
                raise ValueError("Invalid or duplicate required archive member")
            found.add(member.name)
            with tar.extractfile(member) as source, wanted[member.name].open("wb") as out:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
    if found != set(wanted):
        raise ValueError("Archive is missing required feature members")


def load_partition(directory, schema, sport, part, csv_mode):
    require_azure_job()
    if csv_mode:
        dtype = {f: "float32" for f in schema["features"]}
        dtype.update({"target_" + t: "float32" for t in schema["targets"]})
        dtype.update({k: "string" for k in META})
        # Null sentinel is the CSV export contract; preserve IDs including zeros.
        frame = pd.read_csv(directory / (part + ".csv.gz"), dtype=dtype,
                            keep_default_na=False, na_values=[r"\N"], low_memory=False)
    else:
        frame = pd.read_parquet(directory / (part + ".parquet"))
    expected = schema.get(part + "_rows")
    if expected is not None and len(frame) != int(expected):
        raise ValueError("CSV/Parquet row count disagrees with feature schema")
    return prepare_frame(frame, schema, sport, part)


def predict(xgb, model, frame, features, sport, target):
    result = model.predict(xgb.DMatrix(frame[features], nthread=4))
    if nonnegative(sport, target):
        result = np.maximum(0, result)
    if not np.isfinite(result).all():
        raise RuntimeError("Nonfinite model predictions")
    verify_gpu_config(model.save_config())
    return result


def train_sport(xgb, client, sport, output, temporary):
    require_azure_job()
    csv_name = "csv-base-%s.tar.gz" % sport
    csv_mode = csv_name in client.assets
    asset = csv_name if csv_mode else "dataset-%s.tar.gz" % sport
    if asset not in client.assets:
        raise ValueError("Missing source feature archive for " + sport)
    directory = temporary / sport
    directory.mkdir()
    archive = client.asset(asset)
    copy_archive_members(archive, sport, directory, csv_mode)
    archive.unlink()
    schema = json.loads((directory / "feature_schema.json").read_text())
    features = validate_schema(schema, sport)
    json_write(output / (sport + "_feature_schema.json"), schema)
    development = load_partition(directory, schema, sport, "development", csv_mode)
    splits = periods(development)
    fitted = {}
    report = {"sport": sport, "source_asset": asset, "format": "CSV" if csv_mode else "Parquet",
              "development_rows": len(development), "features": len(features), "heads": {}}
    for target in TARGETS[sport]:
        valid = eligible_mask(development, sport, target)
        fit = development.loc[splits["fit"] & valid]
        cal = development.loc[splits["calibration"] & valid]
        if min(len(fit), len(cal)) < 50:
            raise RuntimeError("Insufficient development split for " + sport + "/" + target)
        started = time.monotonic()
        matrix = xgb.DMatrix(fit[features], label=fit["target_" + target], nthread=4)
        model = strict_train(xgb, matrix, ROUNDS)
        elapsed = time.monotonic() - started
        del matrix
        cal_prediction = predict(xgb, model, cal, features, sport, target)
        fallback = max(0.5, float(fit["target_" + target].std()))
        residuals = np.sort((cal["target_" + target].to_numpy() - cal_prediction) / scale_for(cal, target, fallback))
        quantiles = np.quantile(residuals, [0.1, 0.9])
        model.save_model(output / (sport + "_" + target + ".json"))
        config = json.loads(model.save_config())
        json_write(output / (sport + "_" + target + "_config.json"), config)
        calibration = {"period": [str(FIT_END), str(TEST_START)], "n": len(cal),
                       "scale_fallback_from_fit": fallback, "q10": float(quantiles[0]), "q90": float(quantiles[1]),
                       "method": "Empirical standardized 2024 residual quantiles; not betting-line probabilities"}
        json_write(output / (sport + "_" + target + "_calibration.json"), calibration)
        pd.DataFrame({"standardized_residual": residuals}).to_csv(
            output / (sport + "_" + target + "_calibration_residuals.csv.gz"), index=False,
            compression={"method": "gzip", "mtime": 0})
        fitted[target] = (model, fallback, quantiles)
        report["heads"][target] = {"fit_n": len(fit), "calibration_n": len(cal),
                                   "fit_seconds": elapsed, "gpu": verify_gpu_config(config),
                                   "calibration": metrics(cal["target_" + target], cal_prediction),
                                   "cohort": sorted(NFL_ROLES[target]) if sport == "nfl" else "Recorded NBA appearances"}
        print(json.dumps({"stage": "fitted", "sport": sport, "target": target,
                          "fit_n": len(fit), "calibration_n": len(cal), "gpu": "cuda:0"}), flush=True)
        del fit, cal
        gc.collect()
    # All heads and residual calibrators are frozen before loading test rows.
    del development
    gc.collect()
    test_all = load_partition(directory, schema, sport, "holdout", csv_mode)
    report["holdout_rows"] = len(test_all)
    prediction_path = output / (sport + "_predictions.csv.gz")
    with gzip.open(prediction_path, "wt", encoding="utf-8", newline="") as predictions_file:
        for idx, target in enumerate(TARGETS[sport]):
            test = test_all.loc[eligible_mask(test_all, sport, target)]
            if len(test) < 50:
                raise RuntimeError("Insufficient benchmark split for " + sport + "/" + target)
            model, fallback, quantiles = fitted[target]
            prediction = predict(xgb, model, test, features, sport, target)
            scale = scale_for(test, target, fallback)
            lower = prediction + quantiles[0] * scale
            if nonnegative(sport, target):
                lower = np.maximum(0, lower)
            upper = np.maximum(lower, prediction + quantiles[1] * scale)
            actual = test["target_" + target].to_numpy()
            baseline = test["f_" + target + "_mean10"].to_numpy()
            measured = evaluate(actual, prediction, baseline, lower, upper)
            measured["years"] = {str(y): metrics(actual[test.game_date.dt.year.to_numpy() == y],
                                                     prediction[test.game_date.dt.year.to_numpy() == y])
                                  for y in sorted(test.game_date.dt.year.unique())}
            report["heads"][target]["benchmark"] = measured
            out = test[[c for c in META if c in test]].copy()
            out["stat"] = target
            out["actual"], out["prediction"], out["last10_baseline"] = actual, prediction, baseline
            out["interval80_low"], out["interval80_high"] = lower, upper
            out.to_csv(predictions_file, header=idx == 0, index=False, na_rep=r"\N")
            print(json.dumps({"stage": "evaluated", "sport": sport, "target": target,
                              "test_n": len(test), "mae": measured["model"]["mae"],
                              "baseline_mae": measured["last10_baseline"]["mae"]}), flush=True)
    json_write(output / (sport + "_evaluation.json"), report)
    return report


def frozen_experiment():
    return {"experiment": "a100_xgboost_baseline_features_v1", "parameters": dict(PARAMS),
            "boosting_rounds": ROUNDS, "xgboost_version": XGB_VERSION,
            "fit_end_exclusive": str(FIT_END), "calibration_end_exclusive": str(TEST_START),
            "test_start_inclusive": str(TEST_START), "targets": TARGETS,
            "minimum_prior_appearances": 10, "minimum_split_rows": 50,
            "early_stopping": False, "holdout_tuning": False,
            "context_features_added": False,
            "benchmark_status": "2025+ was already evaluated in the earlier baseline; reused benchmark, not an untouched external test",
            "cohort": "Observed appearances only; missing participation rows are not zero outcomes",
            "betting_roi": "Not evaluated; no verified executable quotes, settlement simulation or fees"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-release", required=True)
    parser.add_argument("--repo", default="Spoofyy-1/Tabular-Model-for-sports")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sport", choices=["both", "nba", "nfl"], default="both")
    args = parser.parse_args()
    require_azure_job()
    hardware = check_cuda_a100()
    import xgboost as xgb
    smoke = gpu_smoke(xgb)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("Use an empty output directory for an immutable experiment run")
    experiment = frozen_experiment()
    experiment["source_repository"] = args.repo
    experiment["source_release"] = args.data_release
    experiment["trainer_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    json_write(output / "experiment.json", experiment)
    json_write(output / "gpu_smoke.json", {"hardware": hardware, "smoke": smoke})
    status = {"status": "running", "job": os.environ["CONTAINER_APP_JOB_NAME"],
              "execution": os.environ["CONTAINER_APP_JOB_EXECUTION_NAME"],
              "data_release": args.data_release, "started_at": pd.Timestamp.now(tz="UTC").isoformat()}
    json_write(output / "run_status.json", status)
    try:
        reports = {}
        with tempfile.TemporaryDirectory(prefix="sports-a100-") as temp:
            temporary = Path(temp)
            client = ReleaseClient(args.repo, args.data_release, temporary)
            for sport in (["nba", "nfl"] if args.sport == "both" else [args.sport]):
                reports[sport] = train_sport(xgb, client, sport, output, temporary)
                gc.collect()
            expected = sum(len(TARGETS[s]) for s in reports)
            if sum(len(r["heads"]) for r in reports.values()) != expected:
                raise RuntimeError("Not every requested target completed")
            files = []
            for path in sorted(output.iterdir()):
                if path.is_file() and path.name != "run_status.json":
                    files.append({"name": path.name, "bytes": path.stat().st_size,
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            json_write(output / "manifest.json", {"release": client.release, "sources": client.sources,
                       "total_download_bytes": client.total, "artifacts": files,
                       "completed_heads": expected, "gpu_fit_verified": True,
                       "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__})
        status.update(status="complete", completed_heads=expected)
    except Exception as exc:
        status.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
        raise
    finally:
        status["finished_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        json_write(output / "run_status.json", status)
    print(json.dumps({"status": "complete", "completed_heads": expected,
                      "gpu": hardware["gpu_name"], "betting_roi": "not_evaluated"}), flush=True)


if __name__ == "__main__":
    main()
