"""Synthetic frames only; no network, real records or data files."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd

spec = importlib.util.spec_from_file_location("weather_nfl", Path(__file__).with_name("nfl.py"))
nfl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nfl)


def fixture():
    schedules = pd.DataFrame([
        {"game_id": "g1", "home_team": "AA", "away_team": "BB", "game_date": "2024-12-30T18:00:00Z", "season": "2024", "game_date_time_known": True, "temp": "32", "wind": "10", "roof": "outdoors", "surface": "grass"},
        {"game_id": "g2", "home_team": "BB", "away_team": "AA", "game_date": "2025-01-01T18:00:00Z", "season": "2024", "game_date_time_known": True, "temp": None, "wind": None, "roof": "dome", "surface": "turf"}])
    players = pd.DataFrame([
        {"game_id": "g1", "player_id": "00-0000001", "team": "AA", "opponent": "BB", "game_date": "2024-12-30T18:00:00Z", "season": "2024", "position": "QB", "passing_yards": "100", "attempts": "10"},
        {"game_id": "g1", "player_id": "00-0000002", "team": "BB", "opponent": "AA", "game_date": "2024-12-30T18:00:00Z", "season": "2024", "position": "QB", "passing_yards": "0", "attempts": "0"},
        {"game_id": "g2", "player_id": "00-0000001", "team": "AA", "opponent": "BB", "game_date": "2025-01-01T18:00:00Z", "season": "2024", "position": "QB", "passing_yards": "200", "attempts": "20"}])
    return players, schedules


class WeatherTests(unittest.TestCase):
    def test_units_missingness_roof_and_split(self):
        out, excluded = nfl.panel(*fixture())
        self.assertEqual(len(excluded), 0)
        self.assertAlmostEqual(out.wind_kmh.iloc[0], 16.09344)
        self.assertEqual(out.temperature_celsius_assuming_fahrenheit.iloc[0], 0)
        self.assertFalse(out.temperature_unit_independently_verified.any())
        self.assertTrue(pd.isna(out.wind_mph.iloc[-1]))
        self.assertEqual(out.wind_bucket.iloc[-1], "not_applicable_to_roof_category")
        self.assertEqual(out.evaluation_split.iloc[-1], "holdout_2025_onward")
        self.assertEqual(out.player_id.iloc[0], "00-0000001")

    def test_bad_opponent_excluded_no_many_to_many(self):
        p, s = fixture()
        p.loc[1, "opponent"] = "CC"
        out, excluded = nfl.panel(p, s)
        self.assertEqual(len(out), 2)
        self.assertEqual(len(excluded), 1)

    def test_date_and_season_mismatches_excluded(self):
        p, s = fixture()
        p.loc[1, "game_date"] = "2024-12-31T18:00:00Z"
        p.loc[2, "season"] = "2025"
        out, excluded = nfl.panel(p, s)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(excluded), 2)

    def test_duplicates_rejected(self):
        p, s = fixture()
        with self.assertRaises(ValueError):
            nfl.panel(pd.concat([p, p.iloc[:1]], ignore_index=True), s)
        with self.assertRaises(ValueError):
            nfl.panel(p, pd.concat([s, s.iloc[:1]], ignore_index=True))

    def test_unknown_date_boundary_unresolved(self):
        p, s = fixture()
        s.loc[1, "game_date_time_known"] = False
        out, _ = nfl.panel(p, s)
        self.assertEqual(out.evaluation_split.iloc[-1], "date_precision_unresolved")

    def test_invalid_weather_not_zero_filled(self):
        p, s = fixture()
        s.loc[0, "wind"] = "-1"
        s.loc[0, "temp"] = "999"
        out, _ = nfl.panel(p, s)
        self.assertTrue(out.wind_value_invalid.iloc[0])
        self.assertTrue(pd.isna(out.wind_mph.iloc[0]))
        self.assertEqual(out.wind_bucket.iloc[0], "unknown")
        self.assertTrue(pd.isna(out.temperature_fahrenheit_assumed.iloc[0]))

    def test_retractable_unknown_not_open_air(self):
        p, s = fixture()
        s.loc[0, "roof"] = "retractable"
        out, _ = nfl.panel(p, s)
        self.assertEqual(out.weather_exposure.iloc[0], "retractable_status_unknown")
        self.assertFalse(out.weather_exposure_open_air.iloc[0])

    def test_denominators_include_known_zero_only_volume_positive(self):
        out, _ = nfl.panel(*fixture())
        stats = nfl.aggregates(out)
        row = stats.loc[stats.evaluation_split.eq("development_through_2024") & stats.metric.eq("passing_yards")].iloc[0]
        self.assertEqual(row.player_game_rows, 2)
        self.assertEqual(row.distinct_games, 1)
        self.assertEqual(row.nonmissing_outcome_rows, 2)
        self.assertEqual(row.outcome_mean, 50)
        self.assertEqual(row.paired_positive_opportunity_rows, 1)
        self.assertEqual(row.outcome_per_positive_opportunity, 10)

    def test_missing_metrics_never_become_zero(self):
        out, _ = nfl.panel(*fixture())
        stats = nfl.aggregates(out)
        missing = stats.loc[stats.metric.eq("receiving_yards")]
        self.assertTrue(missing.outcome_mean.isna().all())
        self.assertTrue(missing.outcome_sum.isna().all())
        self.assertTrue(missing.nonmissing_outcome_rows.eq(0).all())

    def test_holdout_changes_do_not_alter_development_aggregate(self):
        p, s = fixture()
        a = nfl.aggregates(nfl.panel(p, s)[0])
        p.loc[2, "passing_yards"] = "9999"
        b = nfl.aggregates(nfl.panel(p, s)[0])
        from pandas.testing import assert_frame_equal
        assert_frame_equal(a.loc[a.evaluation_split.eq("development_through_2024")].reset_index(drop=True), b.loc[b.evaluation_split.eq("development_through_2024")].reset_index(drop=True))

    def test_local_main_refuses_before_io(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                nfl.main()


if __name__ == "__main__":
    unittest.main()
