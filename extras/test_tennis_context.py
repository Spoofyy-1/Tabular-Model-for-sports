"""Only tiny fabricated in-memory examples; never real API responses."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import tennis_context as context


def fixture():
    values = {"entity": "http://www.wikidata.org/entity/Q999999", "place": "http://www.wikidata.org/entity/Q999998",
              "entityLabel": "Synthetic event", "placeLabel": "Synthetic place", "latitude": "30", "longitude": "40",
              "elevation": "100", "elevation_unit": "http://www.wikidata.org/entity/Q11573", "sitelinks": "5", "relation": "event_location"}
    return {"results": {"bindings": [{name: {"value": value} for name, value in values.items()}]}}


def weather():
    return {"utc_offset_seconds": 0, "latitude": 30.1, "longitude": 40.1, "elevation": 95,
            "daily": {"time": ["2024-12-31", "2025-01-01"], **{name: [1, None] for name in context.DAILY}}}


class TennisContextTests(unittest.TestCase):
    def test_ids_units_and_asof_flags(self):
        frame = context.normalize_bindings(fixture())
        self.assertEqual(frame.place_id.iloc[0], "Q999998")
        self.assertEqual(frame.elevation_unit.iloc[0], "http://www.wikidata.org/entity/Q11573")
        self.assertTrue(frame.source_geometry_valid.iloc[0])
        self.assertFalse(frame.automatic_training_join_allowed.any())
        self.assertFalse(frame.historical_place_assignment_verified.any())

    def test_bad_geometry_preserved_but_excluded_from_weather(self):
        source = fixture()
        source["results"]["bindings"][0]["latitude"]["value"] = "999"
        frame = context.normalize_bindings(source)
        self.assertEqual(len(frame), 1)
        self.assertFalse(frame.source_geometry_valid.any())
        self.assertTrue(context.select_weather_places(frame, 2).empty)

    def test_weather_selection_deduplicates_venue_coordinate(self):
        frame = context.normalize_bindings(fixture())
        repeated = pd.concat([frame, frame.assign(entity_id="Q999997")], ignore_index=True)
        self.assertEqual(len(context.select_weather_places(repeated, 12)), 1)

    def test_weather_utc_split_and_missing_value(self):
        place = context.normalize_bindings(fixture()).iloc[0]
        frame = context.weather_frame(weather(), place, "2024-12-31", "2025-01-01")
        self.assertEqual(frame.partition_by_date.tolist(), ["development_through_2024", "holdout_2025_onward"])
        self.assertTrue(pd.isna(frame.temperature_2m_max.iloc[1]))
        self.assertEqual(str(frame.weather_date_utc.dtype), "datetime64[ns, UTC]")
        self.assertFalse(frame.is_historical_forecast.any())
        self.assertFalse(frame.verified_asof.any())

    def test_incomplete_or_wrong_zone_weather_rejected(self):
        place = context.normalize_bindings(fixture()).iloc[0]
        for alteration in ["timezone", "missing_day", "missing_variable"]:
            with self.subTest(alteration=alteration):
                payload = weather()
                if alteration == "timezone":
                    payload["utc_offset_seconds"] = 3600
                elif alteration == "missing_day":
                    payload["daily"]["time"][1] = "2024-12-31"
                else:
                    del payload["daily"][context.DAILY[0]]
                with self.assertRaises(ValueError):
                    context.weather_frame(payload, place, "2024-12-31", "2025-01-01")

    def test_local_entrypoint_rejected_before_network_and_writes(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(context.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                context.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
