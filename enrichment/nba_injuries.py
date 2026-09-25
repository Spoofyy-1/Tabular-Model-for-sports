#!/usr/bin/env python3
"""Cloud-only extraction of timestamped official NBA injury reports into CSV.

NBA PDFs are transient GitHub-runner inputs. Only factual parsed tables and
provenance are published; PDF report timestamps are not independent proof of
historical web availability. No training or betting is performed here.
"""
import argparse
from datetime import datetime, time, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import traceback
import time as time_module
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

BASE_URL = "https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/download/snapshot-36055555015-1/dataset-nba.tar.gz"
PARSER_COMMIT = "57603738fb28185e3c1bf4d71d75af0f85514ac1"
PARSER_SOURCE = "https://github.com/mxufc29/nbainjuries"
EASTERN = ZoneInfo("America/New_York")
SOURCE_COLUMNS = ["Game Date", "Game Time", "Matchup", "Team", "Player Name", "Current Status", "Reason"]
STATUSES = {"Available", "Probable", "Questionable", "Doubtful", "Out"}
ALIASES = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}
HEADER = re.compile(r"Injury\s*Report\s*:\s*(\d{1,2}/\d{1,2}/\d{2,4})\s+(\d{1,2}:\d{2})\s*(AM|PM)", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def report_url(timestamp):
    # URL cadence documented in pinned nbainjuries _util.py.
    suffix = timestamp.strftime("%I_%M%p") if timestamp >= datetime(2025, 12, 22, 9, 0) else timestamp.strftime("%I%p")
    return "https://ak-static.cms.nba.com/referee/injury/Injury-Report_" + timestamp.strftime("%Y-%m-%d_") + suffix + ".pdf"


def verify_header(text, requested):
    matches = HEADER.findall(text or "")
    parsed = []
    for date_text, time_text, ampm in matches:
        fmt = "%m/%d/%Y %I:%M %p" if len(date_text.split("/")[-1]) == 4 else "%m/%d/%y %I:%M %p"
        parsed.append(datetime.strptime(f"{date_text} {time_text} {ampm.upper()}", fmt))
    if not parsed or any(value != requested for value in parsed):
        raise ValueError("Missing or mismatched report-header timestamp")
    return requested.replace(tzinfo=EASTERN).astimezone(timezone.utc)


def fetch(url, maximum, attempts=3):
    require_github_hosted_runner()
    for attempt in range(attempts):
        try:
            with requests.get(url, stream=True, timeout=(15, 60), headers={"User-Agent": "sports-props-research/1.0"}) as response:
                if response.status_code == 404:
                    return None, {"source_url": url, "http_status": 404, "retrieved_at_utc": now(), "status": "source_not_found"}
                response.raise_for_status()
                buffer = io.BytesIO()
                for chunk in response.iter_content(1024 * 1024):
                    buffer.write(chunk)
                    if buffer.tell() > maximum:
                        raise ValueError("Source exceeds bounded input size")
                value = buffer.getvalue()
                return value, {"source_url": url, "http_status": response.status_code, "bytes": len(value), "sha256": sha256(value), "retrieved_at_utc": now(), "last_modified": response.headers.get("Last-Modified")}
        except requests.RequestException:
            if attempt + 1 == attempts:
                raise
            time_module.sleep(2 ** attempt)


def canonical_team(values):
    return values.astype("string").str.strip().str.upper().replace(ALIASES)


def prepare_games(games):
    required = {"game_id", "game_date", "game_local_date", "home_team", "away_team"}
    if not required.issubset(games):
        raise ValueError("Baseline game schema missing " + str(required - set(games)))
    out = games[["game_id", "game_date", "game_local_date", "home_team", "away_team"]].copy()
    out["source_game_date"] = pd.to_datetime(out.game_local_date, errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
    out["matchup_key"] = canonical_team(out.away_team) + "@" + canonical_team(out.home_team)
    out["game_date"] = pd.to_datetime(out.game_date, utc=True, errors="coerce")
    out = out.rename(columns={"game_id": "espn_game_id"})
    keys = ["source_game_date", "matchup_key"]
    ambiguous = out.duplicated(keys, keep=False) | out[keys].isna().any(axis=1)
    return out.loc[~ambiguous, keys + ["espn_game_id", "game_date"]], int(ambiguous.sum())


def report_dates(games, start, end, maximum):
    dates = pd.to_datetime(games.game_local_date, errors="coerce").dt.date
    selected = sorted(set(day for day in dates.dropna() if start <= day <= end))
    if len(selected) > maximum:
        raise ValueError(f"Requested {len(selected)} game-date reports exceeds --max-reports={maximum}; narrow dates explicitly")
    if not selected:
        raise ValueError("No source-listed game dates fall in requested range")
    return selected


def normalized_report(raw, report_timestamp, source_url, content_hash, retrieved_at, lookup):
    if list(raw.columns) != SOURCE_COLUMNS:
        raise ValueError("Parser returned unexpected injury-report column schema")
    output = raw.copy()
    output.columns = ["game_date_source", "game_time_source", "matchup_source", "team_source", "player_name_source", "participation_status_source", "reason_source"]
    for name in output:
        output[name] = output[name].astype("string").str.strip()
    output["source_game_date"] = pd.to_datetime(output.game_date_source, format="%m/%d/%Y", errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
    components = output.matchup_source.str.upper().str.replace(" ", "", regex=False).str.extract(r"^([A-Z]{2,4})@([A-Z]{2,4})$")
    output["matchup_key"] = canonical_team(components[0]) + "@" + canonical_team(components[1])
    unsubmitted = output.reason_source.str.replace(r"\s+", " ", regex=True).str.casefold().eq("not yet submitted").fillna(False)
    known_status = output.participation_status_source.isin(STATUSES)
    output["entry_type"] = "unresolved"
    player_present = output.player_name_source.notna() & output.player_name_source.ne("")
    output.loc[player_present & known_status, "entry_type"] = "player_status"
    output.loc[unsubmitted & ~player_present, "entry_type"] = "team_not_submitted"
    valid_keys = output.source_game_date.notna() & output.matchup_key.notna() & output.team_source.notna() & output.team_source.ne("")
    output["parse_valid"] = valid_keys & output.entry_type.ne("unresolved")
    output["report_timestamp_utc"] = pd.Timestamp(report_timestamp)
    output["report_timestamp_et"] = pd.Timestamp(report_timestamp).tz_convert("America/New_York")
    output["report_header_timestamp_verified"] = True
    output["historical_web_availability_independently_verified"] = False
    output["source_url"] = source_url
    output["source_sha256"] = content_hash
    output["retrieved_at_utc"] = retrieved_at
    output["source_row_number"] = range(1, len(output) + 1)
    output = output.merge(lookup, on=["source_game_date", "matchup_key"], how="left", validate="many_to_one")
    output["espn_game_id"] = output.espn_game_id.astype("string")
    output["espn_player_id"] = pd.Series(pd.NA, index=output.index, dtype="string")
    output["player_identity_resolution"] = "unmapped_source_name"
    output["header_precedes_game_start"] = (output.report_timestamp_utc < output.game_date).astype("boolean").where(output.game_date.notna())
    output["header_to_game_start_hours"] = (output.game_date - output.report_timestamp_utc).dt.total_seconds() / 3600
    output["evaluation_split"] = "unresolved_game_time"
    output.loc[output.game_date < pd.Timestamp("2024-01-01", tz="UTC"), "evaluation_split"] = "fit_pre_2024"
    output.loc[(output.game_date >= pd.Timestamp("2024-01-01", tz="UTC")) & (output.game_date < pd.Timestamp("2025-01-01", tz="UTC")), "evaluation_split"] = "calibration_2024"
    output.loc[output.game_date >= pd.Timestamp("2025-01-01", tz="UTC"), "evaluation_split"] = "holdout_2025_plus"
    return output


def csv_table(frame, destination):
    require_github_hosted_runner()
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False, compression={"method": "gzip", "mtime": 0}, na_rep=r"\N")
    return {"path": destination.name, "rows": len(frame), "schema": {name: str(dtype) for name, dtype in frame.dtypes.items()}, "missing_values": {name: int(value) for name, value in frame.isna().sum().items()}, "csv_null_encoding": r"\N", "id_handling": "Read all *_id fields as strings; no numeric spreadsheet inference"}


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-10-18")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--max-reports", type=int, default=1000)
    parser.add_argument("--pause-seconds", type=float, default=0.2)
    args = parser.parse_args()
    start, end = datetime.strptime(args.start, "%Y-%m-%d").date(), datetime.strptime(args.end, "%Y-%m-%d").date()
    if start < datetime(2021, 10, 18).date() or end < start or end > datetime.now(timezone.utc).date():
        raise ValueError("Invalid injury report date range")
    if not 1 <= args.max_reports <= 2000 or args.pause_seconds < 0:
        raise ValueError("Invalid request bounds")
    # Imported after guard: no parser initialization, Java, files, or network locally.
    from PyPDF2 import PdfReader
    from nbainjuries import injury
    output_dir = ROOT / "data/enrichment/nba_injuries"
    cache = Path(os.environ["RUNNER_TEMP"]) / "nba_injury_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    base, baseline_provenance = fetch(BASE_URL, 100_000_000)
    if base is None:
        raise RuntimeError("Required baseline NBA game release not found")
    with tarfile.open(fileobj=io.BytesIO(base), mode="r:gz") as archive:
        member = archive.getmember("data/nba/games.parquet")
        if not member.isfile() or member.size > 30_000_000:
            raise ValueError("Unexpected baseline games member")
        with archive.extractfile(member) as stream:
            games = pd.read_parquet(stream)
    del base
    lookup, ambiguous_games = prepare_games(games)
    selected = report_dates(games, start, end, args.max_reports)
    manifests, parsed_reports, failures = [], [], []
    started = now()
    for number, day in enumerate(selected, 1):
        requested = datetime.combine(day, time(17, 30))
        url = report_url(requested)
        entry = {"requested_report_et": requested.isoformat(), "source_url": url, "stage": "download"}
        path = cache / url.rsplit("/", 1)[-1]
        try:
            content, provenance = fetch(url, 5_000_000)
            entry.update(provenance)
            if content is None:
                manifests.append(entry)
                continue
            if not content.startswith(b"%PDF"):
                raise ValueError("Source response is not a PDF")
            entry["stage"] = "header_validation"
            reader = PdfReader(io.BytesIO(content))
            if not 1 <= len(reader.pages) <= 100:
                raise ValueError("Unexpected PDF page count")
            reported_utc = verify_header(reader.pages[0].extract_text(), requested)
            entry["report_timestamp_utc"] = reported_utc.isoformat()
            entry["header_timestamp_verified"] = True
            entry["pages"] = len(reader.pages)
            path.write_bytes(content)
            entry["stage"] = "pdf_table_parsing"
            raw = injury.get_reportdata(requested, local=True, localdir=str(cache), return_df=True)
            entry["parsed_column_count"] = len(raw.columns)
            entry["parsed_column_names"] = json.dumps([name if name in SOURCE_COLUMNS else "<unexpected-column-omitted>" for name in raw.columns])
            entry["stage"] = "row_validation"
            cleaned = normalized_report(raw, reported_utc, url, provenance["sha256"], provenance["retrieved_at_utc"], lookup)
            entry.update(status="parsed", rows=len(cleaned), invalid_rows=int((~cleaned.parse_valid).sum()), mapped_rows=int(cleaned.espn_game_id.notna().sum()))
            parsed_reports.append(cleaned)
            manifests.append(entry)
        except Exception as error:
            safe_messages = {"Source exceeds bounded input size", "Missing or mismatched report-header timestamp", "Source response is not a PDF", "Unexpected PDF page count", "Parser returned unexpected injury-report column schema"}
            message = str(error)
            summary = message if message in safe_messages else "Report processing failed at " + entry["stage"]
            stack = [{"file": Path(item.filename).name, "line": item.lineno, "function": item.name} for item in traceback.extract_tb(error.__traceback__)[-5:]]
            entry.update(status="error", error_type=type(error).__name__, error_summary=summary, error_traceback_metadata=json.dumps(stack))
            failures.append(entry.copy())
            manifests.append(entry)
        finally:
            path.unlink(missing_ok=True)
            time_module.sleep(args.pause_seconds)
            if number % 25 == 0 or number == len(selected):
                print(json.dumps({"requested_reports": number, "total_requested": len(selected), "parsed_reports": len(parsed_reports), "error_reports": len(failures), "missing_reports": sum(m.get("status") == "source_not_found" for m in manifests)}), flush=True)
    schema = {}
    if parsed_reports:
        all_rows = pd.concat(parsed_reports, ignore_index=True).sort_values(["report_timestamp_utc", "source_game_date", "matchup_key", "team_source", "player_name_source", "source_row_number"], na_position="last").reset_index(drop=True)
        valid = all_rows.loc[all_rows.parse_valid].copy()
        quarantined = all_rows.loc[~all_rows.parse_valid].copy()
        schema["injury_report_entries"] = csv_table(valid, output_dir / "injury_report_entries.csv.gz")
        schema["quarantined_rows"] = csv_table(quarantined, output_dir / "quarantined_rows.csv.gz")
    else:
        all_rows = pd.DataFrame()
        valid = pd.DataFrame()
        quarantined = pd.DataFrame()
    manifest_frame = pd.DataFrame(manifests)
    schema["report_inventory"] = csv_table(manifest_frame, output_dir / "report_inventory.csv.gz")
    license_url = f"https://raw.githubusercontent.com/mxufc29/nbainjuries/{PARSER_COMMIT}/LICENSE"
    license_bytes, license_provenance = fetch(license_url, 1_000_000)
    if license_bytes is not None:
        (output_dir / "PARSER_LICENSE.txt").write_bytes(license_bytes)
    summary = {"created_at_utc": now(), "started_at_utc": started, "source": "Official NBA injury report PDFs", "start": args.start, "end": args.end, "snapshot_policy": "One report at 17:30 America/New_York for each baseline NBA game-date", "requested_reports": len(selected), "parsed_reports": len(parsed_reports), "source_not_found_reports": int(manifest_frame.status.eq("source_not_found").sum()), "error_reports": len(failures), "rows": len(valid), "quarantined_rows": len(quarantined), "player_status_rows": int(valid.entry_type.eq("player_status").sum()) if len(valid) else 0, "team_not_submitted_rows": int(valid.entry_type.eq("team_not_submitted").sum()) if len(valid) else 0,
        "mapped_game_rows": int(valid.espn_game_id.notna().sum()) if len(valid) else 0, "header_precedes_game_rows": int(valid.header_precedes_game_start.fillna(False).sum()) if len(valid) else 0,
        "same_or_after_game_start_rows": int(valid.header_precedes_game_start.eq(False).fillna(False).sum()) if len(valid) else 0,
        "duplicate_player_report_keys": int(valid.loc[valid.entry_type.eq("player_status")].duplicated(["source_url", "source_game_date", "matchup_key", "team_source", "player_name_source"]).sum()) if len(valid) else 0,
        "split_counts": {str(k): int(v) for k,v in valid.evaluation_split.value_counts().items()} if len(valid) else {},
        "report_date_min": valid.report_timestamp_utc.min().isoformat() if len(valid) else None, "report_date_max": valid.report_timestamp_utc.max().isoformat() if len(valid) else None,
        "baseline_ambiguous_game_rows_excluded_from_mapping": ambiguous_games, "baseline_provenance": baseline_provenance,
        "parser_repository": PARSER_SOURCE, "parser_commit": PARSER_COMMIT, "parser_license_provenance": license_provenance,
        "source_report_guidance": "https://official.nba.com/nba-injury-report-2025-26-season/", "source_terms": "https://www.nba.com/termsofuse", "license_note": "Parser code is MIT; this does not license NBA content. NBA is the source of factual report entries; raw PDFs are not redistributed.",
        "readiness": "Research tables; player identities unresolved, timestamped web availability not independently verified, and a single daily snapshot is incomplete intraday coverage",
        "limitations": ["Do not infer healthy or available from absence; NOT YET SUBMITTED is retained explicitly.", "A 17:30 report may be later than an early game; require header_precedes_game_start and a stricter actual bet cutoff.", "Future model joins must resolve player IDs independently; no fuzzy name match is generated here.", "Header timestamps verify the report label, not an immutable historical publication audit.", "No forecast/training/holdout tuning is performed.", "Error reports and quarantined rows are retained in the remote inventory; missing PDFs are not invented."]}
    (output_dir / "enrichment_summary.json").write_text(json.dumps(summary, indent=2))
    (output_dir / "schema.json").write_text(json.dumps(schema, indent=2))
    (output_dir / "source_manifest.json").write_text(json.dumps({"reports": manifests, "baseline": baseline_provenance, "parser_license": license_provenance}, indent=2))
    print(json.dumps({k: summary[k] for k in ["requested_reports", "parsed_reports", "source_not_found_reports", "error_reports", "rows", "quarantined_rows", "mapped_game_rows", "header_precedes_game_rows"]}, indent=2), flush=True)
    if not parsed_reports:
        raise RuntimeError("No reports successfully parsed; inspect aggregate inventory")


if __name__ == "__main__":
    main()
