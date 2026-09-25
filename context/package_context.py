"""Publish a separate, auditable context-data snapshot without model retraining."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, choices=["nba", "nfl", "odds", "matchups"])
    args = parser.parse_args()
    data = ROOT / "data/context" / args.dataset
    summary = data / "context_summary.json"
    json.loads(summary.read_text())
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive_path = dist / ("context-" + args.dataset + ".tar.gz")
    with tarfile.open(archive_path, "w:gz", compresslevel=1) as archive:
        archive.add(data, arcname="data/context/" + args.dataset)
    if archive_path.stat().st_size >= 1_900_000_000:
        raise RuntimeError("Asset exceeds the declared publication size budget")
    published_summary = dist / (args.dataset + "_context_summary.json")
    published_summary.write_bytes(summary.read_bytes())
    manifest = {"dataset": args.dataset, "built_at_utc": datetime.now(timezone.utc).isoformat(),
                "git_commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
                "model_retraining": False, "assets": [{"name": p.name, "bytes": p.stat().st_size, "sha256": digest(p)} for p in [archive_path, published_summary]]}
    (dist / (args.dataset + "_asset_manifest.json")).write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"dataset": args.dataset, "archive_bytes": archive_path.stat().st_size, "status": "packaged", "model_retraining": False}), flush=True)


if __name__ == "__main__":
    main()
