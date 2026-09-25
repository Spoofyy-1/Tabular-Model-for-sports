"""Synthetic contract and pregame filtering tests; no real data or network."""
from collections import Counter
import copy
import csv
import importlib.util
import io
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("pmxt_sports", Path(__file__).with_name("pmxt_sports.py"))
sports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sports)

PILOT = {"sport": "NBA", "slug": "synthetic-game-2026-04-17", "hour": "2026-04-17T23",
         "scheduled_start": "2026-04-17T23:30:00Z", "rule_date": "April 17",
         "teams": ("Team Alpha", "Team Beta"), "aliases": (("Alpha", "Team Alpha"), ("Beta", "Team Beta")),
         "props": (("Player Alpha", "points", "22.5", ">"),)}


def event():
    return {"slug": PILOT["slug"], "title": "Alpha vs Beta", "markets": [
        {"slug": PILOT["slug"], "conditionId": "0x" + "a" * 64, "outcomes": '["Alpha","Beta"]',
         "clobTokenIds": '["0001","0002"]', "sportsMarketType": "moneyline",
         "description": "NBA game April 17: Alpha wins or Beta wins including overtime.",
         "gameStartTime": "2026-04-17T23:30:00Z"},
        {"slug": "synthetic-prop", "conditionId": "0x" + "b" * 64, "outcomes": '["Yes","No"]',
         "clobTokenIds": '["0003","0004"]', "sportsMarketType": "player_points",
         "description": 'NBA game April 17: Player Alpha scores more than 22.5 points. If inactive, market will resolve "No".'}]}


class SportsPilotTests(unittest.TestCase):
    def test_moneyline_and_prop_keep_separate_identity_semantics(self):
        maps, audit = sports.resolve(event(), PILOT)
        self.assertEqual(audit["verified_moneylines"], 1)
        self.assertEqual(audit["verified_props"], 1)
        self.assertIsNone(maps[0]["player_name"])
        self.assertEqual(maps[1]["threshold_value"], "22.5")
        self.assertEqual(maps[1]["threshold_operator"], ">")
        self.assertEqual(maps[1]["inactive_policy"], "No")
        self.assertNotIn("description", maps[1])

    def test_threshold_225_does_not_match_22point5(self):
        source = event()
        source["markets"][1]["description"] = source["markets"][1]["description"].replace("22.5", "225")
        _, audit = sports.resolve(source, PILOT)
        self.assertEqual(audit["verified_props"], 0)

    def test_different_outcome_semantics_excluded(self):
        source = event()
        source["markets"][1]["outcomes"] = '["Over","Under"]'
        maps, audit = sports.resolve(source, PILOT)
        self.assertEqual(len(maps), 1)
        self.assertEqual(audit["identity_or_schedule_exclusions"], 1)

    def test_changed_schedule_excluded(self):
        source = event()
        source["markets"][0]["gameStartTime"] = "2026-04-18T00:30:00Z"
        _, audit = sports.resolve(source, PILOT)
        self.assertEqual(audit["verified_moneylines"], 0)

    def test_unknown_inactive_policy_stays_null(self):
        source = event()
        source["markets"][1]["description"] = "NBA game April 17: Player Alpha scores more than 22.5 points."
        maps, _ = sports.resolve(source, PILOT)
        self.assertIsNone(maps[1]["inactive_policy"])

    def test_pregame_filter_checks_both_source_and_receipt(self):
        maps, _ = sports.resolve(event(), PILOT)
        for source_time, received_time in [("23:29:59", "23:30:00"), ("23:30:00", "23:29:59")]:
            counts, stream = Counter(), io.StringIO()
            writer = sports.Writer(csv.DictWriter(stream, sports.EVENT_FIELDS), maps, "events", sports.Activity(), counts)
            row = {"condition_id": maps[0]["condition_id"], "source_time_utc": "2026-04-17T" + source_time + "Z",
                   "received_time_utc": "2026-04-17T" + received_time + "Z"}
            writer.writerow(row)
            self.assertEqual(stream.getvalue(), "")
            self.assertEqual(counts["events_excluded_at_or_after_scheduled_start"], 1)

    def test_nfl_inclusive_threshold_and_utc_next_day_are_preserved(self):
        pilot = copy.deepcopy(PILOT)
        pilot.update(sport="NFL", slug="synthetic-game-2026-09-10", rule_date="September 9",
                     scheduled_start="2026-09-10T00:20:00Z", props=(("Player Alpha", "passing_yards", "150", ">="),))
        source = {"slug": pilot["slug"], "title": "Alpha vs Beta", "markets": [{"slug": "passing-prop",
            "conditionId": "0x" + "b" * 64, "outcomes": '["Over","Under"]', "clobTokenIds": '["1","2"]',
            "description": 'NFL game September 9: Player Alpha records 150 or more passing yards. If inactive, market will resolve to "Under".'}]}
        maps, _ = sports.resolve(source, pilot)
        self.assertEqual(maps[0]["threshold_value"], "150")
        self.assertEqual(maps[0]["threshold_operator"], ">=")
        self.assertEqual(maps[0]["scheduled_start_utc"], "2026-09-10T00:20:00Z")


if __name__ == "__main__":
    unittest.main()
