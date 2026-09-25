"""Frozen-input hosted diagnostic. Publishes aggregate counts, never accepted joins."""
from collections import Counter
from datetime import date, datetime, time as daytime, timezone
import csv
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
from urllib.parse import urljoin, urlsplit

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner
from tennis_weather import collect as baseline

BASE = "https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/"
PINS = {
    "snapshot": {"tag": "tennis-weather-36187582212-1", "archive": "csv-tennis-weather-alignment-research-only.tar.gz",
        "manifest": "tennis_weather_asset_manifest.json", "bytes": 8434,
        "sha256": "c9e27e95a667b9f2fe4b6bc12b0af6e091be7770dc30707cda96ded1d42fdfea", "prefix": "data/tennis_weather/"},
    "mcp": dict(baseline.PINS["mcp"])}
MAX_BYTES, MAX_REQUESTS, MAX_SECONDS = 60_000_000, 12, 300
IDENTITY_FIELDS = ["match_id", "player1_name", "player2_name", "match_date", "tournament", "round"]
MATCH_FIELDS = ["competition_group"] + IDENTITY_FIELDS + ["singles_metadata_eligible", "source_retirement_or_walkover_flag", "source_match_date_precision"]
RAW_ALIASES = {"player1_name": ["player1", "player1name"], "player2_name": ["player2", "player2name"],
    "match_date": ["date", "matchdate"], "tournament": ["tournament", "event"], "round": ["round"]}
GATE_COLUMNS = ["candidate_catalog_id", "metadata_stage", "name_policy", "metric", "count", "unit", "counterfactual_only"]
TERM_COLUMNS = ["candidate_catalog_id", "participant_slot", "metric", "count"]
GLOBAL_COLUMNS = ["metadata_stage", "metric", "count"]
PREREQ_COLUMNS = ["candidate_catalog_id", "status", "reason"]
POLICIES = ("current", "both_alias_languages")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Remote:
    def __init__(self):
        self.requests = self.bytes = 0
        self.deadline = time.monotonic() + MAX_SECONDS
        self.sources = []

    def get(self, url, bound):
        require_github_hosted_runner()
        original = url
        hosts = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
        for redirect in range(3):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError("disallowed_frozen_release_host")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or self.requests >= MAX_REQUESTS:
                raise RuntimeError("request_or_time_budget_exceeded")
            self.requests += 1
            with requests.get(url, stream=True, allow_redirects=False, timeout=(min(10, remaining), min(30, remaining)),
                              headers={"User-Agent": "SportsPropsResearch/1.0 (noncommercial frozen-input audit)"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirect == 2 or not response.headers.get("Location"):
                        raise RuntimeError("redirect_budget_exceeded")
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                data = bytearray()
                for block in response.iter_content(65536):
                    self.bytes += len(block)
                    data.extend(block)
                    if self.bytes > MAX_BYTES or len(data) > bound:
                        raise RuntimeError("source_byte_budget_exceeded")
                    if time.monotonic() >= self.deadline:
                        raise TimeoutError("collection_time_budget_exceeded")
                result = bytes(data)
                self.sources.append({"url": original, "bytes": len(result), "sha256": sha(result),
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "hash_basis": "decoded_http_body"})
                return result
        raise RuntimeError("redirect_budget_exceeded")


def bundle(remote, name, members):
    pin = PINS[name]
    prefix = BASE + pin["tag"] + "/"
    manifest = json.loads(remote.get(prefix + pin["manifest"], 300_000))
    if any(manifest.get(key) != pin[key] for key in ("archive", "bytes", "sha256")):
        raise ValueError("manifest_pin_mismatch")
    if name == "mcp" and "CC-BY-NC-SA-4.0" not in manifest.get("licenses", []):
        raise ValueError("mcp_manifest_license_missing")
    payload = remote.get(prefix + pin["archive"], pin["bytes"])
    if len(payload) != pin["bytes"] or sha(payload) != pin["sha256"]:
        raise ValueError("archive_pin_mismatch")
    return baseline.archive_members(payload, manifest, pin["prefix"], [pin["prefix"] + member for member in members])


def schema_contract(payload, source):
    schema = json.loads(payload)
    null_field = "null_token" if source == "snapshot" else "csv_null_encoding"
    if schema.get(null_field) != r"\N" or not isinstance(schema.get("tables"), dict):
        raise ValueError("frozen_schema_null_or_table_contract_mismatch")
    tables = schema["tables"]
    required = {"wikidata_cc0/claims.csv.gz": set(baseline.FACT_COLUMNS), "wikidata_cc0/labels.csv.gz": set(baseline.LABEL_COLUMNS)} if source == "snapshot" else {
        "mens_singles/matches.csv.gz": set(MATCH_FIELDS), "mens_singles/source_matches.csv.gz": {"match_id"}}
    counts = {}
    for name, fields in required.items():
        table = tables.get(name, {})
        columns = table.get("columns") if source == "snapshot" else table.get("schema")
        if not isinstance(columns, (list, dict)) or not fields.issubset(columns) or type(table.get("rows")) is not int or table["rows"] < 0:
            raise ValueError("frozen_schema_required_columns_or_rows_missing")
        counts[name] = table["rows"]
    return counts


def replay_entities(claim_rows, label_rows):
    """Restore only preserved structured values; never invent reference objects."""
    entities = {}
    required_labels = {"entity_id", "label_en", "label_mul", "aliases_en_json", "aliases_mul_json", "revision"}
    for row in label_rows:
        if not required_labels.issubset(row) or row["entity_id"] in entities or not re.fullmatch(r"Q[1-9][0-9]*", str(row["entity_id"])):
            raise ValueError("snapshot_label_schema_or_identity_error")
        entity = {"id": row["entity_id"], "lastrevid": row["revision"], "labels": {}, "aliases": {}, "claims": {}}
        for language in ("en", "mul"):
            if row["label_" + language] is not None:
                entity["labels"][language] = {"value": row["label_" + language], "language": language}
            aliases = json.loads(row["aliases_" + language + "_json"])
            if not isinstance(aliases, list) or any(not isinstance(term, str) for term in aliases):
                raise ValueError("snapshot_alias_schema_error")
            entity["aliases"][language] = [{"value": term, "language": language} for term in aliases]
        entities[row["entity_id"]] = entity
    seen = set()
    required_claims = {"entity_id", "property_id", "claim_id", "snaktype", "value_json", "qualifiers_json", "rank", "reference_count"}
    for row in claim_rows:
        if not required_claims.issubset(row):
            raise ValueError("snapshot_claim_schema_error")
        key = (row["entity_id"], row["property_id"], row["claim_id"])
        if row["entity_id"] not in entities or not row["claim_id"] or key in seen or row["property_id"] not in baseline.PROPERTIES:
            raise ValueError("snapshot_claim_identity_error")
        seen.add(key)
        if row["rank"] not in ("normal", "preferred", "deprecated") or row["snaktype"] not in ("value", "novalue", "somevalue"):
            raise ValueError("snapshot_claim_type_error")
        qualifiers = json.loads(row["qualifiers_json"])
        if not isinstance(qualifiers, dict):
            raise ValueError("snapshot_qualifier_schema_error")
        claim = {"id": row["claim_id"], "rank": row["rank"], "mainsnak": {"snaktype": row["snaktype"],
            "datavalue": {"value": json.loads(row["value_json"])}}, "qualifiers": qualifiers,
            "source_reference_count": int(row["reference_count"])}
        entities[row["entity_id"]]["claims"].setdefault(row["property_id"], []).append(claim)
    return entities


def eligible_terms(values):
    return {baseline.normalized(term) for term in values if isinstance(term, str) and len(baseline.normalized(term).split()) >= 2}


def policy_names(entity, policy):
    if policy == "current":
        return baseline.names(entity)
    if policy != "both_alias_languages":
        raise ValueError("unapproved_diagnostic_policy")
    values = [baseline.resolved_label(entity)[0]]
    values.extend(alias.get("value") for language in ("en", "mul") for alias in entity.get("aliases", {}).get(language, []))
    return eligible_terms(values)


def term_counts(entities):
    output = []
    for candidate_id in sorted(baseline.CANDIDATES):
        participants = sorted(baseline.item_ids(entities.get(candidate_id, {}), "P710"))
        for position, person_id in enumerate(participants, 1):
            entity = entities.get(person_id, {})
            def add(metric, value):
                output.append({"candidate_catalog_id": candidate_id, "participant_slot": position, "metric": metric, "count": int(value)})
            for language in ("en", "mul"):
                terms = [alias.get("value") for alias in entity.get("aliases", {}).get(language, [])]
                nonempty = [term for term in terms if isinstance(term, str) and term.strip()]
                add(language + "_alias_records", len(terms))
                add(language + "_nonempty_alias_records", len(nonempty))
                add(language + "_eligible_distinct_aliases", len(eligible_terms(terms)))
                add(language + "_aliases_rejected_by_token_requirement", sum(len(baseline.normalized(term).split()) < 2 for term in nonempty))
                add(language + "_normalization_duplicate_aliases", len(nonempty) - len({baseline.normalized(term) for term in nonempty}))
            current, both = policy_names(entity, "current"), policy_names(entity, "both_alias_languages")
            add("current_distinct_eligible_terms", len(current))
            add("both_languages_distinct_eligible_terms", len(both))
            add("omitted_distinct_eligible_aliases", len(both - current))
            en = entity.get("labels", {}).get("en", {}).get("value")
            mul = entity.get("labels", {}).get("mul", {}).get("value")
            add("unselected_default_label_present", bool(en and mul))
        if len(participants) == 2:
            for policy in POLICIES:
                first, second = [policy_names(entities.get(person, {}), policy) for person in participants]
                output.append({"candidate_catalog_id": candidate_id, "participant_slot": 0,
                    "metric": policy + "_participant_name_intersection", "count": len(first & second)})
    return output


def date_status(value):
    if value is None:
        return "null"
    if value == "":
        return "empty"
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text) if "T" in text or " " in text else datetime.combine(date.fromisoformat(text), daytime())
    except (ValueError, TypeError):
        return "invalid_syntax"
    if parsed.tzinfo is not None:
        return "offset_aware"
    return "calendar_date" if parsed.time() == daytime() else "nonmidnight_naive"


def raw_headers(columns):
    headers = {}
    for name in columns:
        headers.setdefault(re.sub(r"[^a-z0-9]", "", name.lower()), []).append(name)
    if "match_id" not in columns:
        raise ValueError("source_export_match_id_header_missing")
    resolved = {"match_id": "match_id"}
    for target, aliases in RAW_ALIASES.items():
        choices = [name for alias in aliases for name in headers.get(alias, [])]
        if len(choices) != 1:
            raise ValueError("source_export_identity_header_missing_or_ambiguous")
        resolved[target] = choices[0]
    return resolved


def read_identity_csv(payload, raw_source=False):
    """Validate CSV shape; retain only identity columns, never outcome columns."""
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
        decoded = compressed.read(20_000_001)
    if len(decoded) > 20_000_000:
        raise ValueError("identity_csv_decompression_budget_exceeded")
    reader = csv.reader(io.StringIO(decoded.decode("utf-8-sig")))
    columns = next(reader, [])
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("identity_csv_header_missing_or_duplicate")
    selected = set(raw_headers(columns).values()) if raw_source else set(MATCH_FIELDS)
    if not selected.issubset(columns):
        raise ValueError("identity_csv_required_columns_missing")
    positions = [(index, name) for index, name in enumerate(columns) if name in selected]
    output = []
    for values in reader:
        if len(values) != len(columns) or len(output) >= 50_000:
            raise ValueError("identity_csv_row_shape_or_count_exceeded")
        output.append({name: None if values[index] == r"\N" else values[index] for index, name in positions})
    return output


def raw_identity_rows(rows):
    """Read only source-export identity columns; never status/score/outcome fields."""
    if not rows:
        return [], {}
    resolved = raw_headers(rows[0])
    # Same date parser family as the producer; preserve unparsable source text
    # only in runner memory for aggregate categorization.
    dates = pd.to_datetime(pd.Series([row.get(resolved["match_date"]) for row in rows], dtype="string"), format="mixed", errors="coerce")
    output = []
    for index, row in enumerate(rows):
        value = {target: row.get(source) for target, source in resolved.items()}
        parsed = dates.iloc[index]
        value["match_date"] = None if pd.isna(parsed) else parsed.isoformat()
        value["competition_group"] = "mens_singles"
        output.append(value)
    counts = {"source_date_nonempty_parse_failures": sum(row.get(resolved["match_date"]) not in (None, "") and pd.isna(dates.iloc[index]) for index, row in enumerate(rows))}
    before = Counter()
    for row in rows:
        value = row.get(resolved["match_date"])
        category = "null" if value is None else "empty" if value == "" else "compact_yyyymmdd" if re.fullmatch(r"\d{8}", value) else date_status(value)
        before[category] += 1
    for category in ("null", "empty", "compact_yyyymmdd", "invalid_syntax", "offset_aware", "nonmidnight_naive", "calendar_date"):
        counts["before_parse_date_representation_" + category] = before[category]
    return output, counts


def identity_tuple(row):
    day = baseline.match_date(row.get("match_date"))
    date_key = ("calendar_date", day.isoformat()) if day else ("unparsed", row.get("match_date"))
    # Producer metadata preserves these text fields. Literal differences belong
    # in the integrity audit even when the exact matching policy normalizes them.
    text = tuple(row.get(field) for field in ("player1_name", "player2_name", "tournament", "round"))
    return text + (date_key,)


def compare_metadata(raw, normalized):
    def group(rows):
        groups = {}
        for row in rows:
            if row.get("match_id") not in (None, ""):
                groups.setdefault(row["match_id"], set()).add(identity_tuple(row))
        return groups
    left, right = group(raw), group(normalized)
    common = set(left) & set(right)
    values = {"source_export_ids_absent_from_normalized": len(set(left)-set(right)),
        "normalized_ids_absent_from_source_export": len(set(right)-set(left)),
        "shared_ids_exact_identity_tuple_sets_agree": sum(left[key] == right[key] for key in common),
        "shared_ids_identity_tuple_sets_conflict": sum(left[key] != right[key] for key in common),
        "shared_ids_no_matching_identity_tuple": sum(not (left[key] & right[key]) for key in common),
        "source_export_rows_id_present_but_identity_tuple_absent": sum(row.get("match_id") in right and identity_tuple(row) not in right[row["match_id"]] for row in raw)}
    return [{"metadata_stage": "raw_vs_normalized", "metric": key, "count": int(value)} for key, value in values.items()]


def metadata_counts(rows, stage):
    output = []
    def add(metric, count):
        output.append({"metadata_stage": stage, "metric": metric, "count": int(count)})
    add("rows", len(rows))
    ids = Counter(row.get("match_id") for row in rows if row.get("match_id") not in (None, ""))
    add("distinct_nonempty_match_ids", len(ids))
    add("duplicated_match_id_groups", sum(count > 1 for count in ids.values()))
    add("rows_in_duplicated_match_id_groups", sum(count for count in ids.values() if count > 1))
    identities = [tuple(row.get(column) for column in IDENTITY_FIELDS) for row in rows]
    add("duplicate_identity_field_rows", len(identities) - len(set(identities)))
    grouped = {}
    for row, key in zip(rows, identities):
        grouped.setdefault(row.get("match_id"), set()).add(key)
    add("ids_with_conflicting_identity_fields", sum(len(values) > 1 for key, values in grouped.items() if key not in (None, "")))
    for column in IDENTITY_FIELDS:
        add(column + "_null", sum(row.get(column) is None for row in rows))
        add(column + "_empty", sum(row.get(column) == "" for row in rows))
    for category in ("null", "empty", "invalid_syntax", "offset_aware", "nonmidnight_naive", "calendar_date"):
        add("date_representation_" + category, sum(date_status(row.get("match_date")) == category for row in rows))
    return output


def exact_pair(row, names):
    one, two = baseline.normalized(row.get("player1_name")), baseline.normalized(row.get("player2_name"))
    return (one in names[0] and two in names[1]) or (one in names[1] and two in names[0])


def diagnose_candidate(item, entities, rows, stage, policy):
    output = []
    people = sorted(baseline.item_ids(entities[item["entity_id"]], "P710"))
    names = [policy_names(entities.get(person, {}), policy) for person in people]
    labels = [eligible_terms([baseline.resolved_label(entities.get(person, {}))[0]]) for person in people]
    def add(metric, count, unit="rows"):
        output.append({"candidate_catalog_id": item["entity_id"], "metadata_stage": stage, "name_policy": policy,
            "metric": metric, "count": int(count), "unit": unit, "counterfactual_only": policy != "current" or stage != "normalized"})
    def tally(prefix, selected):
        add(prefix + "_rows", len(selected))
        add(prefix + "_distinct_nonempty_ids", len({r.get("match_id") for r in selected if r.get("match_id") not in (None, "")}), "ids")
    collision = len(names[0] & names[1])
    add("participant_name_collision_terms", collision, "terms")
    add("name_policy_collision_blocked", bool(collision), "candidates")
    checks = [
        ("competition", lambda row: row.get("competition_group") == "mens_singles"),
        ("tournament", lambda row: baseline.normalized(row.get("tournament")) in {"wimbledon", "wimbledon championships", "the championships, wimbledon"}),
        ("parseable_date", lambda row: date_status(row.get("match_date")) == "calendar_date"),
        ("exact_day", lambda row: baseline.match_date(row.get("match_date")) == item["date"]),
        ("final_round", lambda row: baseline.normalized(row.get("round")) in {"f", "final"}),
        ("name_pair", lambda row: exact_pair(row, names)),
    ]
    current = list(rows)
    tally("input", current)
    for name, predicate in checks:
        add("independent_" + name, sum(predicate(row) for row in rows))
        selected = [row for row in current if predicate(row)]
        add("first_failure_" + name, len(current) - len(selected))
        current = selected
        tally("through_" + name, current)
    identity_rows = list(current)
    add("exact_identity_direct_player_order", sum(baseline.normalized(row.get("player1_name")) in names[0] and baseline.normalized(row.get("player2_name")) in names[1] for row in identity_rows))
    add("exact_identity_swapped_player_order", sum(baseline.normalized(row.get("player1_name")) in names[1] and baseline.normalized(row.get("player2_name")) in names[0] for row in identity_rows))
    add("exact_identity_label_only_pairs", sum(exact_pair(row, labels) for row in identity_rows))
    add("exact_identity_pairs_requiring_selected_alias", sum(not exact_pair(row, labels) for row in identity_rows))
    tests = dict(checks)
    for name, keys in (("tournament_and_day", ("tournament", "exact_day")), ("round_and_day", ("final_round", "exact_day")),
                       ("pair_and_tournament_day_round", ("name_pair", "tournament", "exact_day", "final_round"))):
        add("independent_" + name, sum(all(tests[key](row) for key in keys) for row in rows))
    quality_failed = False
    if stage == "normalized":
        gates = [("id_nonempty", lambda row: row.get("match_id") not in (None, "")),
            ("singles_quality", lambda row: baseline.normalized(row.get("singles_metadata_eligible")) == "true"),
            ("retirement_quality", lambda row: baseline.normalized(row.get("source_retirement_or_walkover_flag")) == "false"),
            ("date_precision", lambda row: row.get("source_match_date_precision") == "calendar_date")]
        for name, predicate in gates:
            add("identity_quality_failure_" + name, sum(not predicate(row) for row in identity_rows))
            selected = [row for row in current if predicate(row)]
            add("first_failure_" + name, len(current) - len(selected))
            quality_failed |= len(current) != len(selected)
            current = selected
            tally("through_" + name, current)
        add("existing_policy_blocked_by_any_identity_quality_failure", quality_failed, "candidates")
    for category, condition in (("zero", len(current) == 0), ("one", len(current) == 1), ("multiple", len(current) > 1)):
        add("candidate_cardinality_" + category, condition, "candidates")
    tally("candidate", current)
    ids = Counter(row.get("match_id") for row in rows if row.get("match_id") not in (None, ""))
    repeated_id = len(current) == 1 and ids.get(current[0].get("match_id"), 0) != 1
    add("single_candidate_id_repeated_in_entire_metadata", repeated_id, "candidates")
    outcome = "name_collision" if collision else "quality_blocked" if quality_failed else "zero" if not current else "multiple" if len(current) > 1 else "repeated_id" if repeated_id else "one"
    # Even 'one' is a diagnostic classification, never an accepted match join.
    return output, outcome, identity_rows


def safe_path(path, parent):
    path.relative_to(parent)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("output_symlink_rejected")
        if part == parent:
            break
    return path


def summary_aggregates(terms, prerequisites, gates, global_counts):
    """Compact bounded copies of aggregate metrics, with no source values."""
    if sum(map(len, (terms, prerequisites, gates, global_counts))) > 2048:
        raise ValueError("aggregate_summary_metric_bound_exceeded")
    def nested(rows, dimensions):
        output = {}
        for row in rows:
            if type(row.get("count")) is not int or row["count"] < 0 or not re.fullmatch(r"[a-z][a-z0-9_]{0,150}", row.get("metric", "")):
                raise ValueError("aggregate_summary_invalid_metric")
            if "candidate_catalog_id" in row and row["candidate_catalog_id"] not in baseline.CANDIDATES:
                raise ValueError("aggregate_summary_unexpected_candidate")
            cursor = output
            for dimension in dimensions:
                cursor = cursor.setdefault(str(row[dimension]), {})
            if row["metric"] in cursor:
                raise ValueError("aggregate_summary_duplicate_metric")
            cursor[row["metric"]] = row["count"]
        return output
    exclusions = Counter(row["reason"] for row in prerequisites if row["status"] == "excluded")
    return {
        "cc0": {"rights": "CC0", "prerequisite_exclusion_counts": dict(exclusions),
            "participant_term_counts": nested(terms, ("candidate_catalog_id", "participant_slot"))},
        "research_only": {"rights": "CC-BY-NC-SA-4.0", "diagnostic_only": True,
            "matching_gate_counts": nested(gates, ("candidate_catalog_id", "metadata_stage", "name_policy")),
            "source_metadata_counts": nested(global_counts, ("metadata_stage",))},
        "reading_note": "Independent gates overlap; first-failure gates partition rows. Direct and swapped observations are not additive accepted matches. Both-language aliases and source-export results are counterfactual only."}


def publish(tables, summary, sources, notices):
    require_github_hosted_runner()
    output = safe_path(ROOT / "data/tennis_alignment_diagnostic", ROOT)
    output.mkdir(parents=True, exist_ok=True)
    generated = []
    for name, (rows, columns) in tables.items():
        path = safe_path(output / name, ROOT)
        baseline.write_csv(path, rows, columns)
        generated.append(path)
    schema = {"kind": "aggregate_counts_only", "null_sentinel": r"\N", "count_dtype": "nonnegative integer", "accepted_join_output": False,
        "tables": {name: {"columns": columns, "rows": len(rows)} for name, (rows, columns) in tables.items()},
        "counterfactual_only": "True means an alternative alias policy or source-export diagnostic; never an accepted join.",
        "rights": {"cc0/": "CC0 source-contract counts", "research_only/": "MCP-derived CC-BY-NC-SA-4.0 research diagnostics"}}
    docs = {"summary.json": summary, "schema.json": schema, "source_manifest.json": {"pins": PINS, "sources": sources, "code_revision": summary["code_revision"]}}
    for name, value in docs.items():
        path = safe_path(output / name, ROOT)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        generated.append(path)
    notices = {**notices, "research_only/CHANGES.txt": b"Aggregate identity-gate and source-alias diagnostics only. MCP source attribution/noncommercial/share-alike notice is preserved. No match rows, scores, outcomes, prices, accepted weather joins or training outputs are published. CC0-only metrics are separate.\n"}
    for name, content in notices.items():
        if name not in {"cc0/SOURCE_LICENSE.txt", "research_only/SOURCE_MCP_LICENSE.txt", "research_only/CHANGES.txt"}:
            raise ValueError("unexpected_license_output")
        path = safe_path(output / name, ROOT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        generated.append(path)
    dist = safe_path(ROOT / "dist", ROOT)
    dist.mkdir(exist_ok=True)
    archive = safe_path(dist / "csv-tennis-alignment-diagnostic.tar.gz", ROOT)
    inventory = []
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(generated):
            safe_path(path, ROOT)
            tar.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
            inventory.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())})
    manifest = {"archive": archive.name, "bytes": archive.stat().st_size, "sha256": sha(archive.read_bytes()), "files": inventory,
        "contains_source_rows": False, "rights": schema["rights"]}
    for name, content in (("summary", summary), ("schema", schema), ("asset_manifest", manifest)):
        path = safe_path(dist / ("tennis_alignment_diagnostic_" + name + ".json"), ROOT)
        path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")


def main():
    require_github_hosted_runner()
    remote = Remote()
    terms, prerequisites, gates, global_counts, comparisons = [], [], [], [], []
    notices, input_status, errors = {}, {}, []
    baseline_outcomes, alternative_outcomes = Counter(), Counter()
    stage = "snapshot"
    def timed_out(_signal, _frame):
        raise TimeoutError("collection_time_budget_exceeded")
    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    try:
        prefix = PINS["snapshot"]["prefix"]
        snapshot = bundle(remote, "snapshot", ["wikidata_cc0/claims.csv.gz", "wikidata_cc0/labels.csv.gz", "wikidata_cc0/LICENSE.txt", "schema.json", "source_manifest.json"])
        notices["cc0/SOURCE_LICENSE.txt"] = snapshot[prefix + "wikidata_cc0/LICENSE.txt"]
        expected = schema_contract(snapshot[prefix + "schema.json"], "snapshot")
        claims = baseline.read_csv(snapshot[prefix + "wikidata_cc0/claims.csv.gz"])
        labels = baseline.read_csv(snapshot[prefix + "wikidata_cc0/labels.csv.gz"])
        if len(claims) != expected["wikidata_cc0/claims.csv.gz"] or len(labels) != expected["wikidata_cc0/labels.csv.gz"]:
            raise ValueError("snapshot_schema_row_counts_disagree")
        entities = replay_entities(claims, labels)
        terms = term_counts(entities)
        valid, rejected = baseline.candidate_preflight(entities)
        prerequisites = [{"candidate_catalog_id": key, "status": "passed", "reason": "validated_frozen_prerequisites"} for key in sorted(valid)]
        prerequisites += [{"candidate_catalog_id": row["match_entity_id"], "status": "excluded", "reason": row["reason"]} for row in rejected]
        input_status["snapshot"] = "pinned_archive_and_selected_members_verified"
        if valid:
            stage = "mcp"
            prefix = PINS["mcp"]["prefix"]
            members = ["mens_singles/matches.csv.gz", "mens_singles/source_matches.csv.gz", "schema.json", "source_manifest.json", "LICENSE.txt"]
            mcp = bundle(remote, "mcp", members)
            notices["research_only/SOURCE_MCP_LICENSE.txt"] = mcp[prefix + "LICENSE.txt"]
            expected = schema_contract(mcp[prefix + "schema.json"], "mcp")
            producer = json.loads(mcp[prefix + "source_manifest.json"])
            if producer.get("license") != "CC-BY-NC-SA-4.0":
                raise ValueError("mcp_source_manifest_license_mismatch")
            normalized = read_identity_csv(mcp[prefix + "mens_singles/matches.csv.gz"])
            if not normalized or not all(set(MATCH_FIELDS).issubset(row) for row in normalized):
                raise ValueError("normalized_metadata_schema_missing")
            source_export = read_identity_csv(mcp[prefix + "mens_singles/source_matches.csv.gz"], raw_source=True)
            if len(normalized) != expected["mens_singles/matches.csv.gz"] or len(source_export) != expected["mens_singles/source_matches.csv.gz"]:
                raise ValueError("mcp_schema_row_counts_disagree")
            raw, parse_counts = raw_identity_rows(source_export)
            input_status["mcp"] = "pinned_archive_and_selected_members_verified"
            global_counts = metadata_counts(normalized, "normalized") + metadata_counts(raw, "source_export")
            global_counts += [{"metadata_stage": "source_export", "metric": key, "count": int(value)} for key, value in parse_counts.items()]
            for asset in producer.get("assets", []):
                if asset.get("file") == "charting-m-matches.csv":
                    quality = asset.get("resolved_metadata_columns", {}).get("key_quality", {})
                    for key in ("normalized_exact_duplicates_removed", "missing_match_key_rows_excluded", "conflicting_match_key_rows_excluded"):
                        if type(quality.get(key)) is int and quality[key] >= 0:
                            global_counts.append({"metadata_stage": "published_producer_counters", "metric": key, "count": quality[key]})
            normalized_ids = {row.get("match_id") for row in normalized if row.get("match_id") not in (None, "")}
            global_counts += compare_metadata(raw, normalized)
            for candidate_id, item in sorted(valid.items()):
                outcomes = {}
                for name_policy in POLICIES:
                    for metadata_stage, rows in (("normalized", normalized), ("source_export", raw)):
                        report, outcome, identity_rows = diagnose_candidate(item, entities, rows, metadata_stage, name_policy)
                        gates.extend(report)
                        if metadata_stage == "normalized":
                            outcomes[name_policy] = outcome
                        else:
                            gates.append({"candidate_catalog_id": candidate_id, "metadata_stage": "source_export", "name_policy": name_policy,
                                "metric": "raw_identity_candidate_rows_with_id_absent_from_normalized", "count": sum(row.get("match_id") not in normalized_ids for row in identity_rows),
                                "unit": "rows", "counterfactual_only": True})
                baseline_outcomes[outcomes["current"]] += 1
                alternative_outcomes[outcomes["both_alias_languages"]] += 1
                comparisons.append({"candidate_catalog_id": candidate_id, "metadata_stage": "normalized", "name_policy": "both_alias_languages",
                    "metric": "transition_" + outcomes["current"] + "_to_" + outcomes["both_alias_languages"], "count": 1, "unit": "candidates", "counterfactual_only": True})
        else:
            input_status["mcp"] = "not_requested_no_valid_frozen_candidates"
    except (ValueError, RuntimeError, TimeoutError, requests.RequestException, KeyError, TypeError) as exc:
        message = str(exc)
        code = message if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", message) else type(exc).__name__
        response = getattr(exc, "response", None)
        errors.append({"stage": stage, "code": code, "http_status": getattr(response, "status_code", None)})
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    gates += comparisons
    tables = {"cc0/term_counts.csv.gz": (terms, TERM_COLUMNS), "cc0/prerequisite_audit.csv.gz": (prerequisites, PREREQ_COLUMNS),
        "research_only/matching_gate_counts.csv.gz": (gates, GATE_COLUMNS), "research_only/source_metadata_counts.csv.gz": (global_counts, GLOBAL_COLUMNS)}
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "code_revision": os.environ.get("GITHUB_SHA"),
        "runtime_versions": {"python": sys.version.split()[0], "pandas": pd.__version__},
        "status": "partial_diagnostic" if errors else "diagnostic_complete" if gates else "audit_only",
        "source_inputs": input_status, "errors": errors, "entity_candidates": len(baseline.CANDIDATES),
        "prerequisite_pass_count": sum(row["status"] == "passed" for row in prerequisites), "baseline_lookup_classifications": dict(baseline_outcomes),
        "counterfactual_lookup_classifications": dict(alternative_outcomes), "accepted_match_joins": 0, "accepted_weather_rows": 0,
        "aggregate_diagnostics": summary_aggregates(terms, prerequisites, gates, global_counts),
        "source_bytes": remote.bytes, "http_attempts": remote.requests, "source_archive_bytes_expected": sum(pin["bytes"] for pin in PINS.values()),
        "bounds": {"inbound_bytes": MAX_BYTES, "http_attempts_including_redirects": MAX_REQUESTS, "collection_seconds": MAX_SECONDS},
        "live_source_refreshes": 0, "training_performed": False, "contains_source_rows": False,
        "limitations": ["Diagnostic classifications never promote a source match into accepted weather coverage.",
            "Only existing fixed candidates are examined; earlier zero-join releases remain unchanged.",
            "Current source labels plus both provided alias languages are a counterfactual, not a fuzzy-name policy.",
            "Source-export metadata already reflects the producer's import-time empty-to-null policy; original pre-import empty strings cannot be recovered.",
            "Raw identity-field conflicts do not reproduce every producer normalization field or retirement flag.",
            "Reference counts are retained from frozen claims without inventing reference objects.",
            "CC0 source-contract counts and MCP-derived noncommercial diagnostics have separate rights."]}
    publish(tables, summary, remote.sources, notices)
    print(json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
