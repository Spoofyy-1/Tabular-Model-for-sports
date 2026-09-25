"""Hosted-only contextual CSV packaging; preserve each source's rights notes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=["nfl_profiles", "celebrity_context", "tennis_context", "officials_context"], required=True)
    args = parser.parse_args()
    source = ROOT / "data/extras" / args.dataset
    summary = json.loads((source / "summary.json").read_text())
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    inventory = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Unexpected source symlink")
        if path.is_file():
            inventory.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size,
                              "sha256": digest(path)})
    archive = output / ("csv-extra-" + args.dataset.replace("_", "-") + ".tar.gz")
    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        tar.add(source, arcname=source.relative_to(ROOT).as_posix())
    if archive.stat().st_size > 500_000_000:
        raise ValueError("Context pilot archive exceeds budget")
    manifest = {"dataset": args.dataset, "archive": archive.name, "bytes": archive.stat().st_size,
                "sha256": digest(archive), "files": inventory,
                "rights": "See each source's license and usage notes. This archive does not create additional rights."}
    (output / (args.dataset + "_asset_manifest.json")).write_text(json.dumps(manifest, indent=2))
    shutil.copyfile(source / "summary.json", output / (args.dataset + "_summary.json"))
    print(json.dumps({"archive": archive.name, "bytes": manifest["bytes"], "files": len(inventory)}), flush=True)


if __name__ == "__main__":
    main()
