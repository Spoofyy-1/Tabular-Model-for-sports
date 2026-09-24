"""Behavioral checks for the principal failure mode: future leakage."""
import sys
import unittest
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from features import build_features


def fixture():
    rows = []
    for day in range(1, 16):
        for player, team, opponent in [("p1", "A", "B"), ("p2", "B", "A")]:
            rows.append({"sport": "NBA", "game_id": str(day), "player_id": player, "player_name": player,
                         "team": team, "opponent": opponent, "game_date": "2022-01-%02d" % day,
                         "season": 2022, "is_home": team == "A", "position": "G", "points": day + (team == "A"),
                         "rebounds": day % 7, "assists": day % 5, "minutes": 30})
    return pd.DataFrame(rows)


class TemporalFeatures(unittest.TestCase):
    def test_current_and_future_outcomes_do_not_change_pregame_features(self):
        original = fixture()
        before, features, _ = build_features(original, "nba")
        changed = original.copy()
        changed.loc[pd.to_datetime(changed.game_date) >= pd.Timestamp("2022-01-10"), ["points", "rebounds", "assists", "minutes"]] = 9999
        after, _, _ = build_features(changed, "nba")
        keep = before.game_date <= pd.Timestamp("2022-01-10", tz="UTC")
        pd.testing.assert_frame_equal(before.loc[keep, features], after.loc[keep, features])

    def test_prior_outcome_updates_later_features(self):
        original = fixture()
        a, _, _ = build_features(original, "nba")
        changed = original.copy()
        changed.loc[(changed.player_id == "p1") & (changed.game_id == "9"), "points"] = 100
        b, _, _ = build_features(changed, "nba")
        row = (a.player_id == "p1") & (a.game_id == "10")
        self.assertNotEqual(float(a.loc[row, "f_points_lag1"].iloc[0]), float(b.loc[row, "f_points_lag1"].iloc[0]))

    def test_appending_future_rows_preserves_existing_features(self):
        frame = fixture()
        a, columns, _ = build_features(frame[pd.to_datetime(frame.game_date) <= "2022-01-10"], "nba")
        b, _, _ = build_features(frame, "nba")
        b = b[b.game_date <= pd.Timestamp("2022-01-10", tz="UTC")].reset_index(drop=True)
        pd.testing.assert_frame_equal(a[columns], b[columns])

    def test_duplicate_keys_rejected(self):
        f = fixture()
        with self.assertRaises(ValueError):
            build_features(pd.concat([f, f.iloc[:1]]), "nba")

    def test_nfl_current_role_is_not_a_current_feature_or_cohort_label(self):
        f = fixture().rename(columns={"points": "passing_yards"})
        f["position"] = "QB"
        a, columns, _ = build_features(f, "nfl")
        f.loc[f.game_id == "10", "position"] = "RB"
        b, _, _ = build_features(f, "nfl")
        current = a.game_id == "10"
        pd.testing.assert_frame_equal(a.loc[current, columns], b.loc[current, columns])
        self.assertTrue((b.loc[current, "position"] == "QB").all())


if __name__ == "__main__":
    unittest.main()
