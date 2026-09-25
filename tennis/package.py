"""Package licensed tennis tables on a GitHub-hosted runner only."""
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
    parser.add_argument("--dataset", choices=["points", "markets"], required=True)
    args = parser.parse_args()
    folder, report = {"points": ("mcp_research_only", "data_summary.json"),
                      "markets": ("pmxt_market_pilot", "market_summary.json")}[args.dataset]
    source = ROOT / "data/tennis" / folder
    summary = json.loads((source / report).read_text())
    if args.dataset == "points" and (summary.get("point_rows", 0) <= 0 or summary.get("research_only_noncommercial") is not True):
        raise ValueError("Point package requires positive coverage and explicit noncommercial marking")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Unexpected source symlink")
        if path.is_file():
            files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size,
                          "sha256": digest(path)})
    archive = output / ("csv-tennis-" + folder.replace("_", "-") + ".tar.gz")
    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        tar.add(source, arcname=source.relative_to(ROOT).as_posix())
    if archive.stat().st_size >= 1_900_000_000:
        raise RuntimeError("Tennis archive exceeds release budget")
    manifest = {"dataset": args.dataset, "archive": archive.name, "bytes": archive.stat().st_size,
                "sha256": digest(archive), "files": files,
                "licenses": ["CC-BY-NC-SA-4.0"] if args.dataset == "points" else ["CC-BY-4.0"]}
    (output / ("tennis_" + args.dataset + "_asset_manifest.json")).write_text(json.dumps(manifest, indent=2))
    shutil.copyfile(source / report, output / ("tennis_" + args.dataset + "_summary.json"))
    print(json.dumps({"archive": archive.name, "bytes": manifest["bytes"], "files": len(files)}), flush=True)


if __name__ == "__main__":
    main()
