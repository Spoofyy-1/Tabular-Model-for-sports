"""Bounded hosted-only historical OSM court-infrastructure candidate pilot."""
import argparse
import contextlib
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import tarfile
import time
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

TAG = "extras-36173901119-1"
ARCHIVE = "csv-extra-tennis-context.tar.gz"
URL = "https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/" + TAG + "/" + ARCHIVE
ARCHIVE_BYTES = 758763
ARCHIVE_SHA256 = "d78cffca5132a544cbd9c71fcf9e37b1048f02d87357f49d3cebf648af727cc2"
MEMBER = "data/extras/tennis_context/wikidata_cc0/places.csv.gz"
MEMBER_BYTES = 4322
MEMBER_SHA256 = "e560956727a23ee0d662c141be60d0ee4c53e500445435b9f452af53b2795811"
ENDPOINT = "https://overpass-api.de/api/interpreter"
SNAPSHOTS = ("2020-01-01T00:00:00Z", "2024-01-01T00:00:00Z")
MAX_BYTES = 15_000_000
UA = "SportsPropsCourtPilot/1.0 (https://github.com/kennynakao/Tabular-Model-for-sports; bounded research)"
FACILITY = re.compile(r"\b(?:club|cent(?:er|re)|stadium|complex|court|courts|facility|facilities)\b", re.I)
EXCLUDE = re.compile(r"\b(?:open|championships?|tournaments?|cup|city|province|municipality)\b", re.I)
PLACE_COLUMNS = ["place_coordinate_id", "place_id", "place_label", "latitude", "longitude", "selection_rule", "historical_place_assignment_verified", "source_license"]
COURT_COLUMNS = ["place_coordinate_id", "osm_type", "osm_id", "osm_version", "osm_edit_timestamp_utc", "snapshot_utc", "latitude", "longitude", "surface", "indoor", "covered", "lit", "venue_association_status", "historical_venue_assignment_verified", "match_court_assignment_verified", "roof_operation_observed", "night_session_observed", "automatic_training_join_allowed", "source_license", "source_query_sha256", "source_response_sha256", "retrieved_at_utc"]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_without_timezone")
    return parsed.astimezone(timezone.utc)


def facility_label(label):
    return bool(re.search(r"\btennis\b", label, re.I) and FACILITY.search(label) and not EXCLUDE.search(label))


def select_places(rows, maximum=3):
    """Pure conservative label screening; never substitutes event/city labels."""
    if not 1 <= maximum <= 3:
        raise ValueError("maximum_places_outside_pilot_bound")
    required = {"place_coordinate_id", "place_id", "placeLabel", "latitude", "longitude", "source_geometry_valid"}
    candidates = []
    for row in rows:
        if not required.issubset(row):
            raise ValueError("source_place_schema_missing")
        label = str(row["placeLabel"] or "")
        if str(row["source_geometry_valid"]).lower() != "true" or not facility_label(label):
            continue
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
        except (ValueError, TypeError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon) and -80 <= lat <= 80 and -179.9 <= lon <= 179.9):
            continue
        if not re.fullmatch(r"Q[1-9][0-9]*", row["place_id"] or "") or not row["place_coordinate_id"]:
            continue
        candidates.append({"place_coordinate_id": row["place_coordinate_id"], "place_id": row["place_id"],
            "place_label": label, "latitude": lat, "longitude": lon,
            "selection_rule": "place_label_tennis_and_facility_term_excluding_event_city_terms",
            "historical_place_assignment_verified": False, "source_license": "CC0-1.0"})
    selected, coordinates, identifiers, place_ids = [], set(), set(), set()
    for row in sorted(candidates, key=lambda x: (x["place_id"], x["place_coordinate_id"])):
        coordinate = (round(row["latitude"], 7), round(row["longitude"], 7))
        if coordinate in coordinates or row["place_coordinate_id"] in identifiers or row["place_id"] in place_ids:
            continue
        coordinates.add(coordinate)
        identifiers.add(row["place_coordinate_id"])
        place_ids.add(row["place_id"])
        selected.append(row)
        if len(selected) == maximum:
            break
    return selected


def bbox(place):
    # Full width and height approximately 600 m; below the 1 km pilot bound.
    lat, lon = place["latitude"], place["longitude"]
    dlat, dlon = 0.3 / 111.32, 0.3 / (111.32 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def query_for(place, snapshot):
    if snapshot not in SNAPSHOTS:
        raise ValueError("snapshot_outside_pilot_allowlist")
    bounds = ",".join(format(x, ".7f") for x in bbox(place))
    return '[out:json][timeout:20][maxsize:16777216][date:"' + snapshot + '"];nwr["sport"="tennis"]["leisure"="pitch"](' + bounds + ');out meta center;'


class Budget:
    def __init__(self):
        self.bytes = 0
        self.http_requests = 0

    def request(self):
        if self.http_requests >= 10:  # Four archive hops plus six Overpass calls.
            raise ValueError("http_request_budget_exceeded")
        self.http_requests += 1

    def consume(self, size):
        self.bytes += size
        if self.bytes > MAX_BYTES:
            raise ValueError("source_byte_budget_exceeded")


@contextlib.contextmanager
def deadline():
    """Linux hosted runner wall-clock deadline, including slow streamed bodies."""
    def expired(_signum, _frame):
        raise TimeoutError("source_request_30_second_deadline")
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def fetch(url, budget, limit, query=None):
    require_github_hosted_runner()
    method = requests.get if query is None else requests.post
    kwargs = {} if query is None else {"data": {"data": query}}
    with deadline():
        for hop in range(4):
            if query is None:
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"} or parsed.port not in (None, 443) or parsed.username or parsed.password:
                    raise ValueError("archive_redirect_destination_disallowed")
            budget.request()
            with method(url, headers={"User-Agent": UA, "Accept": "application/json" if query else "application/octet-stream"}, stream=True, timeout=(5, 20), allow_redirects=False, **kwargs) as response:
                # Only archive redirects are followed, at most three trusted hops.
                if query is None and response.status_code in {301, 302, 303, 307, 308}:
                    if hop == 3 or not response.headers.get("Location"):
                        raise ValueError("archive_redirect_limit_or_location_failure")
                    url = urljoin(url, response.headers["Location"])
                    continue
                # No retries, including Overpass redirects, 403 and 429.
                if response.status_code != 200:
                    raise ValueError("http_status_" + str(response.status_code))
                result = bytearray()
                for chunk in response.iter_content(65536):
                    budget.consume(len(chunk))
                    result.extend(chunk)
                    if len(result) > limit:
                        raise ValueError("individual_source_byte_budget_exceeded")
                break
    return bytes(result)


def source_places(raw):
    """No extraction to disk; only the exact hash-pinned CSV member is read."""
    if len(raw) != ARCHIVE_BYTES or sha(raw) != ARCHIVE_SHA256:
        raise ValueError("source_archive_integrity_failure")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        members = []
        for item in archive:
            path = PurePosixPath(item.name)
            if path.is_absolute() or ".." in path.parts or not (item.isfile() or item.isdir()):
                raise ValueError("unsafe_archive_member")
            if item.name == MEMBER:
                members.append(item)
        if len(members) != 1 or members[0].size != MEMBER_BYTES:
            raise ValueError("source_member_contract_failure")
        compressed = archive.extractfile(members[0]).read(MEMBER_BYTES + 1)
    if sha(compressed) != MEMBER_SHA256:
        raise ValueError("source_member_integrity_failure")
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        body = stream.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("source_csv_expansion_bound")
    return list(csv.DictReader(io.StringIO(body.decode("utf-8"))))


def court_rows(payload, place, snapshot, evidence):
    """Pure metadata transformation; omit all mapper identity fields."""
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list) or payload.get("remark"):
        raise ValueError("overpass_response_incomplete_or_invalid")
    rows, rejected, identity_counts = [], {}, {}
    for element in payload["elements"]:
        if isinstance(element, dict) and isinstance(element.get("type"), str) and type(element.get("id")) is int:
            key = (element["type"], element["id"])
            identity_counts[key] = identity_counts.get(key, 0) + 1
    for element in payload["elements"]:
        reason = None
        try:
            if not isinstance(element, dict):
                raise ValueError("malformed_element_metadata")
            tags = element["tags"]
            if not isinstance(tags, dict):
                raise ValueError("malformed_element_tags")
            if tags.get("sport") != "tennis" or tags.get("leisure") != "pitch":
                raise ValueError("unexpected_element_tags")
            kind, number, version = element["type"], element["id"], element["version"]
            if kind not in {"node", "way", "relation"} or type(number) is not int or number <= 0 or type(version) is not int or version <= 0:
                raise ValueError("invalid_element_identity")
            if identity_counts[(kind, number)] > 1:
                raise ValueError("duplicate_element_identity")
            edited = stamp(element["timestamp"])
            if edited > stamp(snapshot):
                raise ValueError("edit_timestamp_after_snapshot")
            centre = element if kind == "node" else element.get("center", {})
            lat, lon = float(centre["lat"]), float(centre["lon"])
            south, west, north, east = bbox(place)
            if not (south <= lat <= north and west <= lon <= east):
                raise ValueError("center_outside_query_box_or_invalid")
            for key in ("surface", "indoor", "covered", "lit"):
                if key in tags and not isinstance(tags[key], str):
                    raise ValueError("invalid_tag_type")
            rows.append({"place_coordinate_id": place["place_coordinate_id"], "osm_type": kind, "osm_id": str(number),
                "osm_version": version, "osm_edit_timestamp_utc": edited.isoformat(), "snapshot_utc": snapshot,
                "latitude": lat, "longitude": lon, **{key: tags.get(key) for key in ("surface", "indoor", "covered", "lit")},
                "venue_association_status": "spatial_candidate_unverified", "historical_venue_assignment_verified": False,
                "match_court_assignment_verified": False, "roof_operation_observed": False, "night_session_observed": False,
                "automatic_training_join_allowed": False, "source_license": "ODbL-1.0", **evidence})
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else "malformed_element_metadata"
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
    return rows, rejected


def write_csv(path, rows, columns):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as target, gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed:
        with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
            writer = csv.DictWriter(text, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: r"\N" if row.get(key) is None else row[key] for key in columns})


def package(output, destination):
    require_github_hosted_runner()
    destination.mkdir(parents=True, exist_ok=True)
    inventory = []
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise ValueError("output_symlink_disallowed")
        if path.is_file():
            inventory.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())})
    archive = destination / "csv-historical-osm-court-infrastructure.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=1) as stream:
        stream.add(output, arcname="data/court_context")
    manifest = {"archive": archive.name, "bytes": archive.stat().st_size, "sha256": sha(archive.read_bytes()), "files": inventory,
                "licenses": ["ODbL-1.0", "CC0-1.0"], "automatic_training_join_allowed": False}
    (destination / "court_context_asset_manifest.json").write_text(json.dumps(manifest, indent=2))
    (destination / "court_context_summary.json").write_text((output / "summary.json").read_text())


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/court_context")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("output_must_be_empty_to_prevent_stale_rows")
    budget, errors, sources, rows, selected = Budget(), [], [], [], []
    try:
        raw = fetch(URL, budget, ARCHIVE_BYTES)
        selected = select_places(source_places(raw))
        sources.append({"source_url": URL, "release_tag": TAG, "archive_sha256": ARCHIVE_SHA256, "member": MEMBER,
                        "member_sha256": MEMBER_SHA256, "source_license": "CC0-1.0", "retrieved_at_utc": utc_now()})
        del raw
    except (requests.RequestException, TimeoutError, ValueError, OSError, tarfile.TarError) as exc:
        errors.append({"stage": "source_places", "error_class": type(exc).__name__, "reason": safe_reason(exc)})
    attempted, succeeded, service_denied = 0, 0, False
    for place in selected:
        for snapshot in SNAPSHOTS:
            if service_denied:
                continue
            if budget.bytes >= MAX_BYTES:
                errors.append({"stage": "overpass", "reason": "source_byte_budget_exhausted"})
                continue
            if attempted:
                time.sleep(2)
            attempted += 1
            query = query_for(place, snapshot)
            record = {"source_url": ENDPOINT, "place_coordinate_id": place["place_coordinate_id"], "snapshot_utc": snapshot,
                      "query": query, "query_sha256": sha(query.encode()), "source_license": "ODbL-1.0"}
            try:
                raw = fetch(ENDPOINT, budget, 2_000_000, query)
                evidence = {"source_query_sha256": record["query_sha256"], "source_response_sha256": sha(raw), "retrieved_at_utc": utc_now()}
                derived, rejected = court_rows(json.loads(raw), place, snapshot, evidence)
                rows.extend(derived)
                succeeded += 1
                record.update({"status": "partial_rejections" if rejected else "complete", "bytes": len(raw), "sha256": sha(raw),
                               "rows": len(derived), "rejected_elements": rejected, "retrieved_at_utc": evidence["retrieved_at_utc"]})
                if rejected:
                    errors.append({"stage": "element_validation", "snapshot_utc": snapshot, "rejection_counts": rejected})
            except (requests.RequestException, TimeoutError, ValueError, OSError) as exc:
                record.update({"status": "failed", "error_class": type(exc).__name__, "reason": safe_reason(exc)})
                errors.append({"stage": "overpass", "snapshot_utc": snapshot, "error_class": type(exc).__name__, "reason": safe_reason(exc)})
                if safe_reason(exc) in {"http_status_403", "http_status_429"}:
                    service_denied = True
            sources.append(record)
    rows.sort(key=lambda row: (row["snapshot_utc"], row["place_coordinate_id"], row["osm_type"], int(row["osm_id"])))
    output = args.output
    write_csv(output / "wikidata_cc0/selected_places.csv.gz", selected, PLACE_COLUMNS)
    write_csv(output / "osm_odbl/court_snapshots.csv.gz", rows, COURT_COLUMNS)
    (output / "wikidata_cc0/LICENSE.txt").write_text("Wikidata structured data: CC0 1.0. https://www.wikidata.org/wiki/Wikidata:Licensing . Source release and member hashes in sources.json. Facility selection uses labels only; historical identity remains unverified.\n")
    (output / "osm_odbl/LICENSE.txt").write_text("Copyright OpenStreetMap contributors. Data made available under ODbL 1.0: https://opendatacommons.org/licenses/odbl/1-0/ . Attribution: https://www.openstreetmap.org/copyright . Changes: bounded historical selection, selected tag/metadata normalization and candidate spatial association. Retain attribution and applicable share-alike terms. No roof-operation or match-assignment claim.\n")
    schema = {"csv_null_encoding": r"\N", "empty_string_is_distinct_from_null": True, "tables": {"wikidata_cc0/selected_places.csv.gz": PLACE_COLUMNS, "osm_odbl/court_snapshots.csv.gz": COURT_COLUMNS},
              "id_columns_are_strings": ["place_coordinate_id", "place_id", "osm_id"], "timestamp_precision": "timezone_qualified",
              "missing_tag_means": "unknown", "false_observed_flags_mean": "not_observed_by_this_source_not_confirmed_false",
              "snapshot_is": "historical_map_database_state_not_verified_facility_effective_date",
              "center_is": "node_coordinate_or_way_relation_bounding_box_center_not_verified_match_court"}
    status = ("source_failed" if errors else "no_defensible_places") if not selected else "partial" if errors else "complete"
    summary = {"status": status, "selected_places": len(selected), "overpass_requests_attempted": attempted, "overpass_requests_succeeded": succeeded,
               "overpass_requests_not_attempted": len(selected) * len(SNAPSHOTS) - attempted, "service_denied_stop": service_denied,
               "total_http_requests": budget.http_requests, "total_http_request_limit": 10,
               "court_snapshot_rows": len(rows), "source_bytes": budget.bytes, "source_byte_limit": MAX_BYTES, "snapshot_dates": list(SNAPSHOTS),
               "row_unit": "OSM_element_times_candidate_place_times_snapshot_not_distinct_physical_courts",
               "missing_tags": {key: sum(row[key] is None for row in rows) for key in ("surface", "indoor", "covered", "lit")},
               "errors": errors, "automatic_training_join_allowed": False, "no_absence_inference": True,
               "historical_venue_assignment_verified": False, "created_at_utc": utc_now()}
    for name, value in [("schema.json", schema), ("sources.json", sources), ("summary.json", summary)]:
        (output / name).write_text(json.dumps(value, indent=2))
    package(output, args.dist)
    print(json.dumps(summary), flush=True)


def safe_reason(exc):
    value = str(exc)
    return value if re.fullmatch(r"[a-z_0-9]+", value) else "source_request_or_schema_failure"


if __name__ == "__main__":
    main()
