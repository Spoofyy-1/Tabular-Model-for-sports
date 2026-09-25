"""Cloud-only NFL combine and draft research with career totals isolated."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

COMBINE = ["season", "pfr_id", "cfb_id", "player_name", "pos", "school", "ht", "wt", "forty", "bench", "vertical", "broad_jump", "cone", "shuttle"]
DRAFT = ["season", "round", "pick", "team", "gsis_id", "pfr_player_id", "cfb_player_id", "pfr_player_name", "position", "category", "side", "college", "age"]


def candidate_table(source, kind):
    allowed = COMBINE if kind == "combine" else DRAFT
    if "season" not in source or not len(source):
        raise ValueError("Nonempty source with season required")
    out = source[[name for name in allowed if name in source]].copy()
    out["measurement_or_draft_year"] = pd.to_numeric(out.season, errors="coerce").astype("Int64")
    out["partition_by_year_only"] = "unresolved_year"
    out.loc[out.measurement_or_draft_year.le(2024).fillna(False), "partition_by_year_only"] = "development_through_2024"
    out.loc[out.measurement_or_draft_year.ge(2025).fillna(False), "partition_by_year_only"] = "holdout_2025_onward"
    out["historical_publication_time_verified"] = False
    out["automatic_training_join_allowed"] = False
    if kind == "combine" and "ht" in out:
        height = out.ht.astype("string").str.extract(r"^(\d)-(\d{1,2})$")
        feet = pd.to_numeric(height[0], errors="coerce")
        inches = pd.to_numeric(height[1], errors="coerce")
        out["height_inches"] = (12 * feet + inches).where(feet.between(4, 8) & inches.between(0, 11)).astype("Float64")
        out["height_cm"] = out.height_inches * 2.54
    if kind == "combine" and "wt" in out:
        out["weight_kg"] = pd.to_numeric(out.wt, errors="coerce") * 0.45359237
    for name in out:
        if name.endswith("_id"):
            out[name] = out[name].astype("string")
    keys = [name for name in ["season", "pick", "player_name", "pfr_player_name", "pfr_id"] if name in out]
    return out.sort_values(keys, na_position="last").reset_index(drop=True)


def fetch(url, max_bytes):
    require_github_hosted_runner()
    with requests.get(url, stream=True, timeout=(15, 90)) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Profile source exceeded bounded transfer")
            chunks.append(chunk)
        data = b"".join(chunks)
    return data


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "columns": len(frame.columns),
            "schema": {name: str(dtype) for name, dtype in frame.dtypes.items()},
            "missing": {name: int(value) for name, value in frame.isna().sum().items()},
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    require_github_hosted_runner()
    out = ROOT / "data/extras/nfl_profiles"
    out.mkdir(parents=True, exist_ok=True)
    summary = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "tables": {}, "sources": [],
               "license": "CC-BY-4.0", "attribution": "nflverse; source combine/draft facts courtesy of Pro Football Reference",
               "limitations": ["Year-only measurement dates do not prove historical publication or pregame availability.",
                   "Draft source includes future career totals; these are isolated in source_audit and excluded from candidate profiles.",
                   "Missing combine measurements are not zeros. Combine participants are a selected population.",
                   "No names are fuzzy-matched to GSIS; source identity values are retained.",
                   "Current corrected source snapshot; historical revisions and player identity mappings require audit."]}
    license_url = "https://raw.githubusercontent.com/nflverse/nflverse-data/main/LICENSE.md"
    license_data = fetch(license_url, 100_000)
    (out / "SOURCE_LICENSE.md").write_bytes(license_data)
    for kind in ["combine", "draft_picks"]:
        response = requests.get("https://api.github.com/repos/nflverse/nflverse-data/releases/tags/" + kind, timeout=30)
        response.raise_for_status()
        assets = [a for a in response.json()["assets"] if a["name"] == kind + ".parquet"]
        if len(assets) != 1 or assets[0]["size"] > 10_000_000:
            raise ValueError("Missing, ambiguous or oversized profile asset")
        asset = assets[0]
        data = fetch(asset["browser_download_url"], 10_000_000)
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != asset["size"] or asset.get("digest") not in (None, "sha256:" + digest):
            raise ValueError("Upstream asset integrity changed")
        frame = pd.read_parquet(io.BytesIO(data))
        summary["sources"].append({"url": asset["browser_download_url"], "bytes": len(data), "sha256": digest,
                                  "source_asset_updated_at": asset["updated_at"], "dictionary": "https://nflreadr.nflverse.com/articles/dictionary_" + kind + ".html"})
        summary["tables"][kind + "_source_audit"] = write(frame, out / (kind + "_source_audit.csv.gz"))
        candidate = candidate_table(frame, "combine" if kind == "combine" else "draft")
        summary["tables"][kind + "_candidate_profiles"] = write(candidate, out / (kind + "_candidate_profiles.csv.gz"))
        summary["tables"][kind + "_candidate_profiles"]["year_partitions"] = {str(k): int(v) for k, v in candidate.partition_by_year_only.value_counts().items()}
        print(json.dumps({"dataset": kind, "rows": len(frame), "earliest_year": int(candidate.measurement_or_draft_year.min()),
                          "latest_year": int(candidate.measurement_or_draft_year.max())}), flush=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "schema.json").write_text(json.dumps({"null_token": r"\N", "tables": summary["tables"],
        "source_audit": "Retrospective original fields, including future career statistics; never automatically model features",
        "candidate_profiles": "Explicit static/combine/draft whitelist; still requires as-of and identity verification"}, indent=2))


if __name__ == "__main__":
    main()
