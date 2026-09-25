"""Five-event CC0 attendance pilot. Real inputs and outputs are hosted-only."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import tarfile
import time
from urllib.parse import urlencode, urljoin, urlsplit

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

# Identity metadata reviewed against the linked primary entity pages, 2026-09-25.
# No attendance values, dates, winners or scores are embedded here.
EVENTS = {"Q20204363": (2020, "Super Bowl LIV"), "Q24233824": (2021, "Super Bowl LV"),
          "Q30114464": (2022, "Super Bowl LVI"), "Q54196526": (2023, "Super Bowl LVII"),
          "Q54196525": (2024, "Super Bowl LVIII")}
TEAMS = {"Q223522": "KC", "Q337758": "SF", "Q320476": "TB", "Q337377": "LAR", "Q223511": "CIN", "Q219714": "PHI"}
VENUES = {"hard rock stadium": "Q864339", "raymond james stadium": "Q1141343", "sofi stadium": "Q19520501",
          "state farm stadium": "Q756433", "university of phoenix stadium": "Q756433", "allegiant stadium": "Q27768421"}
ROLE_QIDS = {"Q24633211": "home", "Q24633216": "away"}
PROPERTIES = ("P31", "P1110", "P585", "P580", "P276", "P1923", "P155", "P156")
SAFE_QUALIFIERS = {"P3831", "P585", "P580", "P582", "P4241", "P421"}
SOURCE = {"tag": "matchup-36179718171-1", "asset": "csv-matchup-nfl-teams.tar.gz", "bytes": 8653720,
          "sha256": "4ecdcf9533346d99c5c121ebd87085b41d041667adbac7074e08a714ea1eaeb8",
          "member": "data/matchup/nfl_teams/schedule_context_audit.csv.gz",
          "member_sha256": "c91a2f24949085036e5fc2664ef0aa3172f70c6fff34d540152ea46b164c3cb8"}
SOURCE_URL = "https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/" + SOURCE["tag"] + "/" + SOURCE["asset"]
MAX_REQUESTS, MAX_BYTES, MAX_API_BYTES, WALL_SECONDS = 20, 20_000_000, 10_000_000, 300
SCHEDULE_COLUMNS = ["game_id", "team", "opponent", "game_date", "season", "is_home", "source_game_type",
                    "source_stadium", "source_stadium_id", "source_game_date_time_known"]
CLAIM_COLUMNS = ["event_qid", "property_id", "statement_id", "statement_index", "rank", "snaktype", "datavalue_json",
                 "retained_qualifiers_json", "qualifier_property_ids_json", "reference_urls_json", "reference_count",
                 "attendance_candidate", "attendance_validation", "entity_revision", "entity_modified_utc_source", "source_url", "verified_asof"]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def retry_after_fields(header):
    """Retain only a parsed interval or date, never arbitrary header text."""
    result = {"retry_after_seconds": None, "retry_after_utc": None}
    if not isinstance(header, str) or len(header) > 128:
        return result
    value = header.strip()
    if re.fullmatch(r"[0-9]{1,10}", value):
        result["retry_after_seconds"] = int(value)
    elif not any(char in value for char in ("\r", "\n")):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is not None:
                result["retry_after_utc"] = parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    return result


def safe_api_error_code(error):
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", code) else "missing_or_invalid_code"


class Budget:
    """Every HTTP attempt, including redirects, shares a finite budget."""
    def __init__(self):
        self.requests = self.bytes = self.api_bytes = 0
        self.deadline = time.monotonic() + WALL_SECONDS
        self.records = []
        self.last_response = {}

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Collection wall-clock budget exhausted")
        if self.requests >= MAX_REQUESTS:
            raise RuntimeError("HTTP request budget exhausted")

    @staticmethod
    def allowed(url, kind):
        parsed = urlsplit(url)
        hosts = {"www.wikidata.org", "wikidata.org"} if kind == "wikidata" else {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
        if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Untrusted source or redirect host")

    def fetch(self, url, kind, bound):
        require_github_hosted_runner()
        original = url
        self.last_response = {}
        with requests.Session() as session:
            session.headers.update({"User-Agent": "sports-props-research/1.0 (+https://github.com/kennynakao/Tabular-Model-for-sports)", "Accept-Encoding": "gzip,deflate"})
            for redirects in range(3):
                self.allowed(url, kind)
                self.check()
                self.requests += 1
                timeout = max(0.1, min(30.0, self.deadline - time.monotonic()))
                with session.get(url, stream=True, allow_redirects=False, timeout=(min(10.0, timeout), timeout)) as response:
                    metadata = {"http_status": int(response.status_code), **retry_after_fields(response.headers.get("Retry-After"))}
                    self.last_response = metadata
                    record = {"source_url": original, "kind": kind, "attempt": self.requests, **metadata,
                        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "body_read": False}
                    self.records.append(record)
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location or redirects == 2:
                            raise ValueError("Missing or excessive redirect")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()  # Includes 429: stop; never retry.
                    body = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError("Collection wall-clock budget exhausted")
                        self.bytes += len(chunk)
                        if kind == "wikidata":
                            self.api_bytes += len(chunk)
                        if len(body) + len(chunk) > bound or self.bytes > MAX_BYTES or self.api_bytes > MAX_API_BYTES:
                            raise RuntimeError("Decoded source byte budget exceeded")
                        body.extend(chunk)
                    result = bytes(body)
                    record.update({"bytes": len(result), "sha256": digest(result), "body_read": True,
                        "hash_basis": "HTTP body after transfer/content decoding"})
                    return result
        raise RuntimeError("Source response unavailable")


def snak_value(snak):
    return snak.get("datavalue", {}).get("value") if snak.get("snaktype") == "value" else None


def active(entity, prop):
    return [s for s in entity.get("claims", {}).get(prop, []) if s.get("rank") in ("normal", "preferred")]


def entity_ids(statements):
    values, invalid = set(), False
    for statement in statements:
        value = snak_value(statement.get("mainsnak", {}))
        if not isinstance(value, dict) or not re.fullmatch(r"Q\d+", str(value.get("id", ""))):
            invalid = True
        else:
            values.add(value["id"])
    return values, invalid


def calendar_day(value):
    """Wikidata day precision is a calendar date, never a midnight instant."""
    if not isinstance(value, dict) or value.get("precision") != 11 or str(value.get("calendarmodel", "")).rsplit("/", 1)[-1] != "Q1985727":
        return None
    match = re.fullmatch(r"\+(\d{4})-(\d{2})-(\d{2})T00:00:00Z", str(value.get("time", "")))
    if not match or value.get("before", 0) != 0 or value.get("after", 0) != 0:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def attendance_value(statement):
    if statement.get("rank") not in ("normal", "preferred"):
        return None, "deprecated_or_unknown_rank"
    snak = statement.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None, snak.get("snaktype", "invalid_snak")
    value = snak_value(snak)
    if not isinstance(value, dict) or value.get("unit") != "1":
        return None, "unknown_quantity_unit"
    if statement.get("qualifiers"):
        return None, "qualified_count_scope_unresolved"
    try:
        amount = Decimal(str(value.get("amount")))
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value() or amount > 2**63 - 1:
            return None, "invalid_nonnegative_integer"
        bounds = [value.get("lowerBound"), value.get("upperBound")]
        if sum(bound is not None for bound in bounds) == 1:
            return None, "incomplete_quantity_bounds"
        for bound in bounds:
            if bound is not None and (not Decimal(str(bound)).is_finite() or Decimal(str(bound)) != amount):
                return None, "uncertain_quantity_bounds"
        return int(amount), "valid_reported_count"
    except (InvalidOperation, ValueError, TypeError):
        return None, "invalid_quantity"


def claim_rows(qid, entity):
    rows = []
    for prop in PROPERTIES:
        for index, statement in enumerate(entity.get("claims", {}).get(prop, [])):
            snak = statement.get("mainsnak", {})
            qualifiers = statement.get("qualifiers", {})
            # Never export score qualifiers, quotations, reference prose or images.
            safe = {key: [snak_value(v) for v in values] for key, values in qualifiers.items() if key in SAFE_QUALIFIERS}
            urls = sorted({v for ref in statement.get("references", []) for s in ref.get("snaks", {}).get("P854", [])
                           for v in [snak_value(s)] if isinstance(v, str) and urlsplit(v).scheme in ("http", "https")})
            amount, reason = attendance_value(statement) if prop == "P1110" else (None, "not_attendance")
            rows.append({"event_qid": qid, "property_id": prop, "statement_id": statement.get("id"), "statement_index": index,
                "rank": statement.get("rank"), "snaktype": snak.get("snaktype"), "datavalue_json": packed(snak_value(snak)),
                "retained_qualifiers_json": packed(safe), "qualifier_property_ids_json": packed(sorted(qualifiers)),
                "reference_urls_json": packed(urls), "reference_count": len(statement.get("references", [])),
                "attendance_candidate": amount, "attendance_validation": reason, "entity_revision": str(entity.get("lastrevid", "")) or None,
                "entity_modified_utc_source": entity.get("modified"), "source_url": "https://www.wikidata.org/wiki/" + qid,
                "verified_asof": False})
    return rows


def valid_entity_schema(qid, entity):
    """An absent payload cannot be evidence that a property is absent."""
    if not isinstance(entity, dict) or entity.get("id") != qid or "missing" in entity or not isinstance(entity.get("claims"), dict):
        return False
    if not isinstance(entity.get("labels", {}), dict):
        return False
    if any(not isinstance(label, dict) or not isinstance(label.get("value"), str) for label in entity.get("labels", {}).values()):
        return False
    def valid_snak(snak):
        return (isinstance(snak, dict) and snak.get("snaktype") in ("value", "somevalue", "novalue")
            and (snak.get("snaktype") != "value" or isinstance(snak.get("datavalue"), dict)))
    for prop in PROPERTIES:
        statements = entity["claims"].get(prop, [])
        if not isinstance(statements, list):
            return False
        for statement in statements:
            if not isinstance(statement, dict) or not valid_snak(statement.get("mainsnak")):
                return False
            qualifiers, refs = statement.get("qualifiers", {}), statement.get("references", [])
            if not isinstance(qualifiers, dict) or not isinstance(refs, list):
                return False
            for key in SAFE_QUALIFIERS:
                values = qualifiers.get(key, [])
                if not isinstance(values, list) or not all(valid_snak(v) for v in values):
                    return False
            for reference in refs:
                if not isinstance(reference, dict) or not isinstance(reference.get("snaks", {}), dict):
                    return False
                values = reference.get("snaks", {}).get("P854", [])
                if not isinstance(values, list) or not all(valid_snak(v) for v in values):
                    return False
    return True


def parse_event(qid, entity, fetch_state=None):
    expected_year, fallback_label = EVENTS[qid]
    received = valid_entity_schema(qid, entity)
    if fetch_state is None:
        fetch_state = {"entity_fetch_status": "received" if received else ("entity_fetch_failed" if entity else "entity_not_fetched")}
    fetch_status = fetch_state["entity_fetch_status"]
    if fetch_status == "received" and not received:
        fetch_status = "entity_fetch_failed"
    if fetch_status != "received":
        entity = {}  # Never interpret unavailable data as missing claims.
    label, language, label_basis = fallback_label, "en", "reviewed_allowlist_metadata"
    for lang in ("en", "mul"):
        source_label = entity.get("labels", {}).get(lang, {})
        if source_label.get("value"):
            label, language, label_basis = source_label["value"], source_label.get("language", lang), "source_entity_label"
            break
    result = {"event_qid": qid, "expected_calendar_year": expected_year, "event_label": label, "label_language": language,
        "label_basis": label_basis, "event_calendar_date": None, "participant_qids_json": "[]", "participant_codes_json": "[]",
        "nominal_roles_json": "{}", "venue_qid": None, "attendance_reported": None, "attendance_status": "missing_property",
        "attendance_claims": len(entity.get("claims", {}).get("P1110", [])) if received and fetch_status == "received" else None,
        "attendance_count_method_verified": False, "attendance_reference_present": None,
        "entity_fetch_status": fetch_status, "entity_fetch_failure_kind": fetch_state.get("failure_kind"),
        "entity_not_fetched_reason": fetch_state.get("not_fetched_reason"), "fetch_http_status": fetch_state.get("http_status"),
        "fetch_api_error_code": fetch_state.get("api_error_code"), "fetch_retry_after_seconds": fetch_state.get("retry_after_seconds"),
        "fetch_retry_after_utc": fetch_state.get("retry_after_utc"), "entity_revision": str(entity.get("lastrevid", "")) or None,
        "entity_modified_utc_source": entity.get("modified"), "identity_exclusions": "", "chain_verified": False,
        "source_url": "https://www.wikidata.org/wiki/" + qid, "verified_asof": False, "automatic_training_join_allowed": False}
    if fetch_status != "received":
        result["attendance_status"] = fetch_status
        result["identity_exclusions"] = fetch_status
        return result
    result["attendance_reference_present"] = False
    problems = []
    if entity.get("id") != qid or "missing" in entity:
        problems.append("entity_missing_or_id_mismatch")
    classes, _ = entity_ids(active(entity, "P31"))
    if "Q32096" not in classes:
        problems.append("super_bowl_class_missing")
    dates = [calendar_day(snak_value(s.get("mainsnak", {}))) for s in active(entity, "P585")]
    if not dates or None in dates or len(set(dates)) != 1 or int(dates[0][:4]) != expected_year:
        problems.append("calendar_date_missing_conflicting_or_outside_allowlist_year")
    else:
        result["event_calendar_date"] = dates[0]
    participants, invalid = entity_ids(active(entity, "P1923"))
    result["participant_qids_json"] = packed(sorted(participants))
    codes = [TEAMS[p] for p in sorted(participants) if p in TEAMS]
    result["participant_codes_json"] = packed(sorted(codes))
    if invalid or len(participants) != 2 or len(set(codes)) != 2:
        problems.append("participants_missing_unknown_or_ambiguous")
    roles = {}
    for statement in active(entity, "P1923"):
        value = snak_value(statement.get("mainsnak", {})) or {}
        team = TEAMS.get(value.get("id")) if isinstance(value, dict) else None
        for role in statement.get("qualifiers", {}).get("P3831", []):
            rv = snak_value(role)
            name = ROLE_QIDS.get(rv.get("id")) if isinstance(rv, dict) else None
            if not team or not name or (team in roles and roles[team] != name):
                problems.append("participant_role_unresolved_or_conflicting")
            else:
                roles[team] = name
    if len(roles) == 2 and len(set(roles.values())) != 2:
        problems.append("participant_roles_not_complementary")
    result["nominal_roles_json"] = packed(roles)
    venues, invalid = entity_ids(active(entity, "P276"))
    if invalid or len(venues) > 1:
        problems.append("event_venue_conflicting")
    elif venues:
        result["venue_qid"] = next(iter(venues))
    counts = [attendance_value(s) for s in active(entity, "P1110")]
    if counts:
        known = {amount for amount, status in counts if status == "valid_reported_count"}
        if any(status != "valid_reported_count" for _, status in counts):
            result["attendance_status"] = "invalid_or_unknown_claims"
        elif len(known) != 1:
            result["attendance_status"] = "conflicting_claims"
        else:
            result["attendance_reported"] = next(iter(known))
            result["attendance_status"] = "reported_count_available"
            result["attendance_reference_present"] = any(bool(s.get("references")) for s in active(entity, "P1110"))
    elif result["attendance_claims"]:
        result["attendance_status"] = "only_deprecated_or_unknown_rank"
    result["identity_exclusions"] = ";".join(sorted(set(problems)))
    return result


def validate_chain(events, entities):
    order = list(EVENTS)
    for record in events:
        qid = record["event_qid"]
        i = order.index(qid)
        valid = qid in entities
        for prop, target_index in (("P155", i - 1), ("P156", i + 1)):
            if not 0 <= target_index < len(order):
                continue  # Never fetch or require years outside the allowlist.
            values, invalid = entity_ids(active(entities.get(qid, {}), prop))
            opposite = "P156" if prop == "P155" else "P155"
            back, back_invalid = entity_ids(active(entities.get(order[target_index], {}), opposite))
            valid &= not invalid and not back_invalid and values == {order[target_index]} and back == {qid}
        record["chain_verified"] = bool(valid)
        if not valid:
            record["identity_exclusions"] = ";".join(filter(None, [record["identity_exclusions"], "chain_missing_or_conflicting"]))
    return events


def normalize_schedule(raw):
    if not set(SCHEDULE_COLUMNS).issubset(raw):
        raise ValueError("Pinned schedule contract missing required columns")
    # Only SB identities are relevant; never export scores or player outcomes.
    frame = raw.loc[raw.source_game_type.eq("SB"), SCHEDULE_COLUMNS].copy()
    for col in SCHEDULE_COLUMNS:
        frame[col] = frame[col].astype("string")
        if col not in ("source_stadium", "source_stadium_id"):
            frame[col] = frame[col].str.strip().replace("", pd.NA)
    frame["game_date"] = pd.to_datetime(frame.game_date, utc=True, format="mixed", errors="coerce")
    frame["known_start"] = frame.source_game_date_time_known.str.lower().isin(["true", "1"])
    frame["home_flag"] = frame.is_home.str.lower().map({"true": True, "false": False, "1": True, "0": False})
    rows, excluded = [], []
    for game, group in frame.groupby("game_id", dropna=False, sort=True):
        required = ["game_id", "team", "opponent", "game_date", "season", "home_flag"]
        okay = (len(group) == 2 and group[required].notna().all().all() and group.known_start.all()
                and group.team.nunique() == 2 and set(group.team) == set(group.opponent)
                and group.team.ne(group.opponent).all() and set(group.home_flag) == {True, False}
                and all(group[c].nunique(dropna=False) == 1 for c in ["game_date", "season", "source_stadium", "source_stadium_id"]))
        if not okay:
            excluded.append({"game_id": game, "schedule_rows": len(group), "reason": "invalid_reciprocal_identity_start_or_venue"})
            continue
        home = group.loc[group.home_flag.eq(True)].iloc[0]
        day = home.game_date.tz_convert("America/New_York").date()
        if not 2020 <= day.year <= 2024:
            continue
        if not str(home.season).isdigit() or int(home.season) != day.year - 1:
            excluded.append({"game_id": game, "schedule_rows": len(group), "reason": "super_bowl_season_year_mismatch"})
            continue
        rows.append({"game_id": game, "game_date": home.game_date, "schedule_eastern_calendar_date": day.isoformat(),
            "season": str(home.season), "nominal_home_team": home.team, "nominal_away_team": home.opponent,
            "source_stadium": home.source_stadium, "source_stadium_id": home.source_stadium_id,
            "schedule_pair_key": packed(sorted([home.team, home.opponent])),
            "evaluation_split": "fit_pre_2024" if home.game_date < pd.Timestamp("2024-01-01", tz="UTC") else "calibration_2024",
            "schedule_publication_time_verified": False})
    columns = ["game_id", "game_date", "schedule_eastern_calendar_date", "season", "nominal_home_team", "nominal_away_team",
               "source_stadium", "source_stadium_id", "schedule_pair_key", "evaluation_split", "schedule_publication_time_verified"]
    return pd.DataFrame(rows, columns=columns), pd.DataFrame(excluded, columns=["game_id", "schedule_rows", "reason"])


def join_events(events, schedule):
    rows = []
    for event in events:
        row = dict(event)
        row.update(game_id=None, game_date=None, season=None, evaluation_split=None, nominal_home_team=None, nominal_away_team=None,
                   source_stadium=None, source_stadium_id=None, schedule_eastern_calendar_date=None,
                   exact_game_join_valid=False, home_away_roles_verified=False,
                   venue_identity_verified=False, venue_verification_basis="unresolved", attendance_join_eligible=False,
                   join_exclusion=event["identity_exclusions"], schedule_publication_time_verified=False)
        if not row["join_exclusion"]:
            candidates = schedule.loc[schedule.schedule_eastern_calendar_date.eq(event["event_calendar_date"])
                & schedule.schedule_pair_key.eq(event["participant_codes_json"])]
            if len(candidates) != 1:
                row["join_exclusion"] = "schedule_match_missing_or_ambiguous"
            else:
                candidate = candidates.iloc[0].to_dict()
                row.update({k: v for k, v in candidate.items() if k != "schedule_pair_key"})
                roles = json.loads(event["nominal_roles_json"])
                expected = {candidate["nominal_home_team"]: "home", candidate["nominal_away_team"]: "away"}
                role_ok = all(expected.get(team) == role for team, role in roles.items())
                row["home_away_roles_verified"] = role_ok and roles == expected
                stadium = candidate["source_stadium"]
                mapped = VENUES.get(str(stadium).casefold()) if pd.notna(stadium) else None
                venue_ok = not (mapped and event["venue_qid"] and mapped != event["venue_qid"])
                row["venue_identity_verified"] = bool(mapped and mapped == event["venue_qid"])
                if row["venue_identity_verified"]:
                    row["venue_verification_basis"] = "reviewed_exact_stadium_name_to_qid_crosswalk_not_provider_id_equivalence"
                if not role_ok:
                    row["join_exclusion"] = "nominal_role_conflict"
                elif not venue_ok:
                    row["join_exclusion"] = "explicit_venue_conflict"
                else:
                    row["exact_game_join_valid"] = True
                    row["attendance_join_eligible"] = event["attendance_status"] == "reported_count_available"
        rows.append(row)
    out = pd.DataFrame(rows)
    out["attendance_reported"] = pd.array(out.attendance_reported, dtype="Int64")
    out["game_date"] = pd.to_datetime(out.game_date, utc=True, errors="coerce")
    # Two event entities must never silently supply two counts for one game.
    repeated = out.game_id.notna() & out.game_id.duplicated(keep=False) & out.exact_game_join_valid
    out.loc[repeated, ["exact_game_join_valid", "attendance_join_eligible"]] = False
    out.loc[repeated, "join_exclusion"] = "multiple_event_entities_match_game"
    return out.sort_values(["game_date", "event_qid"], na_position="last").reset_index(drop=True)


def read_schedule(payload):
    require_github_hosted_runner()
    if len(payload) != SOURCE["bytes"] or digest(payload) != SOURCE["sha256"]:
        raise ValueError("Pinned archive integrity mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as gz:
        uncompressed = gz.read(120_000_001)
    if len(uncompressed) > 120_000_000:
        raise ValueError("Archive decompression limit exceeded")
    data, notices, seen = None, {}, set()
    with tarfile.open(fileobj=io.BytesIO(uncompressed), mode="r:") as archive:
        for member in archive:
            parsed = PurePosixPath(member.name)
            if parsed.is_absolute() or ".." in parsed.parts or "\\" in member.name or member.issym() or member.islnk() or member.name in seen:
                raise ValueError("Unsafe or duplicate archive member")
            seen.add(member.name)
            if member.name == SOURCE["member"]:
                if not member.isfile() or member.size > 12_000_000:
                    raise ValueError("Schedule member exceeds size/type limit")
                value = archive.extractfile(member).read()
                if digest(value) != SOURCE["member_sha256"]:
                    raise ValueError("Pinned schedule member integrity mismatch")
                with gzip.GzipFile(fileobj=io.BytesIO(value)) as gz:
                    data = gz.read(120_000_001)
                if len(data) > 120_000_000:
                    raise ValueError("Schedule CSV decompression limit exceeded")
            elif member.isfile() and "LICENSE" in parsed.name.upper() and member.size <= 100_000:
                notices["source_" + str(len(notices)) + "_" + parsed.name] = archive.extractfile(member).read()
    if data is None:
        raise ValueError("Pinned schedule member absent")
    frame = pd.read_csv(io.BytesIO(data), usecols=SCHEDULE_COLUMNS, dtype="string", keep_default_na=False, na_values=[r"\N"])
    return frame, notices


def write_table(frame, path):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"rows": len(frame), "bytes": path.stat().st_size, "sha256": digest(path.read_bytes()),
        "columns": {c: str(t) for c, t in frame.dtypes.items()}, "nulls": {c: int(n) for c, n in frame.isna().sum().items()}}


def safe_generated_path(path, directory):
    """Reject links before writing or publishing; do not traverse stale trees."""
    path.relative_to(directory)
    if directory.is_symlink():
        raise ValueError("Output directory is a symbolic link")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Generated output path includes a symbolic link")
        if part == directory:
            break
    return path


def publish_inventory(output, generated):
    """Only declared outputs, never directory enumeration or stale files."""
    paths = []
    for relative in sorted(set(generated)):
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in relative:
            raise ValueError("Unsafe generated output name")
        path = safe_generated_path(output / relative, output)
        if not path.is_file():
            raise ValueError("Declared generated file missing")
        paths.append(path)
    return paths


def fetch_entities(budget):
    """Fixed allowlist; the first failed response stops remaining requests."""
    entities, failures = {}, []
    states = {qid: {"entity_fetch_status": "entity_not_fetched", "not_fetched_reason": "not_attempted"} for qid in EVENTS}
    for qid in EVENTS:
        parameters = {"action": "wbgetentities", "ids": qid, "props": "info|labels|claims", "languages": "en|mul",
                      "format": "json", "maxlag": "5"}
        url = "https://www.wikidata.org/w/api.php?" + urlencode(parameters)
        failure_kind, api_code = None, None
        try:
            document = json.loads(budget.fetch(url, "wikidata", 2_000_000))
            if isinstance(document, dict) and "error" in document:
                failure_kind, api_code = "api_error", safe_api_error_code(document["error"])
            else:
                result = document.get("entities") if isinstance(document, dict) else None
                candidate = result.get(qid) if isinstance(result, dict) else None
                if not valid_entity_schema(qid, candidate):
                    failure_kind = "invalid_entity_schema_or_identity"
                else:
                    entities[qid] = candidate
        except requests.HTTPError:
            failure_kind = "http_error"
        except requests.RequestException:
            failure_kind = "transport_error"
        except TimeoutError:
            failure_kind = "collection_wall_timeout"
        except RuntimeError:
            failure_kind = "collection_budget_or_runtime_failure"
        except (ValueError, TypeError):
            failure_kind = "invalid_json_or_response_schema"
        metadata = {key: budget.last_response.get(key) for key in ("http_status", "retry_after_seconds", "retry_after_utc")}
        states[qid] = {"entity_fetch_status": "entity_fetch_failed" if failure_kind else "received", **metadata,
                       "failure_kind": failure_kind, "api_error_code": api_code}
        if failure_kind:
            failures.append({"source": qid, "error": failure_kind, "api_error_code": api_code, **metadata})
            for state in states.values():
                if state["entity_fetch_status"] == "entity_not_fetched":
                    state["not_fetched_reason"] = "collection_stopped_after_source_failure"
            break
    return entities, states, failures


def prepare_inputs(budget):
    """Resolve event prerequisites before spending bytes on NFL schedules."""
    entities, states, failures = fetch_entities(budget)
    events = validate_chain([parse_event(qid, entities.get(qid, {}), states[qid]) for qid in EVENTS], entities)
    eligible = [e for e in events if e["entity_fetch_status"] == "received" and not e["identity_exclusions"]
                and e["chain_verified"] and e["attendance_status"] == "reported_count_available"]
    schedule, excluded = normalize_schedule(pd.DataFrame(columns=SCHEDULE_COLUMNS))
    notices = {}
    schedule_status = "not_attempted_no_eligible_event"
    if eligible and failures:
        schedule_status = "not_attempted_after_source_failure"
    elif eligible:
        schedule_status = "fetch_failed"
        try:
            raw, notices = read_schedule(budget.fetch(SOURCE_URL, "schedule", SOURCE["bytes"]))
            schedule, excluded = normalize_schedule(raw)
            schedule_status = "fetched_and_validated"
        except (ValueError, RuntimeError, TimeoutError, requests.RequestException) as error:
            failures.append({"source": "schedule", "error": type(error).__name__,
                **{key: budget.last_response.get(key) for key in ("http_status", "retry_after_seconds", "retry_after_utc")}})
    return {"entities": entities, "events": events, "failures": failures, "schedule": schedule, "schedule_excluded": excluded,
            "notices": notices, "schedule_fetch_status": schedule_status, "events_passing_schedule_prerequisites": len(eligible)}


def coverage(events, joined, failures):
    available = sum(e["attendance_status"] == "reported_count_available" for e in events)
    received = sum(e["entity_fetch_status"] == "received" for e in events)
    failed = sum(e["entity_fetch_status"] == "entity_fetch_failed" for e in events)
    not_fetched = sum(e["entity_fetch_status"] == "entity_not_fetched" for e in events)
    eligible = int(joined.attendance_join_eligible.sum())
    status = "complete" if eligible == len(EVENTS) and not failures else ("partial" if eligible else "audit_only")
    return {"status": status, "candidate_events": len(EVENTS), "source_entities_received": received,
        "source_entities_fetch_failed": failed, "source_entities_not_fetched": not_fetched,
        "events_with_reported_count": available, "received_events_missing_or_unresolved_count": received - available,
        "received_events_without_attendance_property": sum(e["attendance_status"] == "missing_property" for e in events),
        "events_attendance_availability_unobserved": failed + not_fetched,
        "exact_schedule_joins": int(joined.exact_game_join_valid.sum()), "attendance_game_rows": eligible,
        "venue_identity_verified_joins": int((joined.venue_identity_verified & joined.exact_game_join_valid).sum()),
        "role_verified_joins": int((joined.home_away_roles_verified & joined.exact_game_join_valid).sum()),
        "attendance_status_counts": pd.Series([e["attendance_status"] for e in events]).value_counts().to_dict(),
        "failures": failures, "training_performed": False, "full_nfl_coverage": False}


def main():
    require_github_hosted_runner()
    output = ROOT / "data/nfl_attendance"
    safe_generated_path(output, ROOT)
    for folder in ("cc0", "schedule_join"):
        safe_generated_path(output / folder, ROOT)
    (output / "cc0").mkdir(parents=True, exist_ok=True)
    (output / "schedule_join").mkdir(exist_ok=True)
    budget = Budget()
    def timed_out(_signum, _frame):
        raise TimeoutError("Collection wall-clock limit reached")
    old_handler = signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, WALL_SECONDS)
    try:
        collected = prepare_inputs(budget)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    entities, events, failures = collected["entities"], collected["events"], collected["failures"]
    schedule, schedule_excluded, notices = collected["schedule"], collected["schedule_excluded"], collected["notices"]
    joined = join_events(events, schedule)
    claims = pd.DataFrame([row for qid in EVENTS for row in claim_rows(qid, entities.get(qid, {}))], columns=CLAIM_COLUMNS)
    claims["attendance_candidate"] = pd.array(claims.attendance_candidate, dtype="Int64")
    claims = claims.sort_values(["event_qid", "property_id", "statement_id", "statement_index"])
    event_table = pd.DataFrame(events)
    event_table["attendance_reported"] = pd.array(event_table.attendance_reported, dtype="Int64")
    for field in ("attendance_claims", "fetch_http_status", "fetch_retry_after_seconds"):
        event_table[field] = pd.array(event_table[field], dtype="Int64")
        joined[field] = pd.array(joined[field], dtype="Int64")
    tables = {"cc0/claims.csv.gz": claims, "cc0/event_audit.csv.gz": event_table,
              "schedule_join/game_links.csv.gz": joined, "schedule_join/schedule_exclusions.csv.gz": schedule_excluded}
    generated = list(tables)
    generated += ["cc0/SOURCE_RIGHTS.md", "schedule_join/SOURCE_RIGHTS.md", "identity_crosswalk.json", "summary.json", "schema.json"]
    generated += ["schedule_join/" + name for name in notices]
    for name in generated:
        safe_generated_path(output / name, ROOT)
    schema = {name: write_table(frame, output / name) for name, frame in tables.items()}
    for name, data in notices.items():
        (output / "schedule_join" / name).write_bytes(data)
    (output / "cc0/SOURCE_RIGHTS.md").write_text("Structured claims: Wikidata CC0. https://www.wikidata.org/wiki/Wikidata:Licensing\nReference URLs are provenance only; linked content was not downloaded. No reference quotations, scores, photographs or Wikipedia prose are exported. Entity retrieval/revision times do not establish historical publication.\n")
    (output / "schedule_join/SOURCE_RIGHTS.md").write_text("Game-link audits combine separately attributed Wikidata CC0 claims with an NFL schedule archive only when fetched and validated; consult summary.json schedule_fetch_status. Preserve any attached archive license notices. NFL schedules originate in Lee Sharpe/nfldata through nflverse; https://github.com/nflverse/nfldata . Do not label the entire joined table CC0. No scores or player outcomes are read or exported.\n")
    crosswalk = {"reviewed_at": "2026-09-25", "team_qid_to_code": TEAMS, "exact_venue_name_to_qid": VENUES,
        "identity_source_urls": ["https://www.wikidata.org/wiki/" + qid for qid in sorted(set(TEAMS) | set(VENUES.values()) | set(EVENTS))],
        "provider_stadium_id_equivalence_verified": False}
    (output / "identity_crosswalk.json").write_text(json.dumps(crosswalk, indent=2))
    summary = {**coverage(events, joined, failures), "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "schedule_fetch_status": collected["schedule_fetch_status"], "events_passing_schedule_prerequisites": collected["events_passing_schedule_prerequisites"],
        "source_archive": {**SOURCE, "url": SOURCE_URL}, "source_requests": budget.records, "http_attempts": budget.requests,
        "decoded_source_bytes": budget.bytes, "wikidata_decoded_bytes": budget.api_bytes, "tables": schema,
        "bounds": {"http_attempts_including_redirects": MAX_REQUESTS, "decoded_source_bytes": MAX_BYTES, "wikidata_bytes": MAX_API_BYTES, "collection_wall_seconds": WALL_SECONDS},
        "limitations": ["Five selected championship games cannot represent NFL-wide attendance or crowd effects.",
            "Attendance is a retrospective reported count; counting method, physical occupancy, noise and historical availability remain unverified.",
            "Missing and conflicting statements are retained; absent attendance is never replaced by zero.",
            "Actual-start Wikidata claims are not used as scheduled-kickoff timestamps. Calendar joins use day precision and the NFL source's Eastern date.",
            "Venue identity uses reviewed exact names; provider stadium IDs are retained without claiming Wikidata equivalence. Unknown venues stay unresolved.",
            "No capacity, occupancy ratio, outcome analysis or model fitting is produced."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    schema_doc = {"tables": schema, "csv_null_sentinel": r"\N", "identifiers": "string; never infer numeric ID types",
        "timestamps": "game_date is UTC; event_calendar_date is a date without timezone; source entity modified timestamps are not historical availability",
        "source_claim_values": "Typed structured values retained as JSON; attendance_candidate and attendance_reported are nullable integer counts",
        "licenses": {"cc0/": "Wikidata CC0 structured claims", "schedule_join/": "separate NFL upstream schedule notices plus Wikidata CC0; not wholly CC0"}}
    (output / "schema.json").write_text(json.dumps(schema_doc, indent=2))
    dist = ROOT / "dist"
    safe_generated_path(dist, ROOT)
    dist.mkdir(exist_ok=True)
    target = dist / "csv-nfl-cc0-attendance.tar.gz"
    for filename in (target.name, "nfl_attendance_asset_manifest.json", "nfl_attendance_summary.json"):
        safe_generated_path(dist / filename, ROOT)
    inventory = publish_inventory(output, generated)
    with tarfile.open(target, "w:gz", compresslevel=1) as archive:
        for path in inventory:
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    manifest = {"archive": target.name, "bytes": target.stat().st_size, "sha256": digest(target.read_bytes()),
        "files": [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p.read_bytes())}
                  for p in inventory]}
    (dist / "nfl_attendance_asset_manifest.json").write_text(json.dumps(manifest, indent=2))
    (dist / "nfl_attendance_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({key: value for key, value in summary.items() if key not in {"source_requests", "tables", "limitations"}}), flush=True)


if __name__ == "__main__":
    main()
