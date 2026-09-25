"""Publish the hosted NBA/NFL market pilot with hashes and an aggregate audit."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    require_github_hosted_runner()
    source = ROOT / "data/markets/sports_pilot"
    summary = json.loads((source / "summary.json").read_text())
    if summary.get("license") != "CC-BY-4.0":
        raise ValueError("Unexpected pilot license")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Unexpected source symlink")
        if path.is_file():
            files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)})
    archive = dist / "csv-nba-nfl-market-pilot.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        tar.add(source, arcname=source.relative_to(ROOT).as_posix())
    if archive.stat().st_size > 500_000_000:
        raise ValueError("Pilot output exceeds budget")
    (dist / "sports_market_asset_manifest.json").write_text(json.dumps({"asset": archive.name, "bytes": archive.stat().st_size,
        "sha256": sha(archive), "files": files, "license": "CC-BY-4.0"}, indent=2))
    shutil.copyfile(source / "summary.json", dist / "sports_market_summary.json")


if __name__ == "__main__":
    main()
