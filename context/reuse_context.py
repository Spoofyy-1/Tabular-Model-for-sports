"""Reuse a published context snapshot only when its collector sources match.

Remote-only: download and verify existing archives on the hosted runner, never
on a local workstation. Exit 2 means recollect instead. Original manifests and
retrieval timestamps remain unchanged, so reuse never claims a fresh snapshot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

REPO = "Spoofyy-1/Tabular-Model-for-sports"
COLLECTORS = {"nba": "nba_context.py", "nfl": "nfl_context.py", "odds": "odds_context.py", "matchups": "nba_matchups.py"}


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=COLLECTORS, required=True)
    parser.add_argument("--release-tag", required=True)
    args = parser.parse_args()
    if not args.release_tag:
        return 2
    response = requests.get(f"https://api.github.com/repos/{REPO}/releases/tags/{args.release_tag}", timeout=30)
    if response.status_code == 404:
        return 2
    response.raise_for_status()
    assets = {a["name"]: a for a in response.json()["assets"]}
    manifest_name = args.dataset + "_asset_manifest.json"
    if manifest_name not in assets:
        return 2
    manifest_response = requests.get(assets[manifest_name]["browser_download_url"], timeout=30)
    manifest_response.raise_for_status()
    manifest = manifest_response.json()
    commit = manifest["git_commit"]
    for name in ["context/" + COLLECTORS[args.dataset], "context/package_context.py", "src/runtime.py", "context_requirements.txt"]:
        old = requests.get(f"https://raw.githubusercontent.com/{REPO}/{commit}/{name}", timeout=30)
        old.raise_for_status()
        if hashlib.sha256(old.content).digest() != hashlib.sha256((ROOT / name).read_bytes()).digest():
            return 2
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    for expected in manifest["assets"]:
        name = expected["name"]
        if Path(name).name != name or name not in assets or expected["bytes"] > 1_900_000_000:
            raise ValueError("Invalid snapshot asset metadata")
        if assets[name]["size"] != expected["bytes"]:
            raise ValueError("Snapshot asset size mismatch")
        digest, count = hashlib.sha256(), 0
        with requests.get(assets[name]["browser_download_url"], stream=True, timeout=(20, 120)) as source:
            source.raise_for_status()
            with (dist / name).open("wb") as output:
                for chunk in source.iter_content(1024 * 1024):
                    count += len(chunk)
                    if count > expected["bytes"]:
                        raise ValueError("Snapshot exceeds declared size")
                    digest.update(chunk)
                    output.write(chunk)
        if count != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
            raise ValueError("Snapshot integrity check failed")
    (dist / manifest_name).write_bytes(manifest_response.content)
    (dist / (args.dataset + "_snapshot_reuse.json")).write_text(json.dumps({"source_release": args.release_tag, "source_commit": commit, "collector_sources_match": True, "all_asset_hashes_verified": True}, indent=2))
    print(json.dumps({"dataset": args.dataset, "reused_from": args.release_tag, "integrity_verified": True}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
