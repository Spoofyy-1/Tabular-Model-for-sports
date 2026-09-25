"""Synthetic tests only: no data files, HTTP calls or real sports records."""
import unittest
import pandas as pd
from pandas.testing import assert_frame_equal
from collect import normalize, features, opponent_comparisons, main


def fixture(dates, coaches=None):
    rows = []
    for i, date in enumerate(dates):
        for team, other, home in [("001", "002", True), ("002", "001", False)]:
            rows.append({"game_id": f"g{i}", "team": team, "opponent": other, "game_date": date,
                "season": "2024", "is_home": home, "source_game_date_time_known": True,
                "source_coach_name": coaches[i] if coaches else "Coach A", "source_stadium": "Place",
                "source_stadium_id": "01", "source_roof": "outdoors", "source_surface": "grass"})
    return pd.DataFrame(rows)


class ScheduleTests(unittest.TestCase):
    def test_exact_window_and_strict_current_exclusion(self):
        raw = fixture(["2024-01-01T12:00:00Z", "2024-01-04T12:00:00Z", "2024-01-07T12:00:01Z"])
        frame, _ = normalize(raw, "nfl")
        out = features(frame).query("team == '001'")
        self.assertEqual(out.pre_listed_starts_previous_72h.tolist(), [0, 1, 0])
        self.assertEqual(out.pre_listed_starts_previous_168h.tolist(), [0, 1, 2])
        self.assertEqual(out.pre_hours_since_previous_listed_start.iloc[1], 72)

    def test_future_and_scores_do_not_change_prior_features(self):
        first = fixture(["2024-01-01T12:00Z", "2024-01-04T12:00Z"])
        later = fixture(["2024-01-01T12:00Z", "2024-01-04T12:00Z", "2025-01-01T12:00Z"])
        later["out_team_score"] = [999, 888, 3, 2, -5, 6]
        a = features(normalize(first, "nfl")[0])
        b = features(normalize(later, "nfl")[0]).query("game_id != 'g2'").reset_index(drop=True)
        assert_frame_equal(a, b)
        self.assertFalse(any(c.startswith(("out_", "label_", "observed_")) for c in b))

    def test_same_start_conflicts_excluded_from_later_history(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-01T12:00Z", "2024-01-02T12:00Z"])
        clean, excluded = normalize(raw, "nfl")
        self.assertEqual(len(excluded), 4)
        self.assertEqual(features(clean).pre_listed_starts_previous_72h.tolist(), [0, 0])

    def test_duplicate_game_team_rejected(self):
        raw = fixture(["2024-01-01T12:00Z"])
        with self.assertRaises(ValueError):
            normalize(pd.concat([raw, raw.iloc[:1]], ignore_index=True), "nfl")

    def test_same_start_different_season_labels_remains_ambiguous(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-01T12:00Z"])
        raw.loc[raw.game_id.eq("g1"), "season"] = "2025"
        clean, excluded = normalize(raw, "nfl")
        self.assertEqual(len(clean), 0)
        self.assertEqual(len(excluded), 4)

    def test_unknown_timestamp_excludes_both_sides(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-02T12:00Z"])
        raw.loc[0, "source_game_date_time_known"] = False
        clean, excluded = normalize(raw, "nfl")
        self.assertEqual(len(excluded), 2)
        self.assertEqual(set(clean.game_id), {"g1"})

    def test_unknown_home_remains_unknown_in_audit(self):
        raw = fixture(["2024-01-01T12:00Z"])
        raw["is_home"] = raw.is_home.astype("boolean")
        raw.loc[0, "is_home"] = None
        clean, excluded = normalize(raw, "nfl")
        self.assertEqual(len(clean), 0)
        self.assertTrue(pd.isna(excluded.is_home.iloc[0]))

    def test_missing_coach_breaks_observed_run(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-03T12:00Z", "2024-01-05T12:00Z", "2024-01-07T12:00Z"], ["Coach A", None, "Coach A", "Coach B"])
        out = features(normalize(raw, "nfl")[0]).query("team == '001'")
        self.assertEqual(out.audit_prior_consecutive_listed_games_same_coach_name.iloc[2], 0)
        self.assertTrue(pd.isna(out.audit_coach_name_changed_from_previous.iloc[2]))
        self.assertEqual(out.audit_coach_name_changed_from_previous.iloc[3], True)

    def test_renamed_stadium_is_not_changed_stadium_id(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-02T12:00Z"])
        raw.loc[raw.game_id.eq("g1"), "source_stadium"] = "Renamed Place"
        out = features(normalize(raw, "nfl")[0]).query("game_id == 'g1'")
        self.assertTrue(out.audit_stadium_changed_from_previous.all())
        self.assertFalse(out.audit_stadium_id_changed_from_previous.any())

    def test_season_reset_and_first_history_censoring(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-02T12:00Z"])
        raw.loc[raw.game_id.eq("g1"), "season"] = "2025"
        out = features(normalize(raw, "nfl")[0])
        self.assertEqual(out.pre_prior_listed_games_in_source_season.sum(), 0)
        self.assertTrue(out.pre_72h_window_reaches_first_listed_season_start.all())

    def test_nba_identity_independent_of_score_quality(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-02T12:00Z"]).rename(columns={"team": "team_id", "opponent": "opponent_id"})
        raw["source_game_date_precision"] = "source_timestamp"
        raw["season_type"] = 2
        raw["quality_pair_valid"] = False
        raw["profile_identity_valid"] = False
        raw["out_team_score"] = -999
        clean, excluded = normalize(raw, "nba")
        self.assertEqual(len(clean), 4)
        self.assertEqual(len(excluded), 0)

    def test_pair_mismatch_quarantines_whole_game(self):
        raw = fixture(["2024-01-01T12:00Z"])
        raw.loc[0, "opponent"] = "003"
        clean, excluded = normalize(raw, "nfl")
        self.assertEqual(len(clean), 0)
        self.assertEqual(len(excluded), 2)

    def test_opponent_counts_exact_and_identifiers_preserved(self):
        raw = fixture(["2024-01-01T12:00Z", "2024-01-03T12:00Z"])
        out = opponent_comparisons(features(normalize(raw, "nfl")[0]))
        self.assertEqual(set(out.team), {"001", "002"})
        self.assertEqual(out.difference_team_minus_opponent_pre_listed_starts_previous_72h.sum(), 0)

    def test_calendar_2024_split(self):
        raw = fixture(["2023-12-31T23:59:59Z", "2024-12-31T23:59:59Z", "2025-01-01T00:00:00Z"])
        out = normalize(raw, "nfl")[0].query("team == '001'")
        self.assertEqual(out.evaluation_split.tolist(), ["fit_pre_2024", "calibration_2024", "holdout_2025_plus"])

    def test_cloud_guard_rejects_local_main(self):
        # This test runs only locally; hosted CI validates collection itself.
        import os
        if os.environ.get("GITHUB_ACTIONS") != "true":
            with self.assertRaises(RuntimeError):
                main()


if __name__ == "__main__":
    unittest.main()
