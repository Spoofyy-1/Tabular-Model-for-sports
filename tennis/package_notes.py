"""Package the isolated, noncommercial tennis note mention research tables."""
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
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def main():
    require_github_hosted_runner()
    source = ROOT / "data/tennis/notes_research_only"
    summary = json.loads((source / "summary.json").read_text())
    if summary.get("research_only_noncommercial") is not True:
        raise ValueError("Missing noncommercial research restriction")
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Unexpected symlink")
        if path.is_file():
            files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)})
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / "csv-tennis-note-mentions-research-only.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        tar.add(source, arcname=source.relative_to(ROOT).as_posix())
    if archive.stat().st_size > 500_000_000:
        raise ValueError("Note archive exceeds budget")
    manifest = {"archive": archive.name, "bytes": archive.stat().st_size, "sha256": digest(archive),
                "files": files, "licenses": ["CC-BY-NC-SA-4.0"], "research_only_noncommercial": True}
    (dist / "tennis_notes_asset_manifest.json").write_text(json.dumps(manifest, indent=2))
    shutil.copyfile(source / "summary.json", dist / "tennis_notes_summary.json")


if __name__ == "__main__":
    main()
