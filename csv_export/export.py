"""Stream the published sports tables into CSV on GitHub-hosted runners."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from runtime import require_github_hosted_runner

REPO = 'Spoofyy-1/Tabular-Model-for-sports'
NULL = '\\N'
SOURCES = {
    'base-nba': ('snapshot-36055555015-1', 'dataset-nba.tar.gz'),
    'base-nfl': ('snapshot-36055555015-1', 'dataset-nfl.tar.gz'),
    'context-nba': ('context-36094215424-1', 'context-nba.tar.gz'),
    'context-nfl': ('context-36094215424-1', 'context-nfl.tar.gz'),
    'matchups': ('context-36094215424-1', 'context-matchups.tar.gz'),
    'odds': ('context-36094215424-1', 'context-odds.tar.gz'),
}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('Unsafe archive member')
    return path


def json_value(value):
    if hasattr(value, 'tolist'):
        return value.tolist()
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(type(value).__name__)


def csv_batch(batch):
    """Pure conversion; synthetic batch tests do not access data or disk."""
    frame = batch.to_pandas(types_mapper=pd.ArrowDtype)
    for field in batch.schema:
        t = field.type
        if pa.types.is_nested(t):
            frame[field.name] = [None if value is None else json.dumps(value, default=json_value, ensure_ascii=False, separators=(',', ':')) for value in batch.column(field.name).to_pylist()]
        elif pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
            frame[field.name] = [None if value is None else value.hex() for value in batch.column(field.name).to_pylist()]
        if pa.types.is_string(t) or pa.types.is_large_string(t):
            if frame[field.name].eq(NULL).fillna(False).any():
                raise ValueError('Source text collides with the documented CSV null token')
    return frame


def schema_description(schema):
    return {'format': 'gzip-compressed RFC4180-style UTF-8 CSV', 'null_token': NULL,
            'read_guidance': 'Use keep_default_na=False and na_values=[null_token]. Preserve source string identifiers as strings, including leading zeros. Timestamp cells retain source timezone/precision. Nested cells are JSON; binary cells are hexadecimal.',
            'columns': [{'name': f.name, 'arrow_type': str(f.type), 'nullable': f.nullable,
                         'csv_encoding': 'json' if pa.types.is_nested(f.type) else 'hex' if pa.types.is_binary(f.type) or pa.types.is_large_binary(f.type) or pa.types.is_fixed_size_binary(f.type) else 'scalar'} for f in schema]}


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', choices=SOURCES, required=True)
    args = parser.parse_args()
    tag, asset_name = SOURCES[args.dataset]
    response = requests.get(f'https://api.github.com/repos/{REPO}/releases/tags/{tag}', timeout=30)
    response.raise_for_status()
    asset = next(a for a in response.json()['assets'] if a['name'] == asset_name)
    if asset['size'] > 700_000_000:
        raise ValueError('Source archive exceeds the declared input budget')
    out = ROOT / 'data/csv_export' / args.dataset
    out.mkdir(parents=True, exist_ok=True)
    tables, copied = [], []
    with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'], prefix='sports-csv-') as temp:
        temp = Path(temp)
        archive_path = temp / 'source.tar.gz'
        h, count = hashlib.sha256(), 0
        with requests.get(asset['browser_download_url'], stream=True, timeout=(20, 120)) as r:
            r.raise_for_status()
            with archive_path.open('wb') as f:
                for chunk in r.iter_content(1024 * 1024):
                    count += len(chunk)
                    if count > asset['size']:
                        raise ValueError('Source archive exceeds its declared size')
                    h.update(chunk)
                    f.write(chunk)
        if count != asset['size'] or asset.get('digest') != 'sha256:' + h.hexdigest():
            raise ValueError('Source archive integrity mismatch')
        with tarfile.open(archive_path, 'r|gz') as source:
            for member in source:
                if not member.isfile():
                    continue
                relative = safe_name(member.name)
                if member.size > 2_000_000_000:
                    raise ValueError('Source member exceeds the declared expansion budget')
                destination = out / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if relative.suffix == '.parquet':
                    raw = temp / 'table.parquet'
                    with source.extractfile(member) as f, raw.open('wb') as target:
                        shutil.copyfileobj(f, target, 1024 * 1024)
                    parquet = pq.ParquetFile(raw)
                    csv_path = destination.with_suffix('.csv.gz')
                    rows, batches = 0, 0
                    with gzip.open(csv_path, 'wt', encoding='utf-8', newline='', compresslevel=5) as csv:
                        for batch in parquet.iter_batches(batch_size=32768):
                            frame = csv_batch(batch)
                            frame.to_csv(csv, index=False, header=batches == 0, na_rep=NULL)
                            rows += len(frame)
                            batches += 1
                        if not batches:
                            pd.DataFrame(columns=parquet.schema_arrow.names).to_csv(csv, index=False)
                    if rows != parquet.metadata.num_rows:
                        raise ValueError('CSV row count disagrees with Parquet metadata')
                    schema = schema_description(parquet.schema_arrow)
                    schema.update(source_member=member.name, rows=rows)
                    destination.with_suffix('.schema.json').write_text(json.dumps(schema, indent=2))
                    tables.append({'file': str(csv_path.relative_to(out)), 'rows': rows, 'columns': len(parquet.schema_arrow), 'bytes': csv_path.stat().st_size, 'sha256': sha(csv_path)})
                    print(json.dumps(tables[-1]), flush=True)
                    del parquet
                    raw.unlink()
                elif relative.suffix in {'.json', '.md', '.txt'} and member.size < 25_000_000:
                    with source.extractfile(member) as f, destination.open('wb') as target:
                        shutil.copyfileobj(f, target)
                    copied.append(str(relative))
                # Model binaries and unrelated compressed/raw files are not tabular exports.
    if not tables:
        raise ValueError('Source archive contains no exported tables')
    summary = {'dataset': args.dataset, 'built_at_utc': datetime.now(timezone.utc).isoformat(), 'source_release': tag,
               'source_asset': asset_name, 'source_sha256': h.hexdigest(), 'format': 'csv.gz',
               'table_count': len(tables), 'total_table_rows_including_overlapping_views': sum(t['rows'] for t in tables),
               'model_retraining': False, 'null_token': NULL, 'tables': tables, 'copied_source_documentation': copied}
    (out / 'csv_manifest.json').write_text(json.dumps(summary, indent=2))
    (out / 'CSV_README.md').write_text('# CSV export\n\nEach `.csv.gz` file is a gzip-compressed CSV. Every table has a companion schema JSON. Use UTF-8, quoted CSV parsing and `\\N` as the only null token; empty text is distinct from null. Preserve ID strings and timezone-bearing timestamps. Source JSON/Markdown documents are retained for provenance and license attribution; their older Parquet paths refer to the source release. Row counts cover overlapping tables and cannot be summed as independent training samples.\n')
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    package = dist / ('csv-' + args.dataset + '.tar.gz')
    with tarfile.open(package, 'w:gz', compresslevel=1) as archive:
        for child in sorted(out.iterdir()):
            archive.add(child, arcname=child.name)
    if package.stat().st_size >= 1_900_000_000:
        raise ValueError('CSV package exceeds the publication limit')
    report = dist / (args.dataset + '_csv_summary.json')
    report.write_text(json.dumps(summary, indent=2))
    manifest = {'dataset': args.dataset, 'git_commit': os.environ['GITHUB_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
                'assets': [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in [package, report]]}
    (dist / (args.dataset + '_csv_asset_manifest.json')).write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'dataset': args.dataset, 'tables': len(tables), 'archive_bytes': package.stat().st_size}), flush=True)


if __name__ == '__main__':
    main()
