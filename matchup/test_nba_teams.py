"""Synthetic team profiles and temporal invariance; no source data or files."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_teams as nba


def fixtures(dates=None):
    dates = dates or ["2023-01-01T20:00:00Z", "2023-01-03T20:00:00Z", "2023-01-05T20:00:00Z"]
    rows = []
    for game, date in enumerate(dates):
        for team in [1, 2]:
            rows.append({"game_id": str(game + 1), "team_id": str(team), "opponent_id": str(3 - team), "game_date": date,
                "season": "2023", "season_type": "2", "is_home": str(team == 1), "team_score": "100" if team == 1 else "97",
                "opponent_score": "97" if team == 1 else "100", "field_goals_made": "40" if team == 1 else "38",
                "field_goals_attempted": str(90 + game), "threes": "10" if team == 1 else "9", "threes_attempted": str(30 + game),
                "free_throws_made": "10" if team == 1 else "12", "free_throws_attempted": "15", "offensive_rebounds": "10",
                "defensive_rebounds": "30", "rebounds": "40", "assists": "25", "steals": "5", "blocks": "4",
                "turnovers": "10", "total_turnovers": "12"})
    return pd.DataFrame(rows)


class NbaTeamTests(unittest.TestCase):
    def test_box_style_formula_and_turnover_source(self):
        profile = nba.profiles(fixtures())
        first = profile.iloc[0]
        self.assertAlmostEqual(first.out_three_attempt_share, 1 / 3)
        self.assertAlmostEqual(first.out_effective_field_goal_pct, 0.5)
        self.assertAlmostEqual(first.out_offensive_rebound_share, 0.25)
        self.assertAlmostEqual(first.out_defensive_rebound_share, 0.75)
        self.assertAlmostEqual(first.out_possession_proxy_per_game, 90 + 0.44 * 15 - 10 + 12)
        self.assertEqual(first.turnover_source_basis, "total_turnovers")

    def test_current_and_future_changes_leave_current_pregame_features_unchanged(self):
        source = fixtures()
        before = nba.pregame_features(nba.profiles(source))
        changed = source.copy()
        changed.loc[changed.game_id.isin(["2", "3"]), "field_goals_attempted"] = "120"
        changed.loc[changed.game_id.isin(["2", "3"]), "threes_attempted"] = "60"
        after = nba.pregame_features(nba.profiles(changed))
        pd.testing.assert_frame_equal(before.loc[before.game_id.isin(["1", "2"])].reset_index(drop=True), after.loc[after.game_id.isin(["1", "2"])].reset_index(drop=True))
        later = before.game_id.eq("3")
        self.assertFalse(before.loc[later, "pre_three_attempt_share_mean_last5"].equals(after.loc[later, "pre_three_attempt_share_mean_last5"]))

    def test_same_date_and_incomplete_previous_night_are_excluded(self):
        source = fixtures(["2023-01-01T01:00:00Z", "2023-01-01T20:00:00Z", "2023-01-02T00:00:00Z"])
        feature = nba.pregame_features(nba.profiles(source))
        self.assertTrue(feature.loc[feature.game_id.eq("2"), "pre_eligible_history_games"].eq(0).all())
        latest = feature.loc[feature.game_id.eq("3")]
        self.assertTrue(latest.pre_eligible_history_games.eq(1).all())
        self.assertTrue(latest.pre_history_latest_game_start_utc.eq(pd.Timestamp("2023-01-01T01:00:00Z")).all())

    def test_quality_exclusions_do_not_erase_previous_listed_game(self):
        profile = nba.profiles(fixtures(), flagged_games=["2"])
        feature = nba.pregame_features(profile)
        current = feature.loc[feature.game_id.eq("3")]
        self.assertTrue(current.pre_eligible_history_games.eq(1).all())
        self.assertTrue(current.pre_hours_since_previous_listed_game.eq(48).all())
        self.assertTrue(nba.labels(profile).loc[profile.game_id.eq("2"), "label_team_win"].isna().all())

    def test_windows_reset_each_season(self):
        source = fixtures()
        source.loc[source.game_id.eq("3"), "season"] = "2024"
        feature = nba.pregame_features(nba.profiles(source))
        self.assertTrue(feature.loc[feature.game_id.eq("3"), "pre_eligible_history_games"].eq(0).all())

    def test_missing_stats_stay_unknown_and_get_sample_counts(self):
        source = fixtures()
        source.loc[source.game_id.eq("1"), "field_goals_attempted"] = pd.NA
        feature = nba.pregame_features(nba.profiles(source))
        second = feature.loc[feature.game_id.eq("2")]
        self.assertTrue(second.pre_eligible_history_games.eq(1).all())
        self.assertTrue(second.pre_three_attempt_share_mean_last5.isna().all())
        self.assertTrue(second.pre_three_attempt_share_observed_last5.eq(0).all())

    def test_opponent_profiles_and_targets_stay_separate(self):
        profile = nba.profiles(fixtures())
        feature = nba.pregame_features(profile)
        pairs = nba.matchups(feature)
        target = nba.labels(profile)
        self.assertFalse(any(name.startswith(("out_", "label_")) for name in feature))
        self.assertFalse(any(name.startswith(("out_", "label_")) for name in pairs))
        self.assertTrue(target.loc[target.team_id.eq("1"), "label_team_win"].all())
        self.assertTrue(target.loc[target.team_id.eq("2"), "label_team_win"].eq(False).all())
        self.assertAlmostEqual(pairs.loc[pairs.game_id.eq("2"), "difference_home_minus_away_pre_effective_field_goal_pct_mean_last5"].iloc[0], (40 + 5) / 90 - (38 + 4.5) / 90)
        self.assertAlmostEqual(pairs.loc[pairs.game_id.eq("2"), "interaction_home_three_attempt_share_times_opponent_allowed_last5"].iloc[0], 1 / 9)
        self.assertTrue(pairs.loc[pairs.game_id.eq("1"), "interaction_home_three_attempt_share_times_opponent_allowed_last5"].isna().all())

    def test_one_invalid_box_disqualifies_both_team_labels(self):
        source = fixtures()
        source.loc[source.game_id.eq("1") & source.team_id.eq("1"), "threes"] = "999"
        target = nba.labels(nba.profiles(source))
        self.assertTrue(target.loc[target.game_id.eq("1"), "label_team_win"].isna().all())

    def test_inconsistent_opponent_scores_invalidate_history(self):
        source = fixtures()
        source.loc[(source.game_id.eq("1")) & source.team_id.eq("1"), "opponent_score"] = "99"
        profile = nba.profiles(source)
        self.assertFalse(profile.loc[profile.game_id.eq("1"), "style_history_eligible"].any())
        feature = nba.pregame_features(profile)
        self.assertTrue(feature.loc[feature.game_id.eq("2"), "pre_eligible_history_games"].eq(0).all())

    def test_calendar_splits_not_season_year(self):
        source = fixtures(["2023-12-29T22:00:00Z", "2024-12-29T22:00:00Z", "2025-01-03T22:00:00Z"])
        profile = nba.profiles(source)
        self.assertEqual(profile.groupby("game_id").evaluation_split.first().to_dict(), {"1": "fit_pre_2024", "2": "calibration_2024", "3": "holdout_2025_plus"})
        feature = nba.pregame_features(profile)
        self.assertTrue(feature.loc[feature.game_id.eq("3"), "pre_eligible_history_games"].eq(2).all())

    def test_duplicate_keys_fail_closed(self):
        source = fixtures()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            nba.profiles(pd.concat([source, source.iloc[:1]], ignore_index=True))

    def test_local_execution_stops_before_network_or_writes(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(nba.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                nba.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
