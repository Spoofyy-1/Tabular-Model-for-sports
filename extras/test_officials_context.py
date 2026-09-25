"""Synthetic-only officiating identities, joins, chronology and cloud guards."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import officials_context as officials


def nba_source():
    return pd.DataFrame({"game_id": [123456789], "season": [2025], "official_full_name": ["Example Ref"],
                         "official_position": ["Referee"], "official_order": [1]})


def nba_schedule():
    return officials.normalize_schedule(pd.DataFrame({"game_id": [123456789], "game_date_time": ["2024-12-15T01:00:00Z"],
                                                      "home_id": [1], "away_id": [2], "venue_id": [3]}), 2025)


def nfl_schedule():
    return officials.nfl_schedule(pd.DataFrame({"game_id": ["2024_18_AAA_BBB"], "old_game_id": ["2025010500"], "gsis": ["12345"],
                                               "gameday": ["2025-01-05"], "gametime": ["13:00"]}))


class OfficialTests(unittest.TestCase):
    def test_nba_ids_names_and_actual_date_split(self):
        frame = officials.nba_assignments(nba_source(), nba_schedule(), 2025)
        self.assertEqual(frame.game_id.iloc[0], "123456789")
        self.assertEqual(frame.evaluation_split.iloc[0], "development_through_2024")
        self.assertFalse(frame.assignment_known_before_game_verified.any())
        self.assertEqual(frame.official_identity_type.iloc[0], "source_name_only_no_stable_person_id")

    def test_nba_conflicting_name_records_are_quarantined(self):
        raw = pd.concat([nba_source(), nba_source().assign(official_order=2)], ignore_index=True)
        frame = officials.nba_assignments(raw, nba_schedule(), 2025)
        self.assertTrue(frame.quality_exclusion.eq("conflicting_game_official_records").all())

    def test_nba_missing_name_is_not_invented(self):
        raw = nba_source()
        raw["official_full_name"] = None
        frame = officials.nba_assignments(raw, nba_schedule(), 2025)
        self.assertTrue(frame.official_name.isna().all())
        self.assertEqual(frame.quality_exclusion.iloc[0], "missing_game_or_official_identity")

    def test_nfl_exact_legacy_mapping_and_season_not_used_for_split(self):
        raw = pd.DataFrame({"game_id": [2025010500], "game_key": [12345], "official_id": [7], "official_name": ["Example Ref"], "position": ["Umpire"], "season": [2024]})
        frame = officials.nfl_assignments(raw, nfl_schedule())
        self.assertEqual(frame.game_id.iloc[0], "2024_18_AAA_BBB")
        self.assertEqual(frame.source_game_id.iloc[0], "2025010500")
        self.assertEqual(frame.official_id.iloc[0], "7")
        self.assertEqual(frame.evaluation_split.iloc[0], "holdout_2025_onward")
        self.assertTrue(frame.source_game_key_agrees.iloc[0])

    def test_nfl_conflicting_second_key_is_explicit(self):
        raw = pd.DataFrame({"game_id": ["2025010500"], "game_key": ["99999"], "official_id": ["7"], "official_name": ["Example Ref"], "position": ["Umpire"], "season": [2024]})
        frame = officials.nfl_assignments(raw, nfl_schedule())
        self.assertEqual(frame.quality_exclusion.iloc[0], "source_game_key_disagrees_with_schedule")

    def test_unmatched_game_does_not_receive_guessed_date(self):
        raw = pd.DataFrame({"game_id": ["9999999999"], "official_id": ["7"], "official_name": ["Example Ref"], "position": ["Umpire"], "season": [2024]})
        frame = officials.nfl_assignments(raw, nfl_schedule())
        self.assertTrue(frame.game_id.isna().all())
        self.assertEqual(frame.evaluation_split.iloc[0], "unknown_game_date")

    def test_local_main_rejected_before_source_calls_or_output(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(officials.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                officials.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
