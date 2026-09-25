"""Synthetic in-memory checks only: no real data, network access, or data files."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_context as nba


class NBAContext(unittest.TestCase):
    def test_local_main_rejects_before_io(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(nba.requests, "get") as request, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                nba.main()
            request.assert_not_called()
            mkdir.assert_not_called()

    def test_local_write_rejects_before_directory_or_file_creation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "mkdir") as mkdir, patch.object(pd.DataFrame, "to_parquet") as writer:
            with self.assertRaises(RuntimeError):
                nba.write_parquet(pd.DataFrame(), Path("unused-synthetic-path.parquet"))
            mkdir.assert_not_called()
            writer.assert_not_called()

    def test_utc_split_boundary_and_missing_date(self):
        values = nba.dates(pd.Series(["2023-12-31T23:59:59Z", "2024-01-01T00:00:00Z", "2024-12-31T19:00:00-05:00", None]))
        self.assertEqual(nba.split(values).tolist(), ["fit_pre_2024", "calibration_2024", "holdout_2025_plus", "unknown_date"])

    def test_calendar_precision_does_not_claim_exact_cutoff_membership(self):
        events = nba.normalize_events(pd.DataFrame({"game_id": [1, 2, 3], "game_date": ["2024-12-31", "2025-01-01", "2024-06-01"]}), 2025)
        self.assertEqual(set(events.source_game_date_precision), {"source_calendar_date"})
        self.assertEqual(events.set_index("game_id").evaluation_split.to_dict(), {"1": "date_precision_unresolved", "2": "date_precision_unresolved", "3": "calibration_2024"})

    def test_numeric_event_order_and_nullable_actor_roles(self):
        raw = pd.DataFrame({"game_id": [1, 1, 1], "id": [100, 20, 3], "game_play_number": [10, 2, 1], "athlete_id_1": [7, None, 7], "athlete_id_2": [9, None, None]})
        events = nba.normalize_events(raw, 2024)
        self.assertEqual(events.event_id.tolist(), ["3", "20", "100"])
        self.assertEqual(events.primary_player_id.dropna().tolist(), ["7", "7"])
        self.assertEqual(events.secondary_actor_id.dropna().tolist(), ["9"])
        self.assertNotIn("defender_id", events)

    def test_unknown_shooting_flags_have_explicit_coverage(self):
        raw = pd.DataFrame({"game_id": [1, 1, 1], "id": [1, 2, 3], "athlete_id_1": [7, 7, None], "shooting_play": [True, None, True], "scoring_play": [False, None, True], "text": ["Player misses 24-foot three point jumper", "Player turnover", "No actor recorded"]})
        context = nba.player_event_context(nba.normalize_events(raw, 2024))
        self.assertEqual(len(context), 1)
        row = context.iloc[0]
        self.assertEqual(row.primary_actor_events, 2)
        self.assertEqual(row.observed_shooting_events, 1)
        self.assertEqual(row.shooting_flag_observed_events, 1)
        self.assertEqual(row.period_observed_events, 0)
        self.assertEqual(row.text_identified_three_point_events, 1)
        self.assertEqual(row.described_shot_distance_mean_feet, 24)
        self.assertTrue(row.same_game_retrospective_only)

    def test_missing_actors_do_not_create_synthetic_player_rows(self):
        events = nba.normalize_events(pd.DataFrame({"game_id": [1], "id": [1]}), 2024)
        context = nba.player_event_context(events)
        self.assertTrue(context.empty)
        self.assertEqual(nba.table_report(events)["missing_values"]["primary_player_id"], 1)

    def test_schedule_intervals_sorted_chronologically(self):
        raw = pd.DataFrame({"game_id": [2, 1], "date": ["2024-01-02T02:00:00Z", "2024-01-01T01:00:00Z"], "home_id": [2, 1], "away_id": [1, 2], "venue_id": [4, 3]})
        context = nba.schedule_team_context(nba.normalize_schedule(raw, 2024))
        self.assertEqual(context.hours_since_previous_listed_game.dropna().tolist(), [25, 25])
        self.assertTrue(context.loc[context.game_id.eq("1"), "hours_since_previous_listed_game"].isna().all())
        self.assertTrue(context.loc[context.game_id.eq("1"), "venue_changed_since_previous_listed_game"].isna().all())

    def test_calendar_only_schedule_does_not_invent_hour_precision(self):
        raw = pd.DataFrame({"game_id": [1, 2], "date": ["2024-06-01", "2024-06-02"], "home_id": [1, 2], "away_id": [2, 1]})
        context = nba.schedule_team_context(nba.normalize_schedule(raw, 2024))
        self.assertTrue(context.hours_since_previous_listed_game.isna().all())
        self.assertEqual(context.utc_calendar_days_since_previous_listed_game.dropna().tolist(), [1, 1])


if __name__ == "__main__":
    unittest.main()
