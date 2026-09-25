#!/usr/bin/env python3
"""Collect upstream NBA/ESPN player identity candidates as cloud-only CSV.

These source mappings use name/roster matching. Bijective identifiers are a
consistency check, not verification of player identity. Never auto-join training.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/nba_crosswalk/"
ASSETS = ["nba_player_crosswalk_2026.parquet", "nba_player_crosswalk_2027.parquet"]
LICENSES = [
    ("hoopR-nba-data-LICENSE.txt", "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/LICENSE.md"),
    ("sportsdataverse-data-LICENSE.txt", "https://raw.githubusercontent.com/sportsdataverse/sportsdataverse-data/main/LICENSE"),
]
BUILDER = "https://github.com/sportsdataverse/hoopR/blob/main/R/nba_crosswalk.R"
REQUIRED = {"season", "espn_athlete_id", "nba_player_id", "match_method", "match_confidence"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fetch(url):
    require_github_hosted_runner()
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=(15, 60)) as response:
                response.raise_for_status()
                content = io.BytesIO()
                for block in response.iter_content(1024 * 1024):
                    content.write(block)
                    if content.tell() > 5_000_000:
                        raise ValueError("Crosswalk source exceeds 5MB input bound")
                value = content.getvalue()
                return value, {"source_url": url, "retrieved_at_utc": now(), "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest(), "last_modified": response.headers.get("Last-Modified")}
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def normalized_id(values):
    return values.astype("string").str.strip().str.replace(r"\.0$", "", regex=True).replace("", pd.NA)


def mark_candidates(frame):
    if not REQUIRED.issubset(frame):
        raise ValueError("Crosswalk missing required source fields: " + str(REQUIRED - set(frame)))
    reserved = {"canonical_espn_athlete_id", "canonical_nba_player_id", "project_source_snapshot", "candidate_pair_complete", "candidate_pair_bijective_within_snapshot", "candidate_pair_bijective_across_loaded_snapshots", "source_duplicate_pair_row", "identity_independently_verified", "automatic_training_join_allowed"}
    collision = reserved.intersection(frame.columns) - {"project_source_snapshot"}
    if collision:
        raise ValueError("Source collides with derived column names: " + str(collision))
    out = frame.copy()
    out["canonical_espn_athlete_id"] = normalized_id(out.espn_athlete_id)
    out["canonical_nba_player_id"] = normalized_id(out.nba_player_id)
    valid = out.canonical_espn_athlete_id.str.fullmatch(r"\d+", na=False) & out.canonical_nba_player_id.str.fullmatch(r"\d+", na=False)
    out["candidate_pair_complete"] = valid.astype("boolean")
    a, b = "canonical_espn_athlete_id", "canonical_nba_player_id"
    pairs = out.loc[valid, [a, b]].drop_duplicates()
    espn_degree = pairs.groupby(a)[b].nunique()
    nba_degree = pairs.groupby(b)[a].nunique()
    out["candidate_pair_bijective_across_loaded_snapshots"] = (valid & out[a].map(espn_degree).eq(1) & out[b].map(nba_degree).eq(1)).astype("boolean")
    out["candidate_pair_bijective_within_snapshot"] = False
    out["source_duplicate_pair_row"] = out.duplicated(["project_source_snapshot", a, b], keep=False) & valid
    for _, block in out.groupby("project_source_snapshot", sort=False):
        subset = block.loc[block.candidate_pair_complete.fillna(False), [a, b]].drop_duplicates()
        left = subset.groupby(a)[b].nunique()
        right = subset.groupby(b)[a].nunique()
        out.loc[block.index, "candidate_pair_bijective_within_snapshot"] = block.candidate_pair_complete & block[a].map(left).eq(1) & block[b].map(right).eq(1)
    out["identity_independently_verified"] = False
    out["automatic_training_join_allowed"] = False
    return out.sort_values(["project_source_snapshot", a, b], na_position="last").reset_index(drop=True)


def write_csv(frame, path):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})


def main():
    require_github_hosted_runner()
    output = ROOT / "data/enrichment/nba_crosswalk"
    output.mkdir(parents=True, exist_ok=True)
    parts, provenance = [], []
    for asset in ASSETS:
        content, metadata = fetch(BASE + asset)
        frame = pd.read_parquet(io.BytesIO(content))
        metadata.update(asset=asset, rows=len(frame), original_schema={name: str(dtype) for name, dtype in frame.dtypes.items()})
        if not REQUIRED.issubset(frame):
            raise ValueError("Source crosswalk schema missing " + str(REQUIRED - set(frame)))
        if any(name.startswith("project_source_") for name in frame.columns):
            raise ValueError("Unexpected source column naming conflict")
        frame["project_source_snapshot"] = asset
        frame["project_source_url"] = metadata["source_url"]
        frame["project_source_sha256"] = metadata["sha256"]
        frame["project_source_retrieved_at_utc"] = metadata["retrieved_at_utc"]
        parts.append(frame)
        provenance.append(metadata)
    candidates = mark_candidates(pd.concat(parts, ignore_index=True))
    write_csv(candidates, output / "player_identity_candidates.csv.gz")
    for name, url in LICENSES:
        content, metadata = fetch(url)
        (output / name).write_bytes(content)
        provenance.append(metadata)
    complete = candidates.candidate_pair_complete.fillna(False)
    summary = {"created_at_utc": now(), "source": "SportsDataverse hoopR NBA player crosswalk snapshots", "snapshot_assets": ASSETS,
        "rows": len(candidates), "complete_candidate_rows": int(complete.sum()),
        "incomplete_or_invalid_id_rows": int((~complete).sum()),
        "bijective_within_snapshot_rows": int(candidates.candidate_pair_bijective_within_snapshot.sum()),
        "bijective_across_loaded_snapshots_rows": int(candidates.candidate_pair_bijective_across_loaded_snapshots.sum()),
        "conflicting_candidate_rows": int((complete & ~candidates.candidate_pair_bijective_across_loaded_snapshots).sum()),
        "duplicate_candidate_rows_within_snapshot": int(candidates.source_duplicate_pair_row.sum()),
        "distinct_espn_ids": int(candidates.canonical_espn_athlete_id.nunique()), "distinct_nba_ids": int(candidates.canonical_nba_player_id.nunique()),
        "match_method_counts": {str(k): int(v) for k,v in candidates.match_method.astype("string").fillna("missing").value_counts().items()},
        "snapshot_counts": {str(k): int(v) for k,v in candidates.project_source_snapshot.value_counts().items()},
        "identity_independently_verified": False, "automatic_training_join_allowed": False,
        "producer_builder_reference": BUILDER, "producer_method": "Upstream player matching uses team blocks, normalized names, jersey and birth-date evidence; source match_method/confidence are retained without being treated as proof.",
        "original_fields_preserved": True,
        "limitations": ["2026 and 2027 are source snapshot/season labels, not historical roster membership.", "The latest snapshot may omit retired players and contain incorrect matches.", "A bijective mapping can still match the wrong people; independent identity verification is required.", "No source confidence threshold is used to admit rows into training.", "No baseline player IDs, models or training data are changed."],
        "license_note": "Producer publishes CC BY 4.0 and distribution repository MIT; copies retained. Attribute ESPN/NBA-derived identities to hoopR/SportsDataverse."}
    schema = {"file": "player_identity_candidates.csv.gz", "rows": len(candidates), "columns": {name: str(dtype) for name,dtype in candidates.dtypes.items()}, "missing_values": {name: int(value) for name,value in candidates.isna().sum().items()}, "null_encoding": r"\N; import identifier columns as strings; empty text remains an empty field", "derived_column_meanings": {"canonical_espn_athlete_id": "Source ESPN ID normalized as a string; original field preserved", "canonical_nba_player_id": "Source NBA ID normalized as a string; original field preserved", "candidate_pair_complete": "Both normalized IDs contain digits only", "candidate_pair_bijective_within_snapshot": "Each ID has exactly one counterpart within its source asset, excluding missing pairs", "candidate_pair_bijective_across_loaded_snapshots": "Each ID has exactly one counterpart across both loaded assets, excluding missing pairs", "source_duplicate_pair_row": "The same complete ID pair appears more than once in a source asset", "identity_independently_verified": "Always false", "automatic_training_join_allowed": "Always false"}}
    (output / "schema.json").write_text(json.dumps(schema, indent=2))
    (output / "enrichment_summary.json").write_text(json.dumps(summary, indent=2))
    (output / "source_manifest.json").write_text(json.dumps({"sources": provenance, "attribution": "Player identity candidates compiled by hoopR/SportsDataverse from ESPN and NBA sources; project normalization and consistency flags added."}, indent=2))
    print(json.dumps({key: summary[key] for key in ["rows", "complete_candidate_rows", "conflicting_candidate_rows", "duplicate_candidate_rows_within_snapshot", "identity_independently_verified"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
