"""Synthetic in-memory tests only; no downloads, real source rows or artifacts."""
from datetime import date
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

SPEC = importlib.util.spec_from_file_location("tennis_weather_collect", Path(__file__).with_name("collect.py"))
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)


def claim(value, qualifiers=None):
    return {"id": "synthetic-claim", "rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": value}}, "qualifiers": qualifiers or {}, "references": []}


def items(*ids):
    return [claim({"id": key}) for key in ids]


def coordinate(lat=51.0, lon=-0.2):
    return claim({"latitude": lat, "longitude": lon, "globe": "http://www.wikidata.org/entity/Q2"})


def entities():
    # Fixed catalog IDs identify schema roles; dates/names/coordinates are fabricated.
    return {
        "Q128304298": {"claims": {"P585": [claim({"time": "+2024-06-01T00:00:00Z", "precision": 11, "calendarmodel": "http://www.wikidata.org/entity/Q1985727"})],
            "P710": items("Q10001", "Q10002"), "P276": items(c.COURT), "P361": items("Q120716297"), "P31": items("Q10003")}},
        "Q10001": {"labels": {"en": {"value": "Avery Example"}}},
        "Q10002": {"labels": {"en": {"value": "Blair Sample"}}},
        "Q10003": {"labels": {"en": {"value": "final"}}},
        c.COURT: {"claims": {"P361": items(c.FACILITY), "P625": [coordinate()]}},
        c.TOURNAMENT: {"claims": {"P276": items(c.FACILITY), "P625": [coordinate()]}},
    }


def match():
    return {"match_id": "synthetic-match", "competition_group": "mens_singles", "player1_name": "Blair Sample", "player2_name": "Avery Example",
        "match_date": "2024-06-01", "tournament": "Wimbledon", "round": "F", "source_match_date_precision": "calendar_date",
        "singles_metadata_eligible": "True", "source_retirement_or_walkover_flag": "False"}


def place():
    return {"place_coordinate_id": c.WEATHER_KEY, "place_id": c.TOURNAMENT, "entity_id": c.TOURNAMENT,
        "source_geometry_valid": "True", "latitude": "51.0", "longitude": "-0.2"}


def weather_rows(day):
    rows = []
    for bucket, _, _, _ in c.day_overlaps(day):
        rows.append({"weather_date_utc": bucket.isoformat(), "place_coordinate_id": c.WEATHER_KEY, "place_id": c.TOURNAMENT,
            "weather_model": "ERA5", "requested_latitude": "51.0", "requested_longitude": "-0.2", "grid_latitude": "51.1", "grid_longitude": "-0.1",
            **{field: "1.0" for field in c.DAILY_FIELDS}})
    return rows


class IdentityTests(unittest.TestCase):
    def test_unique_exact_identity_allows_player_order_swap(self):
        item = c.candidate("Q128304298", entities())
        self.assertEqual(c.match_candidate(item, [match()])["match_id"], "synthetic-match")
        self.assertFalse(item["referenced"])

    def test_default_mul_labels_resolve_without_inventing_english(self):
        data = entities()
        for key in ("Q10001", "Q10002"):
            data[key]["labels"] = {"mul": data[key]["labels"]["en"]}
        item = c.candidate("Q128304298", data)
        self.assertEqual(c.match_candidate(item, [match()])["match_id"], "synthetic-match")
        _, labels = c.fact_rows(data, "synthetic")
        row = next(row for row in labels if row["entity_id"] == "Q10001")
        self.assertIsNone(row["label_en"])
        self.assertEqual(row["resolved_label_language"], "mul")
        self.assertEqual(row["resolved_label"], row["label_mul"])

    def test_english_precedes_mul_and_missing_differs_from_overlap(self):
        data = entities()
        data["Q10001"]["labels"]["mul"] = {"value": "Different Synthetic Name"}
        self.assertEqual(c.resolved_label(data["Q10001"]), ("Avery Example", "en"))
        data["Q10001"]["labels"] = {}
        with self.assertRaisesRegex(c.Excluded, "missing_full_participant_names"):
            c.candidate("Q128304298", data)
        data = entities()
        data["Q10001"]["aliases"] = {"mul": [{"value": "Blair Sample"}]}
        with self.assertRaisesRegex(c.Excluded, "intersecting_full_participant_names"):
            c.candidate("Q128304298", data)

    def test_default_aliases_keep_language_provenance(self):
        data = {"labels": {"mul": {"value": "Avery Example"}}, "aliases": {"mul": [{"value": "Avery Extended Example"}]}}
        self.assertIn("avery extended example", c.names(data))
        self.assertEqual(c.resolved_aliases(data)[1], "mul")

    def test_one_rejected_candidate_does_not_block_valid_candidate(self):
        accepted, rejected = c.candidate_preflight(entities())
        self.assertEqual(set(accepted), {"Q128304298"})
        self.assertEqual([row["match_entity_id"] for row in rejected], ["Q121076423"])

    def test_duplicate_identity_not_chosen_by_outcome(self):
        a, b = match(), match()
        b.update(match_id="second", winner="Avery Example")
        with self.assertRaisesRegex(c.Excluded, "ambiguous_exact_mcp"):
            c.match_candidate(c.candidate("Q128304298", entities()), [a, b])

    def test_surname_only_does_not_match(self):
        row = match(); row["player1_name"] = "Sample"
        with self.assertRaises(c.Excluded):
            c.match_candidate(c.candidate("Q128304298", entities()), [row])

    def test_foreign_timezone_or_nonmidnight_mcp_date_rejected(self):
        for value in ["2024-06-01T00:00:00Z", "2024-06-01T12:00:00", "2024-06-01T00:00:00+01:00"]:
            self.assertIsNone(c.match_date(value))
        self.assertEqual(c.match_date("2024-06-01 00:00:00"), date(2024, 6, 1))

    def test_day_precision_never_becomes_kickoff(self):
        day = c.unique_date(entities()["Q128304298"])
        self.assertIs(type(day), date)
        self.assertEqual(day, date(2024, 6, 1))

    def test_month_precision_and_conflicting_dates_excluded(self):
        data = entities()["Q128304298"]
        data["claims"]["P585"][0]["mainsnak"]["datavalue"]["value"]["precision"] = 10
        with self.assertRaises(c.Excluded): c.unique_date(data)
        data = entities()["Q128304298"]
        other = claim({"time": "+2024-06-02T00:00:00Z", "precision": 11, "calendarmodel": "http://www.wikidata.org/entity/Q1985727"})
        data["claims"]["P585"].append(other)
        with self.assertRaises(c.Excluded): c.unique_date(data)

    def test_missing_conditional_entity_is_quarantined(self):
        with self.assertRaises(c.Excluded): c.candidate("Q121076423", entities())

    def test_unknown_claim_alongside_known_value_is_not_silently_dropped(self):
        data = entities()
        data["Q128304298"]["claims"]["P276"].append({"rank": "normal", "mainsnak": {"snaktype": "somevalue"}})
        with self.assertRaisesRegex(c.Excluded, "unknown_required_claim"):
            c.candidate("Q128304298", data)

    def test_wrong_tournament_edition_cannot_match(self):
        data = entities(); data["Q128304298"]["claims"]["P361"] = items("Q99999")
        with self.assertRaises(c.Excluded): c.candidate("Q128304298", data)

    def test_reference_export_omits_winner(self):
        data = entities(); data["Q128304298"]["claims"]["P1346"] = items("Q10001")
        facts, _ = c.fact_rows(data, "synthetic")
        self.assertFalse(any(row["property_id"] == "P1346" for row in facts))
        self.assertTrue(all(row["source_license"] == "CC0-1.0" for row in facts))


class WeatherTests(unittest.TestCase):
    def test_same_facility_and_recorded_coordinate_pass(self):
        _, distance = c.validate_place(entities(), [place()], [place()])
        self.assertEqual(distance, 0)

    def test_nearby_unrelated_facility_is_rejected(self):
        data = entities(); data[c.COURT]["claims"]["P361"] = items("Q99999")
        with self.assertRaisesRegex(c.Excluded, "same_facility"):
            c.validate_place(data, [place()], [place()])

    def test_coordinate_change_is_rejected(self):
        wrong = place(); wrong["latitude"] = "51.01"
        with self.assertRaisesRegex(c.Excluded, "differs_from_entity"):
            c.validate_place(entities(), [wrong], [wrong])

    def test_conflicting_coordinate_claims_are_not_arbitrarily_selected(self):
        data = entities(); data[c.TOURNAMENT]["claims"]["P625"].append(coordinate(52, -0.2))
        with self.assertRaises(c.Excluded): c.validate_place(data, [place()], [place()])

    def test_summer_day_has_two_independent_utc_buckets(self):
        parts = c.day_overlaps(date(2024, 6, 1))
        self.assertEqual([part[1] for part in parts], [3600, 82800])
        self.assertEqual(sum(part[1] for part in parts), 86400)
        self.assertEqual(parts[0][2].isoformat(), "2024-05-31T23:00:00+00:00")

    def test_dst_days_are_not_forced_to_24_hours(self):
        self.assertEqual(sum(row[1] for row in c.day_overlaps(date(2024, 3, 31))), 23*3600)
        self.assertEqual(sum(row[1] for row in c.day_overlaps(date(2024, 10, 27))), 25*3600)

    def test_missing_or_duplicate_bucket_excludes_complete_match(self):
        item = c.candidate("Q128304298", entities())
        rows = weather_rows(item["date"])
        units = {field: "synthetic-unit" for field in c.DAILY_FIELDS}
        for wrong in [rows[:1], rows + [rows[0]]]:
            with self.assertRaises(c.Excluded): c.weather_context(item, match(), wrong, place(), units)

    def test_naive_weather_bucket_rejected(self):
        item = c.candidate("Q128304298", entities())
        rows = weather_rows(item["date"])
        rows[0]["weather_date_utc"] = rows[0]["weather_date_utc"].replace("+00:00", "")
        with self.assertRaises(c.Excluded): c.weather_context(item, match(), rows, place(), {})

    def test_output_has_no_exposure_or_training_claim(self):
        item = c.candidate("Q128304298", entities())
        rows = c.weather_context(item, match(), weather_rows(item["date"]), place(), {field: "synthetic-unit" for field in c.DAILY_FIELDS})
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertFalse(row["automatic_training_join_allowed"])
            self.assertFalse(row["actual_match_interval_known"])
            self.assertEqual(row["context_class"], "calendar_day_overlap_context")
            self.assertIsInstance(row[c.DAILY_FIELDS[0]], float)
            self.assertNotIn("weather_values_json", row)

    def test_nonfinite_weather_value_cannot_be_json_number(self):
        item = c.candidate("Q128304298", entities())
        rows = weather_rows(item["date"]); rows[0][c.DAILY_FIELDS[0]] = "NaN"
        with self.assertRaisesRegex(c.Excluded, "nonfinite"):
            c.weather_context(item, match(), rows, place(), {field: "synthetic-unit" for field in c.DAILY_FIELDS})

    def test_missing_numeric_value_preserved_and_grid_validated(self):
        item = c.candidate("Q128304298", entities())
        rows = weather_rows(item["date"]); rows[0][c.DAILY_FIELDS[0]] = None
        units = {field: "synthetic-unit" for field in c.DAILY_FIELDS}
        output = c.weather_context(item, match(), rows, place(), units)
        self.assertIsNone(output[0][c.DAILY_FIELDS[0]])
        rows[0][c.DAILY_FIELDS[0]] = ""
        self.assertIsNone(c.weather_context(item, match(), rows, place(), units)[0][c.DAILY_FIELDS[0]])
        for bad in ["NaN", "91", None]:
            rows[0]["grid_latitude"] = bad
            with self.assertRaises(c.Excluded): c.weather_context(item, match(), rows, place(), units)

    def test_invalid_numeric_weather_excluded(self):
        item = c.candidate("Q128304298", entities())
        rows = weather_rows(item["date"]); rows[0][c.DAILY_FIELDS[0]] = "invalid"
        with self.assertRaisesRegex(c.Excluded, "invalid_weather_numeric"):
            c.weather_context(item, match(), rows, place(), {field: "synthetic-unit" for field in c.DAILY_FIELDS})


class ArchiveAndGuardTests(unittest.TestCase):
    def archive(self, name="data/test/table.csv.gz", duplicate=False):
        raw = gzip.compress(b"id,value\nsynthetic,1\n")
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for _ in range(2 if duplicate else 1):
                member = tarfile.TarInfo(name); member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        manifest = {"files": [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]}
        return output.getvalue(), manifest

    def test_valid_synthetic_archive_and_csv(self):
        raw, manifest = self.archive()
        selected = c.archive_members(raw, manifest, "data/test/", ["data/test/table.csv.gz"])
        self.assertEqual(c.read_csv(selected["data/test/table.csv.gz"]), [{"id": "synthetic", "value": "1"}])

    def test_empty_text_differs_from_null_and_short_rows_are_rejected(self):
        rows = c.read_csv(gzip.compress(b"id,value\nsynthetic,\nsecond,\\N\n"))
        self.assertEqual(rows[0]["value"], "")
        self.assertIsNone(rows[1]["value"])
        with self.assertRaisesRegex(ValueError, "row_shape"):
            c.read_csv(gzip.compress(b"id,value\nsynthetic\n"))

    def test_duplicate_archive_member_rejected(self):
        raw, manifest = self.archive(duplicate=True)
        with self.assertRaises(ValueError): c.archive_members(raw, manifest, "data/test/", ["data/test/table.csv.gz"])

    def test_traversal_and_member_hash_mismatch_rejected(self):
        raw, manifest = self.archive("data/test/../escape")
        with self.assertRaises(ValueError): c.archive_members(raw, manifest, "data/test/", [])
        raw, manifest = self.archive(); manifest["files"][0]["sha256"] = "bad"
        with self.assertRaises(ValueError): c.archive_members(raw, manifest, "data/test/", ["data/test/table.csv.gz"])

    def test_main_and_fetch_guard_precede_side_effects(self):
        with patch.object(c, "require_github_hosted_runner", side_effect=RuntimeError("guard")), patch.object(c.requests, "get") as get:
            with self.assertRaisesRegex(RuntimeError, "guard"): c.main()
            with self.assertRaisesRegex(RuntimeError, "guard"): c.Remote().get("https://www.wikidata.org/", 100)
            get.assert_not_called()

    def test_request_budget_checked_before_network(self):
        remote = c.Remote(); remote.requests = c.MAX_REQUESTS
        with self.assertRaisesRegex(RuntimeError, "budget"): remote.check()

    def test_entity_requests_explicit_english_and_default_language(self):
        class SyntheticRemote:
            def __init__(self): self.urls = []
            def get(self, url, limit):
                self.urls.append(url)
                return json.dumps({"entities": entities()}).encode()
        remote = SyntheticRemote()
        c.fetch_entities(remote)
        self.assertTrue(remote.urls)
        for url in remote.urls:
            self.assertEqual(parse_qs(urlparse(url).query)["languages"], ["en|mul"])

    def test_no_valid_candidate_skips_both_source_archives(self):
        # Every I/O boundary is mocked with synthetic input; no guard environment
        # is spoofed, no real request is possible and publication is intercepted.
        data = entities(); data["Q128304298"]["claims"].pop("P276")
        with patch.object(c, "require_github_hosted_runner"), patch.object(c, "fetch_entities", return_value=data), \
             patch.object(c, "fetch_bundle") as archives, patch.object(c, "publish") as publish, \
             patch.object(c.requests, "get", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new_callable=io.StringIO):
            c.main()
        archives.assert_not_called()
        summary = publish.call_args.args[2]
        self.assertEqual(summary["status"], "audit_only")
        self.assertEqual(summary["quarantine_count"], 2)
        self.assertEqual(summary["input_status"]["candidate_preflight"], "no_candidates_passed")
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["source_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
