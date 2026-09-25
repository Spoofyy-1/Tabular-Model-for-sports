"""Synthetic tennis scoring and leakage tests; no source data, PDFs or files."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import point_data as tennis


def match_metadata(retired=False):
    source = pd.DataFrame({"match_id": ["synthetic"], "Player 1": ["Example One"], "Player 2": ["Example Two"], "Date": ["20231201"], "Surface": ["Hard"], "Best of": ["3"], "Score": ["RET" if retired else "6-0 6-0"]})
    return tennis.metadata(source, "mens_singles")[0]


def completed_match():
    rows = []
    for game in range(12):
        for point in range(4):
            rows.append({"match_id": "synthetic", "Pt": str(4*game+point+1), "Set1": str(game//6), "Set2": "0", "Gm1": str(game%6), "Gm2": "0", "Gm#": f"{game+1} ({point+1})", "TB?": "0", "Svr": str(game%2+1), "PtWinner": "1", "1stIn": "1", "2ndIn": "", "isAce": "FALSE", "isDouble": "FALSE", "isSvrWinner": str(int(game%2==0))})
    return pd.DataFrame(rows)


class TennisPoints(unittest.TestCase):
    def test_date_surface_names_and_source_initial_server_contract(self):
        metadata = match_metadata()
        self.assertEqual(metadata.match_date.iloc[0], pd.Timestamp("2023-12-01"))
        self.assertEqual(metadata.surface.iloc[0], "Hard")
        self.assertTrue(metadata.singles_metadata_eligible.iloc[0])

    def test_break_point_uses_previous_points_and_current_server(self):
        raw = completed_match().iloc[:4].copy()
        raw["PtWinner"] = ["2", "2", "2", "1"]
        raw["isSvrWinner"] = ["0", "0", "0", "1"]
        states = tennis.point_states(raw, match_metadata())
        self.assertEqual(states.pre_break_point.tolist(), [False, False, False, True])
        self.assertEqual(states.pre_returner_points_in_game.tolist(), [0, 1, 2, 3])
        self.assertTrue(states.label_server_won_point.iloc[-1])

    def test_current_and_future_outcome_changes_leave_prior_states_unchanged(self):
        raw = completed_match()
        before = tennis.point_states(raw, match_metadata())
        changed = raw.copy()
        changed.loc[changed.Pt.astype(int).ge(6), "PtWinner"] = "2"
        after = tennis.point_states(changed, match_metadata())
        columns = [name for name in before if name.startswith("pre_")]
        pd.testing.assert_frame_equal(before.loc[before.source_point_number.le(6), columns], after.loc[after.source_point_number.le(6), columns])

    def test_gap_invalidates_only_missing_point_and_later_prefix(self):
        raw = completed_match().iloc[:6].drop(index=2)
        states = tennis.point_states(raw, match_metadata())
        self.assertEqual(states.pre_score_prefix_valid.tolist(), [True, True, False, False, False])
        self.assertTrue(states.pre_p1_points_in_game.iloc[2:].isna().all())
        self.assertFalse(tennis.early_labels(states).label_early_deficit_eligible.iloc[0])

    def test_points_after_completed_standard_game_require_new_game_id(self):
        raw = completed_match().iloc[:5].copy()
        raw.loc[raw.index[-1], "Gm#"] = "1 (5)"
        states = tennis.point_states(raw, match_metadata())
        self.assertEqual(states.pre_score_prefix_valid.tolist(), [True, True, True, True, False])

    def test_game_id_cannot_advance_before_previous_game_finishes(self):
        raw = completed_match().iloc[:8].copy()
        raw.loc[3, "PtWinner"] = "2"
        states = tennis.point_states(raw, match_metadata())
        self.assertTrue(states.pre_score_prefix_valid.iloc[:4].all())
        self.assertFalse(states.pre_score_prefix_valid.iloc[4:].any())

    def test_source_games_and_sets_cannot_jump(self):
        for field, index in [("Gm1", 4), ("Set1", 24)]:
            with self.subTest(field=field):
                raw = completed_match()
                raw.loc[index:, field] = raw.loc[index:, field].astype(int).add(1).astype(str)
                states = tennis.point_states(raw, match_metadata())
                self.assertTrue(states.pre_score_prefix_valid.iloc[:index].all())
                self.assertFalse(states.pre_score_prefix_valid.iloc[index:].any())
                self.assertFalse(tennis.early_labels(states).label_early_deficit_eligible.iloc[0])

    def test_game_counters_are_global_and_optional_point_suffix_is_checked(self):
        raw = completed_match()
        plain = raw.copy()
        plain["Gm#"] = plain["Gm#"].str.split().str[0]
        self.assertTrue(tennis.point_states(plain, match_metadata()).pre_score_prefix_valid.all())
        raw.loc[24, "Gm#"] = "7 (2)"
        states = tennis.point_states(raw, match_metadata())
        self.assertTrue(states.pre_score_prefix_valid.iloc[:24].all())
        self.assertFalse(states.pre_score_prefix_valid.iloc[24:].any())

    def test_first_and_second_serve_outcomes_preserve_not_applicable(self):
        raw = completed_match().iloc[:4].copy()
        raw["1stIn"] = ["1", "0", "0", "unknown"]
        raw["2ndIn"] = ["", "1", "0", ""]
        states = tennis.point_states(raw, match_metadata())
        self.assertEqual(states.label_first_serve_in.iloc[:3].tolist(), [True, False, False])
        self.assertTrue(pd.isna(states.label_first_serve_in.iloc[3]))
        self.assertTrue(pd.isna(states.label_second_serve_in.iloc[0]))
        self.assertEqual(states.label_second_serve_in.iloc[1:3].tolist(), [True, False])

    def test_tiebreak_not_classified_as_break_point(self):
        raw = completed_match().iloc[:4].copy()
        raw["TB?"] = "1"
        states = tennis.point_states(raw, match_metadata())
        self.assertTrue(states.pre_break_point.isna().all())

    def test_early_deficit_requires_observed_four_games_and_finished_match(self):
        states = tennis.point_states(completed_match(), match_metadata())
        labels = tennis.early_labels(states)
        self.assertTrue(labels.label_early_deficit_eligible.iloc[0])
        self.assertFalse(labels.label_p1_trailing_after_four_games.iloc[0])
        self.assertTrue(labels.label_p2_trailing_after_four_games.iloc[0])
        self.assertEqual(labels.label_p1_games_after_four.iloc[0], 4)
        incomplete = tennis.early_labels(tennis.point_states(completed_match().iloc[:-1], match_metadata()))
        self.assertFalse(incomplete.label_early_deficit_eligible.iloc[0])
        retired = tennis.early_labels(tennis.point_states(completed_match(), match_metadata(retired=True)))
        self.assertFalse(retired.label_early_deficit_eligible.iloc[0])

    def test_descriptive_counts_exclude_2025_holdout(self):
        states = tennis.point_states(completed_match(), match_metadata())
        counts = tennis.count_player_context(states)
        self.assertEqual(int(counts.serve_points.sum()), 48)
        states["match_date"] = pd.Timestamp("2025-01-02")
        self.assertTrue(tennis.count_player_context(states).empty)
        states["match_date"] = pd.Timestamp("2024-12-31")
        self.assertTrue(tennis.count_player_context(states).empty)

    def test_player_summary_preserves_serve_order_and_sample_threshold(self):
        states = tennis.point_states(completed_match(), match_metadata())
        summary = tennis.player_summary([tennis.count_player_context(states)], tennis.early_labels(states))
        one = summary.set_index("player_name").loc["Example One"]
        two = summary.set_index("player_name").loc["Example Two"]
        self.assertEqual(one.early_served_first_matches, 1)
        self.assertEqual(two.early_returned_first_trailing_matches, 1)
        self.assertFalse(summary.clutch_screen_eligible.any())
        low, high = tennis.wilson(50, 100)
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)

    def test_player_summary_keeps_competition_groups_separate(self):
        men = tennis.point_states(completed_match(), match_metadata())
        women = men.copy()
        women["competition_group"] = "womens_singles"
        women["match_id"] = "synthetic-women"
        parts = [tennis.count_player_context(frame) for frame in [men, women]]
        labels = pd.concat([tennis.early_labels(frame) for frame in [men, women]], ignore_index=True)
        summary = tennis.player_summary(parts, labels)
        self.assertEqual(len(summary), 4)
        self.assertTrue(summary.charted_matches.eq(1).all())
        self.assertEqual(summary.groupby("competition_group").serve_points.sum().to_dict(), {"mens_singles": 48, "womens_singles": 48})

    def test_local_main_rejected_before_network_or_storage(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(tennis.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                tennis.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
