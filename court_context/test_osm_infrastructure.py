"""Synthetic in-memory tests; no data downloads or local dataset writes."""
import copy
import contextlib
from pathlib import Path
import unittest
from unittest.mock import patch

from court_context import osm_infrastructure as pilot


class FakeResponse:
    def __init__(self, code=200, location=None):
        self.status_code = code
        self.headers = {"Location": location} if location else {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, _size):
        yield b"synthetic"


def place_row(number=1, label="Example Tennis Club", lat="40", lon="-70"):
    return {"place_coordinate_id": "Q%d-example" % number, "place_id": "Q%d" % number,
            "placeLabel": label, "latitude": lat, "longitude": lon, "source_geometry_valid": "True"}


def element():
    return {"type": "way", "id": 123, "version": 2, "timestamp": "2019-12-01T12:00:00Z",
            "uid": 12345, "user": "Synthetic mapper", "center": {"lat": 40, "lon": -70},
            "tags": {"sport": "tennis", "leisure": "pitch", "surface": "clay", "lit": ""}}


class CourtPilotTests(unittest.TestCase):
    def setUp(self):
        self.place = pilot.select_places([place_row()])[0]
        self.evidence = {"source_query_sha256": "example", "source_response_sha256": "example", "retrieved_at_utc": "2026-01-01T00:00:00Z"}

    def parse(self, elements):
        return pilot.court_rows({"elements": elements}, self.place, pilot.SNAPSHOTS[0], self.evidence)

    def test_selection_rejects_city_and_event_names(self):
        labels = ["Example City", "Example Tennis Open", "Example Tennis Championships", "Tennis Tournament Club", "Example Stadium"]
        self.assertEqual(pilot.select_places([place_row(i + 1, label) for i, label in enumerate(labels)]), [])
        self.assertTrue(pilot.facility_label("Example Lawn Tennis and Croquet Club"))

    def test_selection_caps_and_deduplicates_coordinates(self):
        rows = [place_row(i, lat=str(40 + i / 10)) for i in range(1, 8)]
        rows += [place_row(10, lat="40.1")]
        selected = pilot.select_places(rows)
        self.assertEqual(len(selected), 3)
        self.assertEqual(len({(x["latitude"], x["longitude"]) for x in selected}), 3)

    def test_invalid_geometry_and_missing_schema(self):
        rows = [place_row(lat="nan"), place_row(lat="85"), place_row(lon="180")]
        self.assertEqual(pilot.select_places(rows), [])
        with self.assertRaisesRegex(ValueError, "schema"):
            pilot.select_places([{}])

    def test_multiple_coordinates_for_one_place_select_once(self):
        alternate = place_row(1, lat="40.1")
        alternate["place_coordinate_id"] = "Q1-alternative-coordinate"
        selected = pilot.select_places([place_row(), alternate, place_row(2, lat="40.2")])
        self.assertEqual(len(selected), 2)
        self.assertEqual(len({p["place_id"] for p in selected}), 2)

    def test_bbox_is_under_one_kilometer(self):
        south, west, north, east = pilot.bbox(self.place)
        self.assertLess((north - south) * 112, 1)
        self.assertLess((east - west) * 112, 1)
        self.assertIn('["leisure"="pitch"]', pilot.query_for(self.place, pilot.SNAPSHOTS[0]))
        with self.assertRaises(ValueError):
            pilot.query_for(self.place, "2011-01-01T00:00:00Z")

    def test_missing_and_empty_tags_remain_distinct(self):
        rows, rejected = self.parse([element()])
        self.assertEqual(rejected, {})
        self.assertIsNone(rows[0]["covered"])
        self.assertEqual(rows[0]["lit"], "")
        self.assertFalse(rows[0]["automatic_training_join_allowed"])
        self.assertFalse(rows[0]["roof_operation_observed"])
        self.assertNotIn("uid", rows[0])
        self.assertNotIn("user", rows[0])

    def test_future_edit_and_naive_timestamp_rejected(self):
        future, naive = element(), element()
        naive["id"] = 124
        future["timestamp"] = "2021-01-01T00:00:00Z"
        naive["timestamp"] = "2019-01-01T00:00:00"
        rows, rejected = self.parse([future, naive])
        self.assertEqual(rows, [])
        self.assertEqual(rejected["edit_timestamp_after_snapshot"], 1)
        self.assertEqual(rejected["timestamp_without_timezone"], 1)

    def test_incomplete_response_never_treated_as_zero(self):
        with self.assertRaises(ValueError):
            pilot.court_rows({"elements": [], "remark": "synthetic timeout"}, self.place, pilot.SNAPSHOTS[0], self.evidence)

    def test_duplicate_and_outside_center_are_rejected(self):
        outside = copy.deepcopy(element())
        outside["id"], outside["center"]["lat"] = 124, 50
        rows, rejected = self.parse([element(), element(), outside])
        self.assertEqual(len(rows), 0)
        self.assertEqual(rejected["duplicate_element_identity"], 2)
        self.assertEqual(rejected["center_outside_query_box_or_invalid"], 1)

    def test_conflicting_duplicate_quarantines_both_versions(self):
        other = element()
        other["version"] = 3
        other["tags"]["surface"] = "grass"
        rows, rejected = self.parse([element(), other])
        self.assertEqual(rows, [])
        self.assertEqual(rejected["duplicate_element_identity"], 2)

    def test_wrong_tags_and_invalid_ids(self):
        wrong, boolean_id = element(), element()
        wrong["tags"]["sport"] = "padel"
        boolean_id["id"] = True
        rows, rejected = self.parse([wrong, boolean_id])
        self.assertEqual(rows, [])
        self.assertEqual(sum(rejected.values()), 2)

    def test_malformed_element_objects_are_reported(self):
        malformed = element()
        malformed["tags"] = []
        rows, rejected = self.parse([None, malformed])
        self.assertEqual(rows, [])
        self.assertEqual(sum(rejected.values()), 2)

    def test_local_network_and_write_guards(self):
        with patch.object(pilot, "require_github_hosted_runner", side_effect=RuntimeError("blocked")), patch.object(pilot.requests, "get") as request:
            with self.assertRaises(RuntimeError):
                pilot.fetch("https://example.invalid", pilot.Budget(), 10)
            with self.assertRaises(RuntimeError):
                pilot.write_csv(Path("never-written.csv.gz"), [], [])
            with self.assertRaises(RuntimeError):
                pilot.main()
            request.assert_not_called()

    def test_source_hash_rejects_synthetic_invalid_archive(self):
        with self.assertRaisesRegex(ValueError, "integrity"):
            pilot.source_places(b"synthetic invalid archive")

    def test_source_budget(self):
        budget = pilot.Budget()
        budget.consume(pilot.MAX_BYTES)
        with self.assertRaises(ValueError):
            budget.consume(1)

    def test_overpass_redirect_is_refused_and_counted(self):
        budget = pilot.Budget()
        with patch.object(pilot, "require_github_hosted_runner"), patch.object(pilot, "deadline", contextlib.nullcontext), patch.object(pilot.requests, "post", return_value=FakeResponse(302, "https://example.invalid")) as post:
            with self.assertRaisesRegex(ValueError, "http_status_302"):
                pilot.fetch(pilot.ENDPOINT, budget, 100, "synthetic query")
            self.assertEqual(budget.http_requests, 1)
            self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_archive_redirect_whitelist_and_hop_count(self):
        budget = pilot.Budget()
        with patch.object(pilot, "require_github_hosted_runner"), patch.object(pilot, "deadline", contextlib.nullcontext), patch.object(pilot.requests, "get", side_effect=[FakeResponse(302, "https://release-assets.githubusercontent.com/synthetic"), FakeResponse()]) as get:
            self.assertEqual(pilot.fetch(pilot.URL, budget, 100), b"synthetic")
            self.assertEqual(budget.http_requests, 2)
            self.assertEqual(get.call_count, 2)
        with patch.object(pilot, "require_github_hosted_runner"), patch.object(pilot, "deadline", contextlib.nullcontext), patch.object(pilot.requests, "get", return_value=FakeResponse(302, "https://example.invalid")) as get:
            with self.assertRaisesRegex(ValueError, "destination_disallowed"):
                pilot.fetch(pilot.URL, pilot.Budget(), 100)
            self.assertEqual(get.call_count, 1)

    def test_archive_redirect_and_total_request_caps(self):
        budget = pilot.Budget()
        with patch.object(pilot, "require_github_hosted_runner"), patch.object(pilot, "deadline", contextlib.nullcontext), patch.object(pilot.requests, "get", return_value=FakeResponse(302, "https://github.com/synthetic")) as get:
            with self.assertRaisesRegex(ValueError, "redirect_limit"):
                pilot.fetch(pilot.URL, budget, 100)
            self.assertEqual(budget.http_requests, 4)
            self.assertEqual(get.call_count, 4)
        budget.http_requests = 10
        with self.assertRaisesRegex(ValueError, "http_request_budget"):
            budget.request()


if __name__ == "__main__":
    unittest.main()
