"""Hosted-only, retrospective Wimbledon date alignment; no new weather requests."""
import csv
from datetime import date, datetime, time as daytime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import time
import unicodedata
import re
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

REPO = "kennynakao/Tabular-Model-for-sports"
BASE = "https://github.com/" + REPO + "/releases/download/"
MAX_BYTES, MAX_REQUESTS, MAX_SECONDS = 60_000_000, 24, 480
WEATHER_KEY = "Q41520-565971483706"
COURT, FACILITY, TOURNAMENT = "Q2603183", "Q815369", "Q41520"
CANDIDATES = {"Q128304298": (2024, "Q120716297"), "Q121076423": (2023, "Q113432321")}
PINS = {
    "mcp": {"tag": "tennis-points-36173714850-1", "archive": "csv-tennis-mcp-research-only.tar.gz",
        "manifest": "tennis_points_asset_manifest.json", "bytes": 45_950_657,
        "sha256": "dd23c16cff991bcf1805ad8f97288312dee3c21fc0e513da1f5c1c191ea38f62",
        "prefix": "data/tennis/mcp_research_only/"},
    "weather": {"tag": "extras-36173901119-1", "archive": "csv-extra-tennis-context.tar.gz",
        "manifest": "tennis_context_asset_manifest.json", "bytes": 758_763,
        "sha256": "d78cffca5132a544cbd9c71fcf9e37b1048f02d87357f49d3cebf648af727cc2",
        "prefix": "data/extras/tennis_context/"}}
PROPERTIES = {"P31", "P361", "P585", "P710", "P276", "P625"}
FACT_COLUMNS = ["entity_id", "property_id", "claim_id", "snaktype", "value_json", "rank", "qualifiers_json", "reference_count", "revision", "modified", "retrieved_at_utc", "source_license"]
LABEL_COLUMNS = ["entity_id", "label_en", "aliases_en_json", "revision", "source_license"]
JOIN_COLUMNS = ["match_entity_id", "match_id", "competition_group", "player1_name", "player2_name", "match_local_date", "source_date_precision", "tournament", "round", "court_entity_id", "facility_entity_id", "weather_place_coordinate_id", "court_coordinate_distance_m", "evaluation_split", "timezone_assumption", "date_semantics_assumption", "source_claims_referenced", "venue_geometry_historical_validity_known", "actual_match_start_utc", "actual_match_end_utc", "actual_match_interval_known", "session", "roof_operation", "roof_operation_known", "pregame_available", "automatic_training_join_allowed", "research_only_noncommercial", "license"]
WEATHER_COLUMNS = ["match_entity_id", "match_id", "weather_date_utc", "calendar_day_start_utc", "calendar_day_end_utc", "calendar_overlap_seconds", "context_class", "place_coordinate_id", "place_id", "requested_latitude", "requested_longitude", "grid_latitude", "grid_longitude", "weather_model", "weather_units_json", "actual_match_interval_known", "roof_operation_known", "pregame_available", "automatic_training_join_allowed", "research_only_noncommercial", "weather_license", "joined_license"]
QUARANTINE_COLUMNS = ["match_entity_id", "stage", "reason", "candidate_count"]
DAILY_FIELDS = ["temperature_2m_max", "temperature_2m_min", "temperature_2m_mean", "precipitation_sum", "wind_speed_10m_max", "wind_direction_10m_dominant", "shortwave_radiation_sum", "relative_humidity_2m_mean", "surface_pressure_mean"]
WEATHER_COLUMNS += DAILY_FIELDS


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(value):
    # No accent stripping, surname matching, initials or outcome information.
    return " ".join(unicodedata.normalize("NFC", str(value or "")).casefold().split())


class Excluded(ValueError):
    def __init__(self, reason, count=0):
        self.reason, self.count = reason, count
        super().__init__(reason)


class Remote:
    def __init__(self):
        self.bytes, self.requests, self.started = 0, 0, time.monotonic()
        self.sources = []

    def check(self):
        if self.requests >= MAX_REQUESTS or time.monotonic() - self.started >= MAX_SECONDS:
            raise RuntimeError("request_or_wall_time_budget_exceeded")

    def get(self, url, limit):
        require_github_hosted_runner()
        original, current = url, url
        hosts = {"github.com", "api.github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "www.wikidata.org"}
        for redirect in range(4):
            self.check()
            parsed = urlparse(current)
            if parsed.scheme != "https" or parsed.hostname not in hosts:
                raise RuntimeError("unapproved_source_host")
            self.requests += 1
            remaining = max(1, MAX_SECONDS - (time.monotonic() - self.started))
            with requests.get(current, headers={"User-Agent": "SportsPropsResearch/1.0 (https://github.com/" + REPO + "; noncommercial research)"},
                              stream=True, allow_redirects=False, timeout=(min(10, remaining), min(30, remaining))) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirect == 3:
                        raise RuntimeError("redirect_limit_exceeded")
                    current = urljoin(current, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()  # No retry or access-control bypass.
                length = response.headers.get("Content-Length")
                if length and int(length) > min(limit, MAX_BYTES - self.bytes):
                    raise RuntimeError("declared_source_byte_budget_exceeded")
                output = bytearray()
                for block in response.iter_content(65536):
                    self.bytes += len(block)
                    output.extend(block)
                    if len(output) > limit or self.bytes > MAX_BYTES:
                        raise RuntimeError("source_byte_budget_exceeded")
                    if time.monotonic() - self.started >= MAX_SECONDS:
                        raise RuntimeError("wall_time_budget_exceeded")
                data = bytes(output)
                self.sources.append({"url": original, "bytes": len(data), "sha256": sha(data), "retrieved_at_utc": now(), "http_status": response.status_code})
                return data
        raise RuntimeError("redirect_limit_exceeded")


def archive_members(raw, manifest, prefix, wanted):
    """Validate pinned archive inventory; return only requested small members."""
    entries = manifest.get("files", [])
    declared = {item["path"]: item for item in entries}
    if len(declared) != len(entries) or len(entries) > 5000:
        raise ValueError("ambiguous_or_excessive_archive_inventory")
    observed, selected, total = set(), {}, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("unsafe_archive_member")
            if member.isdir():
                continue
            if not member.isfile() or member.name in observed or not member.name.startswith(prefix):
                raise ValueError("invalid_archive_member")
            observed.add(member.name)
            entry = declared.get(member.name)
            total += member.size
            if entry is None or member.size != entry["bytes"] or total > 150_000_000:
                raise ValueError("archive_inventory_or_size_mismatch")
            if member.name in wanted:
                if member.size > 5_000_000:
                    raise ValueError("selected_member_size_exceeded")
                content = archive.extractfile(member).read(5_000_001)
                if len(content) != member.size or sha(content) != entry["sha256"]:
                    raise ValueError("selected_member_hash_mismatch")
                selected[member.name] = content
    if observed != set(declared) or not set(wanted).issubset(selected):
        raise ValueError("archive_members_missing")
    return selected


def read_csv(raw):
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        plain = stream.read(20_000_001)
    if len(plain) > 20_000_000:
        raise ValueError("decompressed_csv_budget_exceeded")
    reader = csv.DictReader(io.StringIO(plain.decode("utf-8-sig")))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("duplicate_or_missing_csv_headers")
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()) or len(rows) >= 50_000:
            raise ValueError("csv_row_shape_or_count_exceeded")
        rows.append({key: None if value == r"\N" else value for key, value in row.items()})
    return rows


def fetch_bundle(remote, name, wanted):
    pin = PINS[name]
    stem = BASE + pin["tag"] + "/"
    manifest_raw = remote.get(stem + pin["manifest"], 300_000)
    manifest = json.loads(manifest_raw)
    if any(manifest.get(key) != pin[key] for key in ("archive", "bytes", "sha256")):
        raise ValueError("source_pin_manifest_mismatch")
    if name == "mcp" and "CC-BY-NC-SA-4.0" not in manifest.get("licenses", []):
        raise ValueError("mcp_license_missing")
    raw = remote.get(stem + pin["archive"], pin["bytes"])
    if len(raw) != pin["bytes"] or sha(raw) != pin["sha256"]:
        raise ValueError("source_archive_pin_mismatch")
    return archive_members(raw, manifest, pin["prefix"], wanted)


def claims(entity, prop):
    return [claim for claim in entity.get("claims", {}).get(prop, []) if claim.get("rank") != "deprecated" and claim.get("mainsnak", {}).get("snaktype") == "value"]


def value(claim):
    return claim.get("mainsnak", {}).get("datavalue", {}).get("value")


def item_ids(entity, prop):
    return {item.get("id") for claim in claims(entity, prop) if isinstance((item := value(claim)), dict) and item.get("id")}


def require_known_claims(entity, properties):
    for prop in properties:
        active = [claim for claim in entity.get("claims", {}).get(prop, []) if claim.get("rank") != "deprecated"]
        if not active or any(claim.get("mainsnak", {}).get("snaktype") != "value" for claim in active):
            raise Excluded("missing_or_unknown_required_claim_" + prop)


def unique_date(entity):
    require_known_claims(entity, ("P585",))
    records = claims(entity, "P585")
    days = set()
    for claim in records:
        val = value(claim)
        if not isinstance(val, dict) or val.get("precision") != 11 or val.get("calendarmodel", "").rsplit("/", 1)[-1] != "Q1985727":
            raise Excluded("unsupported_match_date_precision_or_calendar")
        # Day precision is a date despite the serialized midnight/Z suffix.
        text = val.get("time", "")
        if not text.startswith("+") or "T" not in text:
            raise Excluded("invalid_date_serialization")
        try:
            days.add(date.fromisoformat(text[1:].split("T")[0]))
        except ValueError:
            raise Excluded("invalid_match_calendar_date")
        if claim.get("qualifiers"):
            raise Excluded("qualified_date_requires_manual_review")
    if len(days) != 1:
        raise Excluded("missing_or_conflicting_match_date", len(days))
    return next(iter(days))


def names(entity):
    vals = [entity.get("labels", {}).get("en", {}).get("value", "")]
    vals.extend(alias.get("value", "") for alias in entity.get("aliases", {}).get("en", []))
    return {normalized(val) for val in vals if len(normalized(val).split()) >= 2}


def candidate(entity_id, entities):
    entity = entities.get(entity_id, {})
    require_known_claims(entity, ("P585", "P710", "P276", "P361", "P31"))
    year, edition = CANDIDATES[entity_id]
    day = unique_date(entity)
    if day.year != year or day >= date(2025, 1, 1):
        raise Excluded("unexpected_candidate_year")
    if item_ids(entity, "P276") != {COURT} or item_ids(entity, "P361") != {edition}:
        raise Excluded("unverified_exact_court_or_tournament_edition")
    types = item_ids(entity, "P31")
    if not types or not any(normalized(entities.get(q, {}).get("labels", {}).get("en", {}).get("value")) == "final" for q in types):
        raise Excluded("unverified_final_round")
    participants = sorted(item_ids(entity, "P710"))
    if len(participants) != 2:
        raise Excluded("participant_count_not_two", len(participants))
    participant_names = [names(entities.get(q, {})) for q in participants]
    if any(not val for val in participant_names) or participant_names[0] & participant_names[1]:
        raise Excluded("missing_or_ambiguous_full_participant_names")
    referenced = all(claim.get("references") for prop in ("P585", "P276", "P710", "P361") for claim in claims(entity, prop))
    return {"entity_id": entity_id, "date": day, "names": participant_names, "referenced": bool(referenced)}


def match_date(value):
    # Source normalizer uses naive calendar dates; reject offset-aware timestamps.
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value)) if "T" in str(value) or " " in str(value) else datetime.combine(date.fromisoformat(str(value)), daytime())
    except ValueError:
        return None
    return parsed.date() if parsed.tzinfo is None and parsed.time() == daytime() else None


def match_candidate(item, matches):
    accepted = []
    for row in matches:
        if row.get("competition_group") != "mens_singles" or normalized(row.get("tournament")) not in {"wimbledon", "wimbledon championships", "the championships, wimbledon"}:
            continue
        if normalized(row.get("round")) not in {"f", "final"} or match_date(row.get("match_date")) != item["date"]:
            continue
        pair = [normalized(row.get("player1_name")), normalized(row.get("player2_name"))]
        same = pair[0] in item["names"][0] and pair[1] in item["names"][1]
        swapped = pair[0] in item["names"][1] and pair[1] in item["names"][0]
        if not (same or swapped):
            continue
        if not row.get("match_id") or normalized(row.get("singles_metadata_eligible")) != "true" or normalized(row.get("source_retirement_or_walkover_flag")) != "false":
            raise Excluded("unsupported_mcp_match_quality")
        if row.get("source_match_date_precision") != "calendar_date":
            raise Excluded("unsupported_mcp_date_precision")
        accepted.append(row)
    if len(accepted) != 1:
        raise Excluded("missing_or_ambiguous_exact_mcp_match", len(accepted))
    selected = accepted[0]
    if sum(row.get("match_id") == selected["match_id"] for row in matches) != 1:
        raise Excluded("duplicate_mcp_match_id")
    return selected


def coordinate(entity):
    coords = set()
    for claim in claims(entity, "P625"):
        val = value(claim)
        if not isinstance(val, dict) or val.get("globe", "").rsplit("/", 1)[-1] != "Q2":
            continue
        lat, lon = float(val["latitude"]), float(val["longitude"])
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            coords.add((lat, lon))
    if len(coords) != 1:
        raise Excluded("missing_or_ambiguous_entity_coordinates", len(coords))
    return next(iter(coords))


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6_371_000 * 2 * math.asin(min(1, math.sqrt(h)))


def validate_place(entities, selected_places, all_places):
    court, tournament = entities.get(COURT, {}), entities.get(TOURNAMENT, {})
    require_known_claims(court, ("P361", "P625"))
    require_known_claims(tournament, ("P276", "P625"))
    if item_ids(court, "P361") != {FACILITY} or item_ids(tournament, "P276") != {FACILITY}:
        raise Excluded("same_facility_identity_not_verified")
    # Selection is by fixed identity; distance cannot choose a substitute venue.
    rows = [row for row in selected_places if row.get("place_coordinate_id") == WEATHER_KEY]
    all_rows = [row for row in all_places if row.get("place_coordinate_id") == WEATHER_KEY]
    if len(rows) != 1 or not all_rows:
        raise Excluded("archived_wimbledon_coordinate_missing_or_ambiguous", len(rows))
    row = rows[0]
    if row.get("place_id") != TOURNAMENT or row.get("entity_id") != TOURNAMENT or normalized(row.get("source_geometry_valid")) != "true":
        raise Excluded("archived_coordinate_identity_or_quality_mismatch")
    coord = float(row["latitude"]), float(row["longitude"])
    if not all(math.isfinite(v) for v in coord) or not (-90 <= coord[0] <= 90 and -180 <= coord[1] <= 180) or any((float(other["latitude"]), float(other["longitude"])) != coord for other in all_rows):
        raise Excluded("archive_coordinate_conflict")
    if distance(coord, coordinate(tournament)) > 2.0:
        raise Excluded("archived_coordinate_differs_from_entity_over_2m")
    separation = distance(coord, coordinate(court))
    if separation > 500:
        raise Excluded("court_coordinate_outside_same_facility_bound")
    return row, separation


def day_overlaps(day):
    zone = ZoneInfo("Europe/London")
    start = datetime.combine(day, daytime(), zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), daytime(), zone).astimezone(timezone.utc)
    bucket = datetime.combine(start.date(), daytime(), timezone.utc)
    rows = []
    while bucket < end:
        seconds = int((min(end, bucket + timedelta(days=1)) - max(start, bucket)).total_seconds())
        if seconds > 0:
            rows.append((bucket, seconds, start, end))
        bucket += timedelta(days=1)
    return rows


def weather_context(item, match, rows, place, units):
    output = []
    for bucket, seconds, start, end in day_overlaps(item["date"]):
        matches = []
        for row in rows:
            try:
                timestamp = datetime.fromisoformat(str(row.get("weather_date_utc")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if timestamp.tzinfo is not None and timestamp.utcoffset() == timedelta(0) and timestamp == bucket:
                matches.append(row)
        if len(matches) != 1:
            raise Excluded("missing_or_duplicate_utc_weather_bucket", len(matches))
        row = matches[0]
        if row.get("place_coordinate_id") != WEATHER_KEY or row.get("place_id") != TOURNAMENT or row.get("weather_model") != "ERA5":
            raise Excluded("weather_identity_or_model_mismatch")
        if (float(row["requested_latitude"]), float(row["requested_longitude"])) != (float(place["latitude"]), float(place["longitude"])):
            raise Excluded("weather_requested_coordinate_mismatch")
        if not all(field in row and isinstance(units.get(field), str) and units[field].strip() for field in DAILY_FIELDS):
            raise Excluded("weather_variable_or_units_schema_missing")
        try:
            # Raw CSV empty text is preserved on ingest; empty numeric observations
            # explicitly mean unknown here, alongside the source's null sentinel.
            values = {field: None if row[field] in (None, "") else float(row[field]) for field in DAILY_FIELDS}
            grid = float(row["grid_latitude"]), float(row["grid_longitude"])
        except (ValueError, TypeError, KeyError):
            raise Excluded("invalid_weather_numeric_or_grid_value")
        if any(number is not None and not math.isfinite(number) for number in values.values()):
            raise Excluded("nonfinite_weather_value")
        if not all(math.isfinite(number) for number in grid) or not (-90 <= grid[0] <= 90 and -180 <= grid[1] <= 180):
            raise Excluded("nonfinite_or_out_of_range_era5_grid_coordinate")
        output.append({"match_entity_id": item["entity_id"], "match_id": match["match_id"], "weather_date_utc": bucket.isoformat(),
            "calendar_day_start_utc": start.isoformat(), "calendar_day_end_utc": end.isoformat(), "calendar_overlap_seconds": seconds,
            "context_class": "calendar_day_overlap_context", **{key: row.get(key) for key in ["place_coordinate_id", "place_id", "requested_latitude", "requested_longitude", "grid_latitude", "grid_longitude", "weather_model"]},
            **values,
            "weather_units_json": json.dumps({field: units[field] for field in DAILY_FIELDS}, sort_keys=True),
            "actual_match_interval_known": False, "roof_operation_known": False, "pregame_available": False,
            "automatic_training_join_allowed": False, "research_only_noncommercial": True,
            "weather_license": "CC-BY-4.0; collected under free API noncommercial terms", "joined_license": "CC-BY-NC-SA-4.0"})
    return output


def fact_rows(entities, retrieved):
    facts, labels = [], []
    for qid, entity in sorted(entities.items()):
        labels.append({"entity_id": qid, "label_en": entity.get("labels", {}).get("en", {}).get("value"),
            "aliases_en_json": json.dumps([x.get("value") for x in entity.get("aliases", {}).get("en", [])]),
            "revision": entity.get("lastrevid"), "source_license": "CC0-1.0"})
        for prop in sorted(PROPERTIES):
            for claim in entity.get("claims", {}).get(prop, []):
                if claim.get("rank") == "deprecated":
                    continue
                facts.append({"entity_id": qid, "property_id": prop, "claim_id": claim.get("id"), "snaktype": claim.get("mainsnak", {}).get("snaktype"), "value_json": json.dumps(value(claim), sort_keys=True),
                    "rank": claim.get("rank"), "qualifiers_json": json.dumps(claim.get("qualifiers", {}), sort_keys=True),
                    "reference_count": len(claim.get("references", [])), "revision": entity.get("lastrevid"), "modified": entity.get("modified"),
                    "retrieved_at_utc": retrieved, "source_license": "CC0-1.0"})
    return facts, labels


def fetch_entities(remote):
    def fetch(ids):
        url = "https://www.wikidata.org/w/api.php?" + urlencode({"action": "wbgetentities", "ids": "|".join(sorted(ids)), "props": "info|labels|aliases|claims", "languages": "en", "format": "json", "maxlag": 5})
        raw = remote.get(url, 5_000_000)
        data = json.loads(raw)
        if "error" in data or not isinstance(data.get("entities"), dict):
            raise ValueError("wikidata_api_error_or_schema")
        return data["entities"]
    entities = fetch(set(CANDIDATES) | {COURT, FACILITY, TOURNAMENT})
    needed = {qid for key in CANDIDATES for prop in ("P710", "P31", "P361") for qid in item_ids(entities.get(key, {}), prop)} - set(entities)
    if len(needed) > 15:
        raise ValueError("unexpected_linked_entity_count")
    if needed:
        entities.update(fetch(needed))
    return entities


def write_csv(path, rows, columns):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as binary, gzip.GzipFile(fileobj=binary, mode="wb", mtime=0) as zipped, io.TextIOWrapper(zipped, encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: r"\N" if row.get(key) is None else row[key] for key in columns})


def publish(output, tables, summary, sources, source_notices=None):
    require_github_hosted_runner()
    output.mkdir(parents=True, exist_ok=True)
    schema = {"null_token": r"\N", "all_identifiers": "strings", "boolean_encoding": "True/False", "match_date_precision": "calendar day, never UTC kickoff",
        "source_csv_policy": "Literal backslash-N is null; empty text remains empty; structurally missing cells are rejected.",
        "numeric_weather_policy": "Null and empty numeric observations become unknown/null, never zero; invalid or nonfinite numbers are excluded.",
        "json_columns": "Fields ending _json contain JSON objects/arrays/scalars; weather variables are separate numeric CSV columns.",
        "numeric_columns": ["reference_count", "revision", "court_coordinate_distance_m", "calendar_overlap_seconds", "requested_latitude", "requested_longitude", "grid_latitude", "grid_longitude", "candidate_count"] + DAILY_FIELDS,
        "weather_intervals": "UTC daily buckets; calendar boundaries are not match start/end", "tables": {name: {"columns": cols, "rows": len(rows)} for name, (rows, cols) in tables.items()}}
    generated = []
    for name, (rows, columns) in tables.items():
        path = output / name
        write_csv(path, rows, columns)
        generated.append(path)
    notices = {
        "wikidata_cc0/LICENSE.txt": "Wikidata structured facts: CC0-1.0. https://www.wikidata.org/wiki/Wikidata:Licensing . Current snapshot; statement references and historical validity may be absent. No article text exported.\n",
        "research_only/LICENSE.txt": "Research-only MCP-derived joins: CC-BY-NC-SA-4.0. Source: https://github.com/JeffSackmann/tennis_MatchChartingProject at 1813a1309b7ed7ebf1c7e884b32bf675d00e4edf. Changes: strict match identity alignment and retrospective calendar-day weather context. Weather CC-BY-4.0: Open-Meteo and Copernicus Climate Change Service/ECMWF ERA5; original collection used Open-Meteo free API noncommercial terms. CC0 facts remain separately available. No commercial-use grant, forecast, point timing, training or wagering claim.\n"}
    documents = {"summary.json": summary, "schema.json": schema, "source_manifest.json": {"pins": PINS, "sources": sources}}
    for name, payload in documents.items():
        path = output / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        generated.append(path)
    for name, text in notices.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        generated.append(path)
    for name, content in (source_notices or {}).items():
        if name not in {"research_only/SOURCE_MCP_LICENSE.txt", "research_only/SOURCE_WEATHER_LICENSE.txt"}:
            raise ValueError("unexpected_source_license_filename")
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        generated.append(path)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive_path = dist / "csv-tennis-weather-alignment-research-only.tar.gz"
    inventory = []
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(generated):
            member = "data/tennis_weather/" + path.relative_to(output).as_posix()
            archive.add(path, arcname=member, recursive=False)
            inventory.append({"path": member, "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())})
    manifest = {"archive": archive_path.name, "bytes": archive_path.stat().st_size, "sha256": sha(archive_path.read_bytes()), "files": inventory,
        "licenses": {"wikidata_cc0": "CC0-1.0", "research_only": "CC-BY-NC-SA-4.0 plus attributed CC-BY-4.0 weather"}, "research_only_noncommercial": True}
    (dist / "tennis_weather_asset_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copyfile(output / "summary.json", dist / "tennis_weather_summary.json")
    shutil.copyfile(output / "schema.json", dist / "tennis_weather_schema.json")


def main():
    require_github_hosted_runner()
    remote = Remote()
    facts, labels, joins, weather, quarantine = [], [], [], [], []
    input_status, errors = {}, []
    source_notices = {}
    entities, matches, selected_places, places, weather_rows, units = {}, [], [], [], {}, {}
    stage = "wikidata"
    try:
        entities = fetch_entities(remote)
        facts, labels = fact_rows(entities, now())
        input_status["wikidata"] = "validated_response"
        stage = "mcp_archive"
        mp = PINS["mcp"]["prefix"]
        mcp = fetch_bundle(remote, "mcp", [mp + "mens_singles/matches.csv.gz", mp + "schema.json", mp + "LICENSE.txt"])
        source_notices["research_only/SOURCE_MCP_LICENSE.txt"] = mcp[mp + "LICENSE.txt"]
        matches = read_csv(mcp[mp + "mens_singles/matches.csv.gz"])
        input_status["mcp"] = "archive_pin_and_member_hashes_verified"
        stage = "weather_archive"
        wp = PINS["weather"]["prefix"]
        wanted = [wp + name for name in ("schema.json", "source_manifest.json", "wikidata_cc0/places.csv.gz", "wikidata_cc0/weather_collection_places.csv.gz", "weather_noncommercial_research/LICENSE.txt")]
        wanted.extend(wp + "weather_noncommercial_research/" + str(year) + "/" + WEATHER_KEY + ".csv.gz" for year in (2023, 2024))
        bundle = fetch_bundle(remote, "weather", wanted)
        source_notices["research_only/SOURCE_WEATHER_LICENSE.txt"] = bundle[wp + "weather_noncommercial_research/LICENSE.txt"]
        places = read_csv(bundle[wp + "wikidata_cc0/places.csv.gz"])
        selected_places = read_csv(bundle[wp + "wikidata_cc0/weather_collection_places.csv.gz"])
        units = json.loads(bundle[wp + "schema.json"]).get("weather_units", {}).get(WEATHER_KEY, {})
        for year in (2023, 2024):
            weather_rows[year] = read_csv(bundle[wp + "weather_noncommercial_research/" + str(year) + "/" + WEATHER_KEY + ".csv.gz"])
        input_status["weather"] = "archive_pin_and_member_hashes_verified"
        stage = "facility_alignment"
        place, separation = validate_place(entities, selected_places, places)
        input_status["same_facility_coordinate"] = "verified_current_identity_and_coordinate_sanity_only"
        used_match_ids = set()
        stage = "candidate_alignment"
        for entity_id in sorted(CANDIDATES):
            try:
                item = candidate(entity_id, entities)
                match = match_candidate(item, matches)
                if match["match_id"] in used_match_ids:
                    raise Excluded("match_reused_by_multiple_entities")
                context = weather_context(item, match, weather_rows[item["date"].year], place, units)
                day = item["date"]
                joins.append({"match_entity_id": entity_id, "match_id": match["match_id"], "competition_group": "mens_singles",
                    "player1_name": match["player1_name"], "player2_name": match["player2_name"], "match_local_date": day.isoformat(),
                    "source_date_precision": "calendar_day_wikidata_precision_11", "tournament": match["tournament"], "round": match["round"],
                    "court_entity_id": COURT, "facility_entity_id": FACILITY, "weather_place_coordinate_id": WEATHER_KEY,
                    "court_coordinate_distance_m": round(separation, 3), "evaluation_split": "fit_pre_2024" if day.year < 2024 else "calibration_2024" if day.year == 2024 else "holdout_2025_plus",
                    "timezone_assumption": "Europe/London from fixed Wimbledon venue mapping", "date_semantics_assumption": "source day treated as local playing calendar day; actual interval unverified",
                    "source_claims_referenced": item["referenced"], "venue_geometry_historical_validity_known": False,
                    "actual_match_start_utc": None, "actual_match_end_utc": None, "actual_match_interval_known": False,
                    "session": None, "roof_operation": None, "roof_operation_known": False, "pregame_available": False,
                    "automatic_training_join_allowed": False, "research_only_noncommercial": True, "license": "CC-BY-NC-SA-4.0"})
                weather.extend(context)
                used_match_ids.add(match["match_id"])
            except Excluded as exc:
                quarantine.append({"match_entity_id": entity_id, "stage": "candidate_alignment", "reason": exc.reason, "candidate_count": exc.count})
    except Exception as exc:
        # Never serialize response bodies, redirects' signed URLs or row contents.
        reason = exc.reason if isinstance(exc, Excluded) else str(exc) if isinstance(exc, (ValueError, RuntimeError)) and re.fullmatch(r"[a-z][a-z0-9_]{0,120}", str(exc)) else type(exc).__name__
        response = getattr(exc, "response", None)
        errors.append({"stage": stage, "reason": reason, "http_status": getattr(response, "status_code", None)})
        for entity_id in sorted(CANDIDATES):
            if not any(row["match_entity_id"] == entity_id for row in joins + quarantine):
                quarantine.append({"match_entity_id": entity_id, "stage": stage, "reason": reason, "candidate_count": 0})
    joins.sort(key=lambda row: (row["match_local_date"], row["match_id"]))
    weather.sort(key=lambda row: (row["match_id"], row["weather_date_utc"]))
    tables = {"wikidata_cc0/claims.csv.gz": (facts, FACT_COLUMNS), "wikidata_cc0/labels.csv.gz": (labels, LABEL_COLUMNS),
        "research_only/match_alignment.csv.gz": (joins, JOIN_COLUMNS), "research_only/weather_day_overlap.csv.gz": (weather, WEATHER_COLUMNS),
        "research_only/quarantine.csv.gz": (quarantine, QUARANTINE_COLUMNS)}
    summary = {"created_at_utc": now(), "dataset": "tennis_wimbledon_calendar_day_weather_alignment", "status": "partial" if joins and quarantine else "complete" if joins else "audit_only",
        "fixed_candidate_count": len(CANDIDATES), "cc0_claim_rows": len(facts), "accepted_match_count": len(joins), "weather_bucket_rows": len(weather),
        "quarantine_count": len(quarantine), "exclusion_counts": {reason: sum(row["reason"] == reason for row in quarantine) for reason in sorted({row["reason"] for row in quarantine})},
        "input_status": input_status, "errors": errors, "source_bytes": remote.bytes, "http_requests": remote.requests,
        "original_source_license_notices_preserved": sorted(source_notices),
        "budgets": {"bytes": MAX_BYTES, "http_requests_including_redirects": MAX_REQUESTS, "network_wall_seconds": MAX_SECONDS},
        "research_only_noncommercial": True, "automatic_training_join_allowed": False, "actual_match_intervals_known": 0, "roof_operations_known": 0,
        "limitations": ["Current Wikidata claims may lack references and historical availability.", "Date-only, explicit Europe/London calendar-day assumption; no actual match or point times.", "Two UTC buckets remain independent; no reconstructed local-day mean or match exposure.", "Same-facility ERA5 proxy, not court sensor; roof operation remains unknown.", "No forecast, training, betting or causal inference; CC0 facts and MCP-derived NC-SA joins stay separate."]}
    publish(ROOT / "data/tennis_weather", tables, summary, remote.sources, source_notices)
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
