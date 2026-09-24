"""Package datasets and model outputs on a hosted runner, without Git blobs."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
from runtime import require_github_hosted_runner

ROOT = Path(__file__).resolve().parents[1]


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["nba", "nfl"], required=True)
    args = parser.parse_args()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    sport = args.sport
    report = json.loads((ROOT / "reports" / (sport + "_evaluation.json")).read_text())
    schema = json.loads((ROOT / "data" / sport / "feature_schema.json").read_text())
    lines = ["# " + sport.upper() + " evaluation", "", "Model development uses games before January 1, 2025. Parameters use pre-2024 games; residual calibration uses 2024. Test games start January 1, 2025.", "", "This evaluates stat forecasts, not betting returns. There is no verified betting ROI.", "", "| Target | Fit appearances | Test appearances | Model MAE | Last-10 MAE | Improvement | 80% interval coverage |", "|---|---:|---:|---:|---:|---:|---:|"]
    for target, row in report["heads"].items():
        lines.append("| %s | %s | %s | %.3f | %.3f | %.1f%% | %.1f%% |" % (target, format(row["train_n"], ","), format(row["test"]["n"], ","), row["test"]["mae"], row["last10_baseline"]["mae"], row["mae_improvement_pct"], 100 * row["interval80_coverage"]))
    lines.extend(["", "Development rows: {:,}; holdout rows: {:,}; pregame features: {}.".format(schema["development_rows"], schema["holdout_rows"], len(schema["features"])), "", "Coverage is conditional on recorded appearances, sufficient prior history, valid outcomes, and documented target-specific NFL roles. The residual intervals are approximate. See MODEL_CARD and the JSON report for limitations."])
    overview = ROOT / "reports" / (sport + "_evaluation.md")
    overview.write_text("\n".join(lines) + "\n")
    with tarfile.open(dist / ("dataset-" + sport + ".tar.gz"), "w:gz") as archive:
        archive.add(ROOT / "data" / sport, arcname="data/" + sport)
    with tarfile.open(dist / ("model-" + sport + ".tar.gz"), "w:gz") as archive:
        archive.add(ROOT / "models" / (sport + ".joblib"), arcname="models/" + sport + ".joblib")
        for path in (ROOT / "reports").glob(sport + "_*"):
            archive.add(path, arcname="reports/" + path.name)
    # Small directly readable report assets keep metrics accessible without
    # downloading any model or dataset to the owner's machine.
    for path in [overview, ROOT / "reports" / (sport + "_evaluation.json"), ROOT / "data" / sport / "feature_schema.json"]:
        name = sport + "_feature_schema.json" if path.name == "feature_schema.json" else path.name
        (dist / name).write_bytes(path.read_bytes())
    manifest = {"sport": sport, "cutoff": "2025-01-01", "assets": []}
    for path in sorted(dist.glob("*")):
        if path.is_file() and path.suffix != ".log":
            manifest["assets"].append({"name": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (dist / (sport + "_release_manifest.json")).write_text(json.dumps(manifest, indent=2))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
