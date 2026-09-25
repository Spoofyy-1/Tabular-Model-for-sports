"""Only tiny synthetic in-memory examples; no downloads or dataset files."""
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nfl_context as context


class NFLContextTests(unittest.TestCase):
    def ngs(self, **updates):
        row = {"season": 2024, "season_type": "REG", "week": 17,
               "player_gsis_id": "00-1234567", "team_abbr": "LA",
               "avg_time_to_throw": 2.5}
        row.update(updates)
        return context.normalize(pd.DataFrame([row]), "ngs_passing")

    def schedule(self):
        return context.schedule_lookup(pd.DataFrame({
            "season": [2024, 2024], "week": [17, 18], "game_type": ["REG", "REG"],
            "game_id": ["synthetic_a", "synthetic_b"],
            "gameday": ["2024-12-29", "2025-01-05"], "gametime": ["13:00", "16:25"],
            "home_team": ["LA", "LA"], "away_team": ["SEA", "SEA"],
        }), 2001, 2026)

    def test_policy_blocks_local_before_any_network_or_write(self):
        # Remove runner flags even when tests execute remotely. Never set them
        # to simulate an allowed environment or override the runtime guard.
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(context.requests, "get") as network:
                with self.assertRaises(SystemExit):
                    context.get_file("https://example.invalid", Path("not-created"), [0])
                network.assert_not_called()
            with self.assertRaises(SystemExit):
                context.write_table(pd.DataFrame(), "not-created", Path("not-created"))

    def test_missing_and_invalid_gsis_preserved_without_name_matching(self):
        for source_id in [None, "bad-id"]:
            result = self.ngs(player_gsis_id=source_id, player_display_name="Synthetic Name")
            self.assertFalse(result.loc[0, "identity_valid"])
            self.assertTrue(pd.isna(result.loc[0, "player_id"]))
            self.assertTrue(result.loc[0, "identity_exclusion_reason"])
            if source_id is not None:
                self.assertEqual(result.loc[0, "player_id_source"], source_id)

    def test_normalization_keeps_missing_measurements_and_original_team(self):
        result = self.ngs(avg_time_to_throw=None)
        self.assertEqual(result.loc[0, "team"], "LAR")
        self.assertEqual(result.loc[0, "team_source"], "LA")
        self.assertTrue(pd.isna(result.loc[0, "avg_time_to_throw"]))
        self.assertFalse(result["verified_asof"].any())
        self.assertFalse(result["pregame_feature_enabled"].any())

    def test_calendar_holdout_does_not_use_nfl_season(self):
        records = pd.concat([self.ngs(week=17), self.ngs(week=18)], ignore_index=True)
        result = context.attach_games(records, self.schedule())
        self.assertEqual(result["split_by_game_date"].tolist(),
                         ["development_through_2024", "holdout_2025_onward"])
        self.assertEqual(str(result["game_date"].dtype), "datetime64[ns, UTC]")

    def test_ngs_week_zero_cannot_match_target_game(self):
        result = context.attach_games(self.ngs(week=0), self.schedule())
        self.assertTrue(pd.isna(result.loc[0, "game_id"]))
        self.assertEqual(result.loc[0, "split_by_game_date"], "unmatched_game_date")
        for invalid_week in [None, -1]:
            with self.assertRaisesRegex(ValueError, "NGS week"):
                self.ngs(week=invalid_week)

    def test_ambiguous_schedule_join_is_rejected(self):
        schedule = self.schedule()
        with self.assertRaises(pd.errors.MergeError):
            context.attach_games(self.ngs(), pd.concat([schedule, schedule], ignore_index=True))

    def test_source_depth_timestamp_remains_unverified(self):
        result = context.normalize(pd.DataFrame({
            "gsis_id": ["00-1234567", "00-1234567"], "team": ["LA", "LA"],
            "dt": ["2025-09-01T07:00:00Z", "invalid"], "pos_slot": [1, 2], "pos_rank": [2, 1],
        }), "depth_charts", source_season=2025)
        self.assertEqual(str(result["source_snapshot_at_utc"].dtype), "datetime64[ns, UTC]")
        self.assertTrue(pd.isna(result.loc[1, "source_snapshot_at_utc"]))
        self.assertEqual(result.loc[1, "dt"], "invalid")
        self.assertFalse(result["verified_asof"].any())
        self.assertNotIn("game_id", result)
        self.assertNotIn("week", result)
        self.assertEqual(result["season"].tolist(), [2025, 2025])

    def test_weekly_conflict_fails_but_exact_duplicates_are_removed(self):
        same = self.ngs()
        result, dropped = context.prepare_table(pd.concat([same, same], ignore_index=True), "ngs_passing_weekly")
        self.assertEqual((len(result), dropped), (1, 1))
        changed = self.ngs(avg_time_to_throw=3.5)
        with self.assertRaisesRegex(ValueError, "Conflicting canonical NGS"):
            context.prepare_table(pd.concat([same, changed], ignore_index=True), "ngs_passing_weekly")

    def test_multiple_depth_roles_and_evolving_schema_survive_parquet(self):
        depth = context.normalize(pd.DataFrame({
            "season": [2024, 2024], "week": [17, 17], "game_type": ["REG", "REG"],
            "gsis_id": ["00-1234567", "00-1234567"], "club_code": ["LA", "LA"],
            "formation": ["Offense", "Special Teams"], "depth_position": ["RB", "PR"],
            "depth_team": [1, "2"],
        }), "depth_charts")
        result, dropped = context.prepare_table(depth, "depth_charts_weekly_2001_2024")
        self.assertEqual((len(result), dropped), (2, 0))
        self.assertEqual(str(result["depth_team"].dtype), "string")
        buffer = io.BytesIO()
        result.to_parquet(buffer, index=False)
        buffer.seek(0)
        self.assertEqual(len(pd.read_parquet(buffer)), 2)

    def injury(self):
        return pd.DataFrame({
            "season": [2025], "week": [1], "game_type": ["REG"],
            "gsis_id": ["00-1234567"], "team": ["LA"], "full_name": ["Synthetic Name"],
            "report_status": ["Questionable"], "practice_status": [None],
            "date_modified": ["2025-09-06T17:00:00Z"],
        })

    def injury_schedule(self):
        return context.schedule_lookup(pd.DataFrame({
            "season": [2025], "week": [1], "game_type": ["REG"], "game_id": ["synthetic_injury_game"],
            "gameday": ["2025-09-07"], "gametime": ["13:00"], "home_team": ["LA"], "away_team": ["SEA"],
        }), 2025, 2026)

    def test_injury_modification_time_is_not_verified_publication_time(self):
        result = context.normalize_injuries(self.injury(), 2025, self.injury_schedule())
        self.assertTrue(result.loc[0, "modified_before_kickoff"])
        self.assertFalse(result.loc[0, "verified_asof"])
        self.assertFalse(result.loc[0, "pregame_feature_enabled"])
        self.assertEqual(result.loc[0, "date_modified"], "2025-09-06T17:00:00Z")
        self.assertEqual(result.loc[0, "player_name"], "Synthetic Name")
        self.assertTrue(pd.isna(result.loc[0, "practice_status"]))
        self.assertEqual(result.loc[0, "split_by_game_date"], "holdout_2025_onward")

    def test_injury_missing_timestamp_or_schedule_does_not_infer_health(self):
        source = self.injury()
        source.loc[0, "date_modified"] = "invalid"
        source.loc[0, "week"] = 2
        result = context.normalize_injuries(source, 2025, self.injury_schedule())
        self.assertTrue(pd.isna(result.loc[0, "game_id"]))
        self.assertTrue(pd.isna(result.loc[0, "source_snapshot_at_utc"]))
        self.assertFalse(result.loc[0, "modified_before_kickoff"])
        self.assertEqual(result.loc[0, "report_status"], "Questionable")

    def test_injury_schema_empty_and_source_season_safeguards(self):
        for source in [self.injury().drop(columns=["gsis_id"]), self.injury().iloc[:0]]:
            with self.assertRaises(ValueError):
                context.normalize_injuries(source, 2025, self.injury_schedule())
        with self.assertRaisesRegex(ValueError, "different season"):
            context.normalize_injuries(self.injury(), 2026, self.injury_schedule())

    def test_new_injury_export_omits_timestamp_without_substitution(self):
        source = self.injury().drop(columns=["date_modified"])
        result = context.normalize_injuries(source, 2025, self.injury_schedule())
        self.assertFalse(result.loc[0, "source_modification_column_present"])
        self.assertTrue(pd.isna(result.loc[0, "date_modified"]))
        self.assertTrue(pd.isna(result.loc[0, "source_snapshot_at_utc"]))
        self.assertEqual(str(result["source_snapshot_at_utc"].dtype), "datetime64[ns, UTC]")
        self.assertEqual(result.loc[0, "modification_timestamp_status"], "source_column_not_provided")
        self.assertFalse(result.loc[0, "modified_before_kickoff"])
        self.assertFalse(result.loc[0, "verified_asof"])
        self.assertEqual(result.loc[0, "report_status"], "Questionable")


if __name__ == "__main__":
    unittest.main()
