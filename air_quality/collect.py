"""Bounded EPA AirData county-day AQI archive. Real I/O is GitHub-hosted only."""
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tarfile
import time
from urllib.parse import urljoin, urlsplit
import zipfile

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

YEARS = tuple(range(2000, 2026))
BASE = "https://aqs.epa.gov/aqsweb/airdata/"
MAX_REQUESTS, MAX_BYTES, ZIP_BYTES, CSV_BYTES = 60, 100_000_000, 5_000_000, 60_000_000
WALL_SECONDS, FETCH_SECONDS = 600, 540
NULL = r"\N"
PERMISSION = "https://www.epa.gov/outdoor-air-quality-data/do-i-need-request-permission-use-monitoring-data-and-graphics-airdata"
CATALOG = BASE + "download_files.html"
CATEGORIES = ["Good", "Moderate", "Unhealthy for Sensitive Groups", "Unhealthy", "Very Unhealthy", "Hazardous"]
REQUIRED = {"state code", "county code", "date", "aqi"}


class HardWallTimeout(TimeoutError):
    pass


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def header_key(value):
    return " ".join(value.strip().casefold().split())


def retry_after(value):
    result = {"retry_after_seconds": None, "retry_after_utc": None}
    if not isinstance(value, str) or len(value) > 128 or "\n" in value or "\r" in value:
        return result
    if re.fullmatch(r"[0-9]{1,10}", value.strip()):
        result["retry_after_seconds"] = int(value)
    else:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is not None:
                result["retry_after_utc"] = parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            pass
    return result


class Budget:
    def __init__(self):
        self.requests = self.bytes = 0
        self.deadline = time.monotonic() + FETCH_SECONDS
        self.records = []

    @staticmethod
    def allowed(url):
        part = urlsplit(url)
        if (part.scheme != "https" or part.hostname != "aqs.epa.gov" or part.port not in (None, 443)
                or part.username or part.password or part.query or part.fragment
                or not re.fullmatch(r"/aqsweb/airdata/daily_aqi_by_county_20\d{2}\.zip", part.path)):
            raise ValueError("source_or_redirect_not_allowlisted")

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("collection_deadline")
        if self.requests >= MAX_REQUESTS:
            raise RuntimeError("request_budget")
        if self.bytes >= MAX_BYTES:
            raise RuntimeError("source_byte_budget")

    def fetch(self, year):
        require_github_hosted_runner()
        if year not in YEARS:
            raise ValueError("year_not_allowlisted")
        original = url = BASE + f"daily_aqi_by_county_{year}.zip"
        with requests.Session() as session:
            session.headers.update({"User-Agent": "sports-props-research/1.0 (+https://github.com/kennynakao/Tabular-Model-for-sports)", "Accept-Encoding": "identity"})
            for hop in range(3):
                self.allowed(url)
                # Redirects may not switch the requested annual file.
                if urlsplit(url).path != urlsplit(original).path:
                    raise ValueError("redirect_changed_source_year")
                self.check()
                self.requests += 1
                remaining = max(.1, min(30., self.deadline - time.monotonic()))
                with session.get(url, stream=True, allow_redirects=False, timeout=(min(10., remaining), remaining)) as response:
                    record = {"source_url": original, "year": year, "attempt": self.requests,
                              "http_status": int(response.status_code), "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                              **retry_after(response.headers.get("Retry-After"))}
                    self.records.append(record)
                    modified = retry_after(response.headers.get("Last-Modified"))["retry_after_utc"]
                    record["last_modified_utc_source"] = modified
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location or hop == 2:
                            raise ValueError("missing_or_excessive_redirect")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise RuntimeError("http_status_failure")
                    body = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        self.bytes += len(chunk)
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError("collection_deadline")
                        if len(body) + len(chunk) > ZIP_BYTES or self.bytes > MAX_BYTES:
                            raise RuntimeError("source_byte_budget")
                        body.extend(chunk)
                    result = bytes(body)
                    record.update({"bytes": len(result), "sha256": sha256(result), "hash_basis": "HTTP body after transfer/content decoding"})
                    return result, record
        raise RuntimeError("no_source_response")


def extract_csv(body, year):
    """Pure validation helper: exact one regular member, bounded decompression."""
    if len(body) > ZIP_BYTES:
        raise ValueError("zip_byte_budget")
    expected = f"daily_aqi_by_county_{year}.csv"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != expected:
            raise ValueError("unexpected_or_duplicate_zip_members")
        item = members[0]
        mode = item.external_attr >> 16
        if item.is_dir() or item.flag_bits & 1 or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
            raise ValueError("nonregular_or_encrypted_zip_member")
        if item.file_size > CSV_BYTES or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("csv_byte_budget_or_compression")
        with archive.open(item) as stream:
            result = stream.read(CSV_BYTES + 1)
        if len(result) > CSV_BYTES or len(result) != item.file_size:
            raise ValueError("csv_size_mismatch")
        return result


def parse_csv(body):
    if len(body) > CSV_BYTES:
        raise ValueError("csv_byte_budget")
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig"), newline=""), strict=True)
    try:
        headers = next(reader)
    except StopIteration:
        raise ValueError("missing_csv_header") from None
    keys = [header_key(value) for value in headers]
    if not keys or any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("missing_or_ambiguous_csv_headers")
    if not REQUIRED.issubset(keys):
        raise ValueError("missing_required_csv_headers")
    rows = []
    for row in reader:
        if len(row) != len(headers):
            raise ValueError("csv_row_width_mismatch")
        if NULL in row:
            raise ValueError("source_contains_reserved_null_token")
        rows.append(row)
    frame = pd.DataFrame(rows, columns=keys, dtype="string")
    return frame, dict(zip(keys, headers))


def integers(values):
    text = values.str.strip()
    numeric = pd.to_numeric(text, errors="coerce")
    # Nullable signed integers bound the storage type, not the AQI scale.
    valid = text.str.fullmatch(r"\+?[0-9]+(?:\.0+)?") & numeric.notna() & numeric.ge(0) & numeric.lt(9_223_372_036_854_775_000) & numeric.mod(1).eq(0)
    return numeric.where(valid).astype("Int64"), valid


def normalize(frame, year):
    if not REQUIRED.issubset(frame.columns) or not frame.columns.is_unique:
        raise ValueError("missing_or_duplicate_required_columns")
    source_columns = list(frame.columns)
    frame = frame.drop_duplicates().reset_index(drop=True).copy()
    # Each source column survives in raw CSV and as an unmodified string here.
    renamed = {col: "source_" + re.sub(r"[^a-z0-9]+", "_", col).strip("_") for col in source_columns}
    if len(set(renamed.values())) != len(renamed):
        raise ValueError("ambiguous_normalized_source_headers")
    result = frame.rename(columns=renamed)
    state, county = frame["state code"].str.strip(), frame["county code"].str.strip()
    codes_valid = (state.str.fullmatch(r"[0-9]{1,2}") & county.str.fullmatch(r"[0-9]{1,3}")
                   & ~state.str.fullmatch(r"0+") & ~county.str.fullmatch(r"0+"))
    result["aqs_state_code"] = state.str.zfill(2).where(codes_valid)
    result["aqs_county_code"] = county.str.zfill(3).where(codes_valid)
    result["aqs_county_key"] = (result.aqs_state_code + result.aqs_county_code).where(codes_valid)
    dates = frame["date"].str.strip()
    parsed = pd.to_datetime(dates.where(dates.str.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")), format="%Y-%m-%d", errors="coerce")
    date_valid = parsed.notna()
    year_valid = parsed.dt.year.eq(year)
    result["observation_date"] = parsed.dt.strftime("%Y-%m-%d").astype("string")
    result["aqi"], aqi_valid = integers(frame["aqi"])
    result["source_year"] = year
    partition = pd.Series(pd.NA, index=frame.index, dtype="string")
    partition = partition.mask(parsed.dt.year.lt(2024), "fit_pre_2024")
    partition = partition.mask(parsed.dt.year.eq(2024), "calibration_2024")
    result["calendar_partition"] = partition.mask(parsed.dt.year.ge(2025), "holdout_2025_plus")
    expected = pd.Series(pd.NA, index=frame.index, dtype="string")
    for upper, label in reversed(list(zip([50, 100, 150, 200, 300], CATEGORIES[:5]))):
        expected = expected.mask(result.aqi.le(upper).fillna(False), label)
    expected = expected.mask(result.aqi.gt(300).fillna(False), "Hazardous")
    result["category_from_aqi_current_band"] = expected
    if "category" in frame:
        category = frame.category.map(header_key)
        mapped = category.map({header_key(v): v for v in CATEGORIES}).astype("string")
        status = pd.Series("unrecognized", index=frame.index, dtype="string")
        status = status.mask(category.eq(""), "missing")
        status = status.mask(mapped.notna() & ~aqi_valid, "aqi_unavailable")
        status = status.mask(mapped.notna() & aqi_valid & mapped.eq(expected).fillna(False), "matches_current_band")
        status = status.mask(mapped.notna() & aqi_valid & mapped.ne(expected).fillna(False), "mismatch_current_band")
        result["category_validation"] = status
    else:
        result["category_validation"] = "column_absent"
    if "number of sites reporting" in frame:
        result["number_of_sites_reporting"], sites_valid = integers(frame["number of sites reporting"])
        result["reporting_sites_validation"] = pd.Series("invalid", index=frame.index, dtype="string").mask(frame["number of sites reporting"].eq(""), "missing").mask(sites_valid, "valid")
        result["reporting_sites_validation"] = result.reporting_sites_validation.mask(result.number_of_sites_reporting.eq(0).fillna(False), "zero_reporters_inconsistent_with_aqi")
    else:
        result["number_of_sites_reporting"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
        result["reporting_sites_validation"] = "column_absent"
    result["historical_available_asof_verified"] = False
    reason = pd.Series("", index=frame.index, dtype="string")
    for mask, label in [(~codes_valid, "invalid_aqs_codes"), (~date_valid, "invalid_calendar_date"), (date_valid & ~year_valid, "source_year_date_mismatch"), (~aqi_valid, "invalid_or_missing_aqi")]:
        reason = reason.mask(mask, reason + label + ";")
    usable_key = codes_valid & date_valid
    # Every remaining duplicate key is a conflict, even if only an optional field differs.
    conflict = result.loc[usable_key].duplicated(["aqs_county_key", "observation_date"], keep=False).reindex(result.index, fill_value=False)
    reason = reason.mask(conflict, reason + "conflicting_county_date;")
    result["exclusion_reason"] = reason.str.rstrip(";")
    sort = ["aqs_county_key", "observation_date"]
    canonical = result.loc[reason.eq("")].sort_values(sort, kind="stable").reset_index(drop=True)
    quarantine = result.loc[reason.ne("")].sort_values(sort, kind="stable", na_position="last").reset_index(drop=True)
    stats = {"deduplicated_rows": len(result), "canonical_rows": len(canonical), "quarantined_rows": len(quarantine),
             "conflicting_county_date_rows": int(conflict.sum()), "counties": int(canonical.aqs_county_key.nunique()),
             "first_date": None if canonical.empty else str(canonical.observation_date.min()),
             "last_date": None if canonical.empty else str(canonical.observation_date.max()),
             "category_validation_counts": {str(k): int(v) for k, v in result.category_validation.value_counts().items()}}
    return canonical, quarantine, stats


def safe_path(path):
    require_github_hosted_runner()
    path = Path(path)
    if any(parent.is_symlink() for parent in [path, *path.parents]):
        raise ValueError("publication_symlink")
    return path


def write_bytes(path, body):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body)


def write_json(path, value):
    write_bytes(path, (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


def write_frame(path, frame):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1, mtime=0) as zipped:
            with io.TextIOWrapper(zipped, encoding="utf-8", newline="") as text:
                frame.to_csv(text, index=False, na_rep=NULL, lineterminator="\n")


def schema(frame):
    return {"columns": [{"name": str(col), "dtype": str(frame[col].dtype)} for col in frame],
            "null_token": NULL, "empty_text": "preserved; not null", "id_columns": ["aqs_state_code", "aqs_county_code", "aqs_county_key"],
            "date_semantics": "observation_date is a calendar day; no timezone, intraday time or pregame availability is inferred"}


def timeout_handler(*_):
    raise HardWallTimeout("hard_wall_deadline")


def main():
    require_github_hosted_runner()
    output, dist = ROOT / "data/air_quality", ROOT / "dist"
    safe_path(output)
    if output.exists():
        raise RuntimeError("refuse_existing_output_directory")
    output.mkdir(parents=True)
    safe_path(dist).mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(WALL_SECONDS)
    budget, files, reports, schemas, errors, raw_audits = Budget(), [], [], {}, [], []
    try:
        for year in YEARS:
            try:
                zipped, record = budget.fetch(year)
                body = extract_csv(zipped, year)
                raw_path = output / "raw" / f"daily_aqi_by_county_{year}.csv.gz"
                write_bytes(raw_path, gzip.compress(body, compresslevel=1, mtime=0))
                files.append(raw_path)
                raw_relative = str(raw_path.relative_to(output))
                raw_schema = {"representation": "exact original CSV bytes, gzip compressed without changing cells or headers",
                              "source_csv_bytes": len(body), "source_csv_sha256": sha256(body),
                              "null_token": None, "null_semantics": "No source null token assumed; original empty text preserved verbatim",
                              "headers_validated": False}
                schemas[raw_relative] = raw_schema
                raw_audits.append({"year": year, "path": raw_relative, "source_csv_sha256": sha256(body)})
                frame, headers = parse_csv(body)
                raw_schema.update({"headers_validated": True, "headers": list(headers.values()),
                                   "columns": [{"name": name, "dtype": "source CSV text"} for name in headers.values()],
                                   "rows": len(frame)})
                input_rows = len(frame)
                canonical, quarantine, counts = normalize(frame, year)
                counts.update({"year": year, "input_rows": input_rows, "identical_duplicate_rows_removed": input_rows - counts["deduplicated_rows"],
                               "source_csv_sha256": sha256(body), "source_csv_bytes": len(body), "source_headers": headers,
                               "source_record": record, "status": "complete" if len(canonical) else "no_valid_rows"})
                year_files, year_schemas = [], {}
                for name, table in [("county_daily_aqi", canonical), ("quarantine", quarantine)]:
                    path = output / str(year) / f"{name}.csv.gz"
                    write_frame(path, table)
                    year_files.append(path)
                    year_schemas[str(path.relative_to(output))] = schema(table)
                # Only publish a normalized year after both files have been written.
                files.extend(year_files)
                schemas.update(year_schemas)
                reports.append(counts)
                del frame, canonical, quarantine, body, zipped
                if counts["canonical_rows"] == 0:
                    raise RuntimeError("zero_valid_rows_in_source_year")
            except Exception as exc:
                if isinstance(exc, HardWallTimeout):
                    raise
                # Never echo source bodies, arbitrary HTTP exception URLs, or rows.
                code = str(exc) if re.fullmatch(r"[a-z_]{1,80}", str(exc)) else "unclassified_source_failure"
                errors.append({"year": year, "type": type(exc).__name__, "code": code})
                break  # No automatic retry or requests after an error.
        license_path = output / "PUBLIC_DOMAIN_ATTRIBUTION.txt"
        write_bytes(license_path, ("EPA Air Quality System ambient monitoring data are public domain.\nPermission: " + PERMISSION + "\nSource catalog: " + CATALOG + "\nU.S. Environmental Protection Agency, AirData/AQS. Source files may be revised.\n").encode())
        files.append(license_path)
        summary = {"status": "complete" if len(reports) == len(YEARS) and not errors else "partial" if any(v["canonical_rows"] for v in reports) else "audit_only",
                   "requested_years": list(YEARS), "completed_years": [v["year"] for v in reports if v["status"] == "complete"],
                   "years": reports, "errors": errors, "http_attempts": budget.requests, "source_bytes": budget.bytes,
                   "raw_audit_files": raw_audits, "code_commit_sha": os.environ.get("GITHUB_SHA"),
                   "training_performed": False, "game_joins": 0,
                   "source_requests": budget.records, "canonical_rows": sum(v["canonical_rows"] for v in reports),
                   "quarantined_rows": sum(v["quarantined_rows"] for v in reports),
                   "semantics": "Retrospective county-day AQI context; no stadium/court exposure, smoke attribution, concentration, forecast or game join. Missing days are not generated.",
                   "temporal_limits": "Source historical availability and AQI threshold version are unverified. Calendar partitions do not establish known UTC times or pregame availability.",
                   "identifier_limits": "AQS state/county keys are strings; no external FIPS/venue/county crosswalk has been verified.",
                   "license": {"name": "US EPA monitoring data: public domain", "permission_url": PERMISSION},
                   "budgets": {"max_http_attempts": MAX_REQUESTS, "max_source_bytes": MAX_BYTES, "max_zip_bytes": ZIP_BYTES, "max_csv_bytes_per_year": CSV_BYTES, "wall_seconds": WALL_SECONDS}}
        for name, value in [("summary.json", summary), ("schema.json", {"tables": schemas})]:
            path = output / name
            write_json(path, value)
            files.append(path)
        manifest = {"files": [{"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha256(safe_path(path).read_bytes())} for path in sorted(files)], "inventory_policy": "only explicitly generated files; no directory traversal"}
        archive_path = safe_path(dist / "csv-air-quality.tar.gz")
        pending_archive = safe_path(output / "archive.pending")
        if archive_path.exists():
            raise RuntimeError("refuse_existing_archive")
        with pending_archive.open("xb") as stream:
            with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=1, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w|") as archive:
                    for path in sorted(files):
                        safe_path(path)
                        if not path.is_file():
                            raise ValueError("publication_nonregular_file")
                        archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
        pending_archive.replace(archive_path)
        manifest["archive"] = {"name": archive_path.name, "bytes": archive_path.stat().st_size, "sha256": sha256(archive_path.read_bytes())}
        write_json(dist / "air_quality_summary.json", summary)
        write_json(dist / "air_quality_schema.json", {"tables": schemas})
        write_json(dist / "air_quality_asset_manifest.json", manifest)
        print(json.dumps({"status": summary["status"], "completed_years": len(summary["completed_years"]), "canonical_rows": summary["canonical_rows"], "errors": errors}, sort_keys=True))
        return 0 if summary["status"] == "complete" else 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
