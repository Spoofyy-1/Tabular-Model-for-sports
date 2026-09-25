"""Fabricated in-memory fixtures only; no source downloads or data files."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from nfl_attendance import collect as c


def statement(prop, value=None, rank="normal", snaktype="value", qualifiers=None):
    return {"id": "synthetic-" + prop, "rank": rank, "mainsnak": {"snaktype": snaktype, "property": prop,
        "datavalue": {"value": value}}, "qualifiers": qualifiers or {}}


def qstatement(prop, qid):
    return statement(prop, {"id": qid, "entity-type": "item"})


def timevalue(year=2024, day="06-01", precision=11):
    return {"time": f"+{year}-{day}T00:00:00Z", "timezone": 0, "before": 0, "after": 0,
        "precision": precision, "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}


def count(amount="+12345", **value_changes):
    return statement("P1110", {"amount": amount, "unit": "1", **value_changes})


def entity(qid="Q54196525", year=2024, amount="+12345"):
    return {"id": qid, "lastrevid": 7, "modified": "2026-01-01T00:00:00Z", "labels": {"en": {"value": "Synthetic event", "language": "en"}},
        "claims": {"P31": [qstatement("P31", "Q32096")], "P585": [statement("P585", timevalue(year))],
            "P1923": [qstatement("P1923", "Q223522"), qstatement("P1923", "Q337758")],
            "P276": [qstatement("P276", "Q27768421")], "P1110": [count(amount)]}}


def event(data=None):
    value = c.parse_event("Q54196525", data or entity())
    value["chain_verified"] = True  # Pure isolated join fixture, not a live guard bypass.
    return value


def schedule_rows(**changes):
    common = {"game_id": "synthetic-game", "game_date": "2024-06-01T23:30:00Z", "season": "2023",
        "source_game_type": "SB", "source_stadium": "Allegiant Stadium", "source_stadium_id": "synthetic-stadium",
        "source_game_date_time_known": "True"}
    common.update(changes)
    return [{**common, "team": "KC", "opponent": "SF", "is_home": "True"},
            {**common, "team": "SF", "opponent": "KC", "is_home": "False"}]


def schedule(rows=None):
    return c.normalize_schedule(pd.DataFrame(rows or schedule_rows()))[0]


def linked_entities():
    ids, entities = list(c.EVENTS), {}
    for i, qid in enumerate(ids):
        entities[qid] = entity(qid, c.EVENTS[qid][0])
        if i:
            entities[qid]["claims"]["P155"] = [qstatement("P155", ids[i-1])]
        if i + 1 < len(ids):
            entities[qid]["claims"]["P156"] = [qstatement("P156", ids[i+1])]
    return entities


def response_entities(entities):
    return [FakeResponse(200, body=json.dumps({"entities": {qid: data}}).encode()) for qid, data in entities.items()]


class Attendance(unittest.TestCase):
    def run_inputs(self, responses):
        session, budget = FakeSession(responses), c.Budget()
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "Session", return_value=session), \
             patch.object(c, "read_schedule", return_value=(pd.DataFrame(schedule_rows()), {})) as read:
            result = c.prepare_inputs(budget)
            return result, budget, read.call_count

    def test_reported_count_exact_game_and_calendar_year_not_season(self):
        joined = c.join_events([event()], schedule())
        self.assertTrue(joined.exact_game_join_valid.iloc[0])
        self.assertTrue(joined.attendance_join_eligible.iloc[0])
        self.assertEqual(joined.season.iloc[0], "2023")
        self.assertEqual(joined.evaluation_split.iloc[0], "calibration_2024")
        self.assertFalse(joined.home_away_roles_verified.iloc[0])
        self.assertFalse(joined.verified_asof.iloc[0])

    def test_absent_unknown_novalue_and_zero_remain_distinct(self):
        data = entity()
        data["claims"].pop("P1110")
        self.assertEqual(event(data)["attendance_status"], "missing_property")
        for kind in ("somevalue", "novalue"):
            data["claims"]["P1110"] = [statement("P1110", snaktype=kind)]
            self.assertEqual(event(data)["attendance_status"], "invalid_or_unknown_claims")
            self.assertEqual(c.claim_rows("Q54196525", data)[1]["event_qid"], "Q54196525")
        data["claims"]["P1110"] = [count("0")]
        self.assertEqual(event(data)["attendance_reported"], 0)

    def test_duplicate_identical_counts_and_conflicting_counts(self):
        data = entity()
        data["claims"]["P1110"].append(count("12345.0"))
        self.assertEqual(event(data)["attendance_reported"], 12345)
        data["claims"]["P1110"].append(count("54321"))
        self.assertIsNone(event(data)["attendance_reported"])
        self.assertEqual(event(data)["attendance_status"], "conflicting_claims")
        self.assertEqual(len([r for r in c.claim_rows("Q54196525", data) if r["property_id"] == "P1110"]), 3)

    def test_invalid_count_unit_bounds_rank_and_qualifier(self):
        for bad in [count("1.5"), count("-1"), count("NaN"), count("1e100"), count("1", unit="Q5"),
                    count("12", lowerBound="11"), count("12", upperBound="13")]:
            self.assertIsNone(c.attendance_value(bad)[0])
        exact = count("12", lowerBound="12", upperBound="12")
        self.assertEqual(c.attendance_value(exact)[0], 12)
        exact["rank"] = "deprecated"
        self.assertIsNone(c.attendance_value(exact)[0])
        exact["rank"] = "normal"
        exact["qualifiers"] = {"P518": [{"snaktype": "value", "datavalue": {"value": {"id": "Q1"}}}]}
        self.assertIsNone(c.attendance_value(exact)[0])

    def test_null_quantity_bounds_match_absent_and_incomplete_bounds_rejected(self):
        self.assertEqual(c.attendance_value(count("12", lowerBound=None, upperBound=None))[0], 12)
        self.assertEqual(c.attendance_value(count("12", lowerBound=None))[0], 12)
        self.assertEqual(c.attendance_value(count("12", lowerBound="12"))[1], "incomplete_quantity_bounds")
        self.assertEqual(c.attendance_value(count("12", lowerBound="12", upperBound=None))[1], "incomplete_quantity_bounds")

    def test_invalid_claim_cannot_be_overridden_by_good_claim(self):
        data = entity()
        data["claims"]["P1110"].append(count("1.5"))
        self.assertIsNone(event(data)["attendance_reported"])

    def test_preferred_does_not_silently_replace_conflicting_normal(self):
        data = entity()
        preferred = count("54321")
        preferred["rank"] = "preferred"
        data["claims"]["P1110"].append(preferred)
        self.assertEqual(event(data)["attendance_status"], "conflicting_claims")

    def test_date_precision_ambiguity_and_outside_year(self):
        for invalid in [timevalue(2024, precision=10), timevalue(2025), timevalue(2024, "02-30")]:
            data = entity()
            data["claims"]["P585"] = [statement("P585", invalid)]
            self.assertIn("calendar_date", event(data)["identity_exclusions"])
        data = entity()
        data["claims"]["P585"].append(statement("P585", timevalue(2024, "06-02")))
        self.assertIn("calendar_date", event(data)["identity_exclusions"])

    def test_calendar_date_uses_eastern_day_and_ignores_actual_start_claim(self):
        data = entity()
        data["claims"]["P580"] = [statement("P580", {**timevalue(), "time": "+2024-06-01T19:43:00Z", "precision": 14})]
        # UTC date differs; source Eastern date still matches the event calendar date.
        joined = c.join_events([event(data)], schedule(schedule_rows(game_date="2024-06-02T01:30:00Z")))
        self.assertTrue(joined.exact_game_join_valid.iloc[0])

    def test_source_precision_false_and_nonreciprocal_games_excluded(self):
        for rows in [schedule_rows(source_game_date_time_known="False"), schedule_rows()[:1], schedule_rows() * 2,
                     schedule_rows(season="2024")]:
            valid, excluded = c.normalize_schedule(pd.DataFrame(rows))
            self.assertTrue(valid.empty)
            self.assertFalse(excluded.empty)
        rows = schedule_rows()
        rows[1]["opponent"] = "TB"
        self.assertTrue(c.normalize_schedule(pd.DataFrame(rows))[0].empty)

    def test_nominal_roles_validate_without_inferring_from_venue(self):
        data = entity()
        for statement_, role in zip(data["claims"]["P1923"], ["Q24633211", "Q24633216"]):
            statement_["qualifiers"] = {"P3831": [{"snaktype": "value", "datavalue": {"value": {"id": role}}}]}
        joined = c.join_events([event(data)], schedule())
        self.assertTrue(joined.home_away_roles_verified.iloc[0])
        data["claims"]["P1923"][0]["qualifiers"]["P3831"][0]["datavalue"]["value"]["id"] = "Q24633216"
        self.assertIn("participant_roles_not_complementary", event(data)["identity_exclusions"])
        data["claims"]["P1923"][1]["qualifiers"] = {}
        joined = c.join_events([event(data)], schedule())
        self.assertEqual(joined.join_exclusion.iloc[0], "nominal_role_conflict")

    def test_wrong_unknown_and_ambiguous_participants_fail(self):
        data = entity()
        data["claims"]["P1923"][1] = qstatement("P1923", "Q320476")
        self.assertFalse(c.join_events([event(data)], schedule()).exact_game_join_valid.iloc[0])
        data["claims"]["P1923"].append(qstatement("P1923", "Q999999999"))
        self.assertIn("participants", event(data)["identity_exclusions"])

    def test_venue_conflict_excluded_and_unknown_venue_flagged(self):
        wrong = c.join_events([event()], schedule(schedule_rows(source_stadium="SoFi Stadium")))
        self.assertEqual(wrong.join_exclusion.iloc[0], "explicit_venue_conflict")
        unknown = c.join_events([event()], schedule(schedule_rows(source_stadium="Unmapped synthetic venue")))
        self.assertTrue(unknown.exact_game_join_valid.iloc[0])
        self.assertFalse(unknown.venue_identity_verified.iloc[0])

    def test_ambiguous_schedule_and_duplicate_entity_game_do_not_double_count(self):
        rows = schedule_rows() + schedule_rows(game_id="other-synthetic-game")
        self.assertFalse(c.join_events([event()], schedule(rows)).exact_game_join_valid.iloc[0])
        duplicated = c.join_events([event(), event()], schedule())
        self.assertFalse(duplicated.exact_game_join_valid.any())

    def test_labels_en_then_mul_then_reviewed_metadata(self):
        data = entity()
        data["labels"] = {"mul": {"language": "mul", "value": "Synthetic multilingual event"}}
        self.assertEqual(event(data)["label_language"], "mul")
        self.assertEqual(event(data)["label_basis"], "source_entity_label")
        data["labels"] = {}
        self.assertEqual(event(data)["label_basis"], "reviewed_allowlist_metadata")

    def test_outcomes_quotations_and_other_properties_never_exported(self):
        data = entity()
        data["claims"]["P1346"] = [qstatement("P1346", "Q223522")]
        data["claims"]["P1923"][0]["qualifiers"] = {"P1351": [{"datavalue": {"value": "score-secret"}}]}
        data["claims"]["P1110"][0]["references"] = [{"snaks": {"P854": [{"snaktype": "value", "datavalue": {"value": "https://example.org/reference.pdf"}}],
            "P1683": [{"snaktype": "value", "datavalue": {"value": "quotation-secret"}}]}}]
        serialized = json.dumps(c.claim_rows("Q54196525", data))
        self.assertNotIn("score-secret", serialized)
        self.assertNotIn("quotation-secret", serialized)
        self.assertNotIn("P1346", serialized)
        self.assertIn("https://example.org/reference.pdf", serialized)

    def test_refined_start_qualifiers_retained_without_converting_to_kickoff(self):
        data = entity()
        start = statement("P580", timevalue())
        start["qualifiers"] = {prop: [{"snaktype": "value", "datavalue": {"value": {"id": qid}}}]
                               for prop, qid in [("P4241", "Q111"), ("P421", "Q222")]}
        data["claims"]["P580"] = [start]
        retained = [r for r in c.claim_rows("Q54196525", data) if r["property_id"] == "P580"][0]
        self.assertEqual(set(json.loads(retained["retained_qualifiers_json"])), {"P4241", "P421"})
        self.assertTrue(c.join_events([event(data)], schedule()).exact_game_join_valid.iloc[0])

    def test_date_uncertainty_is_not_exact_game_date(self):
        for key in ("before", "after"):
            uncertain = timevalue()
            uncertain[key] = 1
            self.assertIsNone(c.calendar_day(uncertain))

    def test_chain_bounded_and_conflicts_quarantined(self):
        entities = {}
        ids = list(c.EVENTS)
        for i, qid in enumerate(ids):
            entities[qid] = entity(qid, c.EVENTS[qid][0])
            if i:
                entities[qid]["claims"]["P155"] = [qstatement("P155", ids[i-1])]
            if i + 1 < len(ids):
                entities[qid]["claims"]["P156"] = [qstatement("P156", ids[i+1])]
        parsed = [c.parse_event(qid, item) for qid, item in entities.items()]
        self.assertTrue(all(r["chain_verified"] for r in c.validate_chain(parsed, entities)))
        entities[ids[2]]["claims"]["P155"] = [qstatement("P155", ids[2])]
        parsed = [c.parse_event(qid, item) for qid, item in entities.items()]
        result = c.validate_chain(parsed, entities)
        self.assertFalse(result[2]["chain_verified"])
        self.assertFalse(result[1]["chain_verified"])

    def test_partial_and_audit_only_coverage_do_not_claim_completion(self):
        events = [event()]
        result = c.coverage(events, c.join_events(events, schedule()), [])
        self.assertEqual(result["status"], "partial")
        data = entity()
        data["claims"].pop("P1110")
        events = [event(data)]
        result = c.coverage(events, c.join_events(events, schedule()), [])
        self.assertEqual(result["status"], "audit_only")

    def test_empty_inputs_preserve_schema(self):
        valid, excluded = c.normalize_schedule(pd.DataFrame(columns=c.SCHEDULE_COLUMNS))
        self.assertTrue(valid.empty)
        self.assertIn("game_id", valid)
        empty_events = [c.parse_event(qid, {}) for qid in c.EVENTS]
        self.assertFalse(c.join_events(empty_events, valid).exact_game_join_valid.any())

    def test_cloud_guard_precedes_io(self):
        with patch.object(c, "require_github_hosted_runner", side_effect=RuntimeError("blocked")), patch.object(c.requests, "Session") as session:
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                c.Budget().fetch("https://www.wikidata.org/w/api.php", "wikidata", 5)
            session.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                c.read_schedule(b"synthetic")

    def test_source_hosts_and_bounds(self):
        for url in ["http://www.wikidata.org/w/api.php", "https://www.wikidata.org.evil.test/a", "https://user@www.wikidata.org/a", "https://www.wikidata.org:123/a"]:
            with self.assertRaises(ValueError):
                c.Budget.allowed(url, "wikidata")
        c.Budget.allowed("https://release-assets.githubusercontent.com/example", "schedule")
        budget = c.Budget()
        budget.requests = c.MAX_REQUESTS
        with self.assertRaises(RuntimeError):
            budget.check()
        budget.requests = 0
        budget.deadline = 0
        with self.assertRaises(TimeoutError):
            budget.check()

    def test_source_empty_text_and_null_stadium_remain_distinct(self):
        for value in ("", "  ", None):
            joined = c.join_events([event()], schedule(schedule_rows(source_stadium=value, source_stadium_id=value)))
            if value is None:
                self.assertTrue(pd.isna(joined.source_stadium.iloc[0]))
            else:
                self.assertEqual(joined.source_stadium.iloc[0], value)
                self.assertEqual(joined.source_stadium_id.iloc[0], value)

    def test_publication_uses_inventory_without_directory_enumeration(self):
        base = Path("/synthetic/output")
        with patch.object(Path, "is_symlink", return_value=False), patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "rglob", side_effect=AssertionError("Must not enumerate stale files")):
            self.assertEqual(c.publish_inventory(base, ["cc0/claims.csv.gz"]), [base / "cc0/claims.csv.gz"])
            with self.assertRaises(ValueError):
                c.publish_inventory(base, ["../outside"])
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ValueError):
                c.publish_inventory(base, ["cc0/claims.csv.gz"])

    def test_redirects_count_and_never_follow_untrusted_host(self):
        session = FakeSession([FakeResponse(302, location="https://www.wikidata.org/second"), FakeResponse(200, body=b"{}")])
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "Session", return_value=session):
            budget = c.Budget()
            self.assertEqual(budget.fetch("https://www.wikidata.org/first", "wikidata", 10), b"{}")
            self.assertEqual(budget.requests, 2)
            self.assertEqual(budget.bytes, 2)
        session = FakeSession([FakeResponse(302, location="https://example.org/not-allowed")])
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "Session", return_value=session):
            with self.assertRaises(ValueError):
                c.Budget().fetch("https://www.wikidata.org/first", "wikidata", 10)
            self.assertEqual(session.calls, 1)

    def test_http_limit_applies_before_redirect_and_byte_excess_stops(self):
        session = FakeSession([FakeResponse(302, location="https://www.wikidata.org/second")])
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "Session", return_value=session):
            budget = c.Budget()
            budget.requests = c.MAX_REQUESTS - 1
            with self.assertRaises(RuntimeError):
                budget.fetch("https://www.wikidata.org/first", "wikidata", 10)
            self.assertEqual(session.calls, 1)
            self.assertEqual(budget.requests, c.MAX_REQUESTS)
        session = FakeSession([FakeResponse(200, body=b"123456")])
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "Session", return_value=session):
            with self.assertRaises(RuntimeError):
                c.Budget().fetch("https://www.wikidata.org/first", "wikidata", 5)

    def test_first_api_error_retains_diagnostics_and_skips_schedule(self):
        response = FakeResponse(200, body=b'{"error":{"code":"maxlag","info":"private-free-text-do-not-publish"}}', retry_after="17")
        result, budget, reads = self.run_inputs([response])
        self.assertEqual(budget.requests, 1)
        self.assertEqual(reads, 0)
        self.assertEqual(result["schedule_fetch_status"], "not_attempted_no_eligible_event")
        states = [e["entity_fetch_status"] for e in result["events"]]
        self.assertEqual(states, ["entity_fetch_failed"] + ["entity_not_fetched"] * 4)
        self.assertTrue(all(e["attendance_claims"] is None for e in result["events"]))
        failure = result["failures"][0]
        self.assertEqual(failure["api_error_code"], "maxlag")
        self.assertEqual(failure["http_status"], 200)
        self.assertEqual(failure["retry_after_seconds"], 17)
        joined = c.join_events(result["events"], result["schedule"])
        summary = c.coverage(result["events"], joined, result["failures"])
        self.assertEqual(summary["source_entities_received"], 0)
        self.assertEqual(summary["source_entities_fetch_failed"], 1)
        self.assertEqual(summary["source_entities_not_fetched"], 4)
        self.assertEqual(summary["received_events_without_attendance_property"], 0)
        self.assertEqual(summary["events_attendance_availability_unobserved"], 5)
        self.assertNotIn("private-free-text-do-not-publish", json.dumps(summary))
        self.assertNotIn("private-free-text-do-not-publish", json.dumps(budget.records))

    def test_http_error_retry_after_metadata_without_reading_body(self):
        response = FakeResponse(429, body=b"forbidden response body", retry_after="Wed, 01 Jan 2031 00:00:00 GMT")
        result, budget, reads = self.run_inputs([response])
        failure = result["failures"][0]
        self.assertEqual(failure["error"], "http_error")
        self.assertEqual(failure["http_status"], 429)
        self.assertEqual(failure["retry_after_utc"], "2031-01-01T00:00:00+00:00")
        self.assertEqual(budget.bytes, 0)
        self.assertFalse(budget.records[0]["body_read"])
        self.assertEqual(reads, 0)

    def test_unavailable_and_received_missing_claims_have_different_denominators(self):
        qid = next(iter(c.EVENTS))
        received = {"id": qid, "claims": {}}  # A valid received entity can truly lack P1110.
        responses = response_entities({qid: received}) + [FakeResponse(200, body=b'{"error":{"code":"badvalue"}}')]
        result, _, reads = self.run_inputs(responses)
        self.assertEqual(reads, 0)
        self.assertEqual(result["events"][0]["attendance_status"], "missing_property")
        self.assertEqual(result["events"][0]["attendance_claims"], 0)
        self.assertEqual(result["events"][1]["attendance_status"], "entity_fetch_failed")
        self.assertIsNone(result["events"][1]["attendance_claims"])
        joined = c.join_events(result["events"], result["schedule"])
        summary = c.coverage(result["events"], joined, result["failures"])
        self.assertEqual(summary["source_entities_received"], 1)  # Revision absence does not mean fetch failure.
        self.assertEqual(summary["received_events_without_attendance_property"], 1)
        self.assertEqual(summary["received_events_missing_or_unresolved_count"], 1)
        self.assertEqual(summary["events_attendance_availability_unobserved"], 4)

    def test_one_observed_count_does_not_classify_four_unfetched_as_missing(self):
        first = next(iter(c.EVENTS))
        events = [c.parse_event(qid, entity(qid, c.EVENTS[qid][0]) if qid == first else {}) for qid in c.EVENTS]
        empty_schedule = c.normalize_schedule(pd.DataFrame(columns=c.SCHEDULE_COLUMNS))[0]
        joined = c.join_events(events, empty_schedule)
        summary = c.coverage(events, joined, [])
        self.assertEqual(summary["source_entities_received"], 1)
        self.assertEqual(summary["events_with_reported_count"], 1)
        self.assertEqual(summary["received_events_missing_or_unresolved_count"], 0)
        self.assertEqual(summary["events_attendance_availability_unobserved"], 4)

    def test_invalid_entity_payload_is_failed_not_missing_property(self):
        qid = next(iter(c.EVENTS))
        for invalid in ({"id": qid}, {"id": qid, "claims": []}, {"id": "Q999999999", "claims": {}},
                        {"id": qid, "claims": {"P1110": [None]}}):
            result, budget, reads = self.run_inputs(response_entities({qid: invalid}))
            self.assertEqual(result["events"][0]["attendance_status"], "entity_fetch_failed")
            self.assertIsNone(result["events"][0]["attendance_claims"])
            self.assertEqual(result["failures"][0]["error"], "invalid_entity_schema_or_identity")
            self.assertEqual(budget.requests, 1)
            self.assertEqual(reads, 0)

    def test_safe_diagnostic_code_and_header_sanitization(self):
        self.assertEqual(c.safe_api_error_code({"code": "maxlag"}), "maxlag")
        for bad in ("unsafe\ntext", "https://example.org/?token=secret", "a" * 81, {"unexpected": "object"}, None):
            self.assertEqual(c.safe_api_error_code({"code": bad}), "missing_or_invalid_code")
        for bad in ("17\r\nX-Secret: value", "-5", "x" * 129, "https://example.org/token", None):
            self.assertEqual(c.retry_after_fields(bad), {"retry_after_seconds": None, "retry_after_utc": None})

    def test_no_count_or_invalid_prerequisites_prevent_schedule_download(self):
        for invalid_identity in (False, True):
            entities = linked_entities()
            for qid, data in entities.items():
                if invalid_identity:
                    data["claims"]["P585"] = [statement("P585", timevalue(2040))]
                else:
                    data["claims"].pop("P1110")
            result, budget, reads = self.run_inputs(response_entities(entities))
            self.assertEqual(budget.requests, 5)
            self.assertEqual(reads, 0)
            self.assertEqual(result["events_passing_schedule_prerequisites"], 0)
            self.assertEqual(result["schedule_fetch_status"], "not_attempted_no_eligible_event")

    def test_broken_chain_prevents_schedule_download(self):
        entities = linked_entities()
        for data in entities.values():
            data["claims"].pop("P155", None)
        result, _, reads = self.run_inputs(response_entities(entities))
        self.assertEqual(reads, 0)
        self.assertEqual(result["events_passing_schedule_prerequisites"], 0)

    def test_schedule_download_occurs_only_after_all_fixed_event_requests(self):
        responses = response_entities(linked_entities()) + [FakeResponse(200, body=b"synthetic archive placeholder")]
        result, budget, reads = self.run_inputs(responses)
        self.assertEqual(reads, 1)
        self.assertEqual(result["schedule_fetch_status"], "fetched_and_validated")
        self.assertEqual(result["events_passing_schedule_prerequisites"], 5)
        self.assertEqual([r["kind"] for r in budget.records], ["wikidata"] * 5 + ["schedule"])

    def test_later_api_error_stops_even_when_earlier_event_is_eligible(self):
        responses = response_entities(linked_entities())[:4] + [FakeResponse(200, body=b'{"error":{"code":"maxlag"}}')]
        result, budget, reads = self.run_inputs(responses)
        self.assertGreater(result["events_passing_schedule_prerequisites"], 0)
        self.assertEqual(budget.requests, 5)
        self.assertEqual(reads, 0)
        self.assertEqual(result["schedule_fetch_status"], "not_attempted_after_source_failure")

    def test_signed_redirect_targets_never_appear_in_public_request_records(self):
        session = FakeSession([FakeResponse(302, location="https://release-assets.githubusercontent.com/path?signature=synthetic-secret"),
                               FakeResponse(200, body=b"synthetic")])
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "Session", return_value=session):
            budget = c.Budget()
            budget.fetch(c.SOURCE_URL, "schedule", 100)
            self.assertNotIn("synthetic-secret", json.dumps(budget.records))
            self.assertEqual([r["http_status"] for r in budget.records], [302, 200])


class FakeResponse:
    def __init__(self, code, body=b"", location=None, retry_after=None):
        self.status_code, self.body = code, body
        self.headers = {"Location": location} if location else {}
        if retry_after is not None:
            self.headers["Retry-After"] = retry_after

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise c.requests.HTTPError("synthetic HTTP failure", response=self)

    def iter_content(self, _size):
        yield self.body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.headers, self.calls = responses, {}, 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, _url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.calls += 1
        return self.responses.pop(0)


if __name__ == "__main__":
    unittest.main()
