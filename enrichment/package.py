"""Package CSV enrichment tables only on the hosted collection runner."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from runtime import require_github_hosted_runner


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    require_github_hosted_runner()
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', choices=['nba_injuries', 'nfl_plays', 'nba_crosswalk'], required=True)
    a = p.parse_args()
    out = ROOT / 'data/enrichment' / a.dataset
    summary = out / 'enrichment_summary.json'
    json.loads(summary.read_text())
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    report = dist / (a.dataset + '_enrichment_summary.json')
    report.write_bytes(summary.read_bytes())
    package = dist / ('csv-' + a.dataset + '.tar.gz')
    with tarfile.open(package, 'w:gz', compresslevel=1) as archive:
        archive.add(out, arcname='data/enrichment/' + a.dataset)
    if package.stat().st_size >= 1_900_000_000:
        raise ValueError('Archive exceeds publication budget')
    manifest = {'dataset': a.dataset, 'format': 'csv.gz', 'git_commit': os.environ['GITHUB_SHA'],
                'assets': [{'name': f.name, 'bytes': f.stat().st_size, 'sha256': digest(f)} for f in [package, report]]}
    (dist / (a.dataset + '_enrichment_asset_manifest.json')).write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'dataset': a.dataset, 'archive_bytes': package.stat().st_size}), flush=True)


if __name__ == '__main__':
    main()
