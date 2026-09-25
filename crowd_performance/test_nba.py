"""Synthetic in-memory fixtures only; never source downloads or data writes."""
import gzip
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from crowd_performance import nba


def raw_schedule(game="101", date="2024-06-01T19:00:00Z", attendance=18000, capacity=19000):
    return {"game_id": game, "game_date_time": date, "game_date": date[:10], "home_id": "11", "away_id": "22",
            "venue_id": "33", "season_type": 2, "attendance": attendance, "venue_capacity": capacity,
            "status_type_completed": True, "neutral_site": False}


def base_game(game="101", date="2024-06-01T19:00:00Z", season=2024):
    return {"game_id": game, "game_date": date, "game_local_date": date[:10], "home_team_id": "11", "away_team_id": "22", "season": str(season), "season_type": "2"}


def player(game="101", person="1", date="2024-06-01T19:00:00Z", season=2024, **changes):
    row = {"game_id": game, "player_id": person, "team_id": "11", "opponent_id": "22", "player_name": "Synthetic Player",
           "game_date": date, "game_local_date": date[:10], "season": str(season), "season_type": "2", "is_home": "True",
           "did_not_play": "False", "boxscore_observed": "True", **{key: "0" for key in nba.STATS}}
    row.update(points="8", rebounds="2", assists="1", minutes="4")
    row.update(changes)
    return row


def game_context(raw=None, games=None, season=2024):
    schedule = nba.normalize_schedule(pd.DataFrame(raw or [raw_schedule()]), season)
    return nba.validate_game_links(schedule, pd.DataFrame(games or [base_game()]))[0]


class NBAAttendance(unittest.TestCase):
    def test_clean_identity_and_flags(self):
        joined, excluded = nba.join_players(pd.DataFrame([player()]), game_context())
        self.assertEqual(len(joined), 1)
        self.assertTrue(excluded.empty)
        row = joined.iloc[0]
        self.assertTrue(row.descriptive_outcome_eligible)
        self.assertFalse(row.historical_capacity_validity_verified)
        self.assertFalse(row.automatic_training_join_allowed)
        self.assertEqual(row.evaluation_split, "calibration_2024")

    def test_duplicate_identical_schedule_quarantines_whole_game(self):
        context = game_context([raw_schedule(), raw_schedule()])
        self.assertFalse(context.canonical_game_join_valid.any())
        joined, excluded = nba.join_players(pd.DataFrame([player(), player(person="2")]), context)
        self.assertTrue(joined.empty)
        self.assertEqual(len(excluded), 2)

    def test_conflicting_schedule_and_duplicate_base_are_not_first_wins(self):
        conflicting = raw_schedule()
        conflicting["attendance"] = 9999
        context = game_context([raw_schedule(), conflicting])
        self.assertFalse(context.canonical_game_join_valid.any())
        context = game_context(games=[base_game(), base_game()])
        self.assertFalse(context.canonical_game_join_valid.any())

    def test_same_game_id_different_time_or_opponent_fails(self):
        for key, value in [("game_date", "2024-06-01T20:00:00Z"), ("home_team_id", "44"), ("season_type", "3")]:
            base = base_game()
            base[key] = value
            self.assertFalse(game_context(games=[base]).canonical_game_join_valid.any())

    def test_date_only_schedule_is_unresolved(self):
        context = game_context([raw_schedule(date="2024-06-01")])
        self.assertFalse(context.canonical_game_join_valid.any())
        self.assertIn("precision", context.exclusion_reasons.iloc[0])

    def test_missing_schedule_does_not_create_attendance(self):
        joined, excluded = nba.join_players(pd.DataFrame([player(game="999")]), game_context())
        self.assertTrue(joined.empty)
        self.assertIn("no_validated_schedule", excluded.exclusion_reasons.iloc[0])

    def test_player_metadata_conflict_quarantines_entire_game(self):
        rows = [player(), player(person="2", team_id="44")]
        joined, excluded = nba.join_players(pd.DataFrame(rows), game_context())
        self.assertTrue(joined.empty)
        self.assertEqual(len(excluded), 2)
        self.assertTrue(excluded.exclusion_reasons.str.contains("whole_game").all())

    def test_duplicate_player_id_quarantines_entire_game(self):
        joined, excluded = nba.join_players(pd.DataFrame([player(), player(points="10"), player(person="2")]), game_context())
        self.assertTrue(joined.empty)
        self.assertEqual(len(excluded), 3)

    def test_away_player_opponents_checked(self):
        away = player(team_id="22", opponent_id="11", is_home="False")
        joined, _ = nba.join_players(pd.DataFrame([away]), game_context())
        self.assertEqual(len(joined), 1)
        wrong = player(team_id="22", opponent_id="11", is_home="True")
        joined, _ = nba.join_players(pd.DataFrame([wrong]), game_context())
        self.assertTrue(joined.empty)

    def test_dnp_and_unknown_participation_are_not_zero_outcomes(self):
        dnp = player(did_not_play="True", boxscore_observed="False", **{key: None for key in nba.STATS})
        unknown = player(person="2", did_not_play=None, boxscore_observed=None)
        joined, _ = nba.join_players(pd.DataFrame([dnp, unknown]), game_context())
        self.assertFalse(joined.descriptive_outcome_eligible.any())
        self.assertEqual(set(joined.participation_status), {"explicit_dnp", "unknown_source_participation"})
        summary = nba.descriptive_summary(joined)
        self.assertEqual(summary.points_nonmissing_games.sum(), 0)
        self.assertTrue(summary.points_mean.isna().all())
        self.assertTrue(summary.points_sum.isna().all())

    def test_missing_and_zero_minutes_do_not_imply_dnp(self):
        dates = ["2024-06-01T19:00:00Z", "2024-06-02T19:00:00Z", "2024-06-03T19:00:00Z"]
        games = ["101", "102", "103"]
        context = game_context([raw_schedule(g, d) for g, d in zip(games, dates)], [base_game(g, d) for g, d in zip(games, dates)])
        rows = [player("101", date=dates[0], points="8", rebounds=None, minutes="0"),
                player("102", date=dates[1], points="12", rebounds="4", minutes=None),
                player("103", date=dates[2], did_not_play="True", boxscore_observed="False", **{key: None for key in nba.STATS})]
        joined, _ = nba.join_players(pd.DataFrame(rows), context)
        summary = nba.descriptive_summary(joined).iloc[0]
        self.assertEqual(summary.source_listed_player_games, 3)
        self.assertEqual(summary.reported_played_games, 2)
        self.assertEqual(summary.explicit_dnp_games, 1)
        self.assertEqual(summary.points_nonmissing_games, 2)
        self.assertEqual(summary.points_mean, 10)
        self.assertEqual(summary.rebounds_nonmissing_games, 1)
        self.assertEqual(summary.rebounds_missing_eligible_games, 1)
        self.assertEqual(summary.minutes_nonmissing_games, 1)
        self.assertEqual(summary.reported_played_with_zero_minutes, 1)
        self.assertEqual(summary.reported_played_with_missing_minutes, 1)

    def test_reconciliation_exclusion_preserves_context_but_not_means(self):
        joined, _ = nba.join_players(pd.DataFrame([player()]), game_context(), ["101"])
        self.assertEqual(len(joined), 1)
        self.assertFalse(joined.descriptive_outcome_eligible.any())
        summary = nba.descriptive_summary(joined).iloc[0]
        self.assertEqual(summary.source_quality_excluded_games, 1)
        self.assertTrue(pd.isna(summary.points_mean))

    def test_negative_or_infinite_stats_exclude_primary_cohort(self):
        for stat in ["-1", "inf"]:
            joined, _ = nba.join_players(pd.DataFrame([player(turnovers=stat)]), game_context())
            self.assertTrue(joined.invalid_numeric_outcome_present.all())
            self.assertFalse(joined.descriptive_outcome_eligible.any())

    def test_fractional_count_stats_excluded_but_overtime_minutes_valid(self):
        for key in [key for key in nba.STATS if key != "minutes"]:
            joined, _ = nba.join_players(pd.DataFrame([player(**{key: "1.5"})]), game_context())
            self.assertTrue(joined.fractional_count_outcome_present.all(), key)
            self.assertFalse(joined.descriptive_outcome_eligible.any(), key)
        joined, _ = nba.join_players(pd.DataFrame([player(minutes="50.5")]), game_context())
        self.assertTrue(joined.descriptive_outcome_eligible.all())
        self.assertFalse(joined.fractional_count_outcome_present.any())

    def test_dnp_positive_stat_and_boxscore_contradictions_flagged(self):
        joined, _ = nba.join_players(pd.DataFrame([player(did_not_play="True")]), game_context())
        self.assertTrue(joined.dnp_with_positive_stat_conflict.all())
        self.assertTrue(joined.source_boxscore_flag_conflict.all())
        self.assertFalse(joined.descriptive_outcome_eligible.any())

    def test_ratio_preserves_over_one_and_unverified_zero(self):
        context = game_context([raw_schedule(attendance=21000, capacity=20000)])
        self.assertEqual(context.reported_attendance_capacity_ratio.iloc[0], 1.05)
        self.assertTrue(context.reported_ratio_exceeds_one.iloc[0])
        context = game_context([raw_schedule(attendance=0, capacity=20000)])
        self.assertEqual(context.reported_attendance_capacity_ratio.iloc[0], 0)
        self.assertEqual(context.reported_attendance_band.iloc[0], "zero_reported_unverified")
        self.assertFalse(context.actual_occupancy_verified.iloc[0])

    def test_invalid_attendance_or_capacity_has_no_ratio(self):
        for attendance, capacity in [(None, 100), (-1, 100), (float("inf"), 100), (10, 0), (10, -1), (10, float("inf")), (10, None), (10.5, 100), (10, 100.5)]:
            context = game_context([raw_schedule(attendance=attendance, capacity=capacity)])
            self.assertTrue(pd.isna(context.reported_attendance_capacity_ratio.iloc[0]))

    def test_fractional_attendance_preserved_and_flagged(self):
        context = game_context([raw_schedule(attendance=18000.5, capacity=19000.5)])
        row = context.iloc[0]
        self.assertEqual(row.attendance_actual, 18000.5)
        self.assertEqual(row.venue_capacity_source, 19000.5)
        self.assertTrue(row.attendance_fractional_count)
        self.assertTrue(row.capacity_fractional_count)
        self.assertEqual(row.reported_attendance_band, "invalid_reported_attendance")

    def test_known_calendar_date_conflicts_quarantine_but_unknown_retained(self):
        base = base_game()
        base["game_local_date"] = "2024-06-02"
        context = game_context(games=[base])
        self.assertFalse(context.canonical_game_join_valid.any())
        self.assertIn("calendar_date_mismatch", context.exclusion_reasons.iloc[0])
        base["game_local_date"] = None
        context = game_context(games=[base])
        self.assertTrue(context.canonical_game_join_valid.all())
        self.assertFalse(context.base_schedule_calendar_date_checked.any())
        joined, excluded = nba.join_players(pd.DataFrame([player(), player(person="2", game_local_date="2024-06-02")]), game_context())
        self.assertTrue(joined.empty)
        self.assertEqual(len(excluded), 2)
        joined, _ = nba.join_players(pd.DataFrame([player(game_local_date=None)]), game_context())
        self.assertEqual(len(joined), 1)
        self.assertFalse(joined.player_schedule_calendar_date_checked.any())

    def test_calendar_dates_never_shift_timezone(self):
        dates = nba.calendar_dates(pd.Series(["2024-06-01T23:00:00-05:00", "2024-02-30", None, "2024-06-01 00:00:00"]))
        self.assertEqual(dates.iloc[0], "2024-06-01")
        self.assertTrue(pd.isna(dates.iloc[1]))
        self.assertTrue(pd.isna(dates.iloc[2]))
        self.assertEqual(dates.iloc[3], "2024-06-01")

    def test_utc_schema_is_normalization_contract_not_precision_inference(self):
        nba.validate_timestamp_schema({"columns": [{"name": "game_date", "arrow_type": "timestamp[ns, tz=UTC]"}]})
        for arrow_type in ["string", "timestamp[ns]", "date32[day]"]:
            with self.assertRaises(ValueError):
                nba.validate_timestamp_schema({"columns": [{"name": "game_date", "arrow_type": arrow_type}]})
        joined, _ = nba.join_players(pd.DataFrame([player()]), game_context())
        self.assertFalse(joined.player_timestamp_precision_independently_verified.any())

    def test_distinct_game_and_player_denominators(self):
        context = game_context([raw_schedule(), raw_schedule("102")], [base_game(), base_game("102")])
        rows = [player(), player(person="2"), player(person="3", did_not_play="True", boxscore_observed="False", **{key: None for key in nba.STATS})]
        joined, _ = nba.join_players(pd.DataFrame(rows), context)
        result = nba.attendance_denominators(context, joined).iloc[0]
        self.assertEqual(result.distinct_schedule_games, 2)
        self.assertEqual(result.games_with_validated_player_rows, 1)
        self.assertEqual(result.source_listed_player_rows, 3)
        self.assertEqual(result.eligible_player_rows, 2)
        self.assertEqual(result.explicit_dnp_player_rows, 1)
        self.assertFalse(result.player_rows_are_independent_crowd_samples)

    def test_quality_gates_require_coverage_and_eligible_development(self):
        early = "2023-06-01T19:00:00Z"
        context = game_context([raw_schedule(), raw_schedule("102", early)], [base_game(), base_game("102", early)])
        joined, excluded = nba.join_players(pd.DataFrame([player(), player("102", date=early)]), context)
        gates = nba.quality_gates(context, joined, excluded)
        self.assertTrue(gates["passed"])
        low_share = nba.quality_gates(context, joined, pd.concat([excluded, pd.DataFrame({"game_id": ["999"]})]))
        self.assertFalse(low_share["passed"])
        self.assertIn("minimum_player_join_share", low_share["failed_checks"])
        only_calibration = nba.quality_gates(context, joined.loc[joined.evaluation_split.eq("calibration_2024")], excluded)
        self.assertIn("eligible_fit_pre_2024_present", only_calibration["failed_checks"])
        empty = nba.quality_gates(context.iloc[:0], joined.iloc[:0], excluded)
        self.assertFalse(empty["passed"])
        self.assertIn("nonzero_canonical_games", empty["failed_checks"])
        self.assertIn("nonzero_eligible_player_rows", empty["failed_checks"])

    def test_empty_join_summaries_still_have_audit_schema(self):
        context = game_context()
        joined, _ = nba.join_players(pd.DataFrame([player(game="999")]), context)
        self.assertTrue(nba.descriptive_summary(joined).empty)
        denominator = nba.attendance_denominators(context, joined).iloc[0]
        self.assertEqual(denominator.distinct_schedule_games, 1)
        self.assertEqual(denominator.source_listed_player_rows, 0)

    def test_quality_gates_reject_all_unusable_attendance_without_dropping_audit(self):
        early = "2023-06-01T19:00:00Z"
        for attendance in [None, 0, -1, 18000.5, float("inf")]:
            context = game_context([raw_schedule(attendance=attendance), raw_schedule("102", early, attendance=attendance)], [base_game(), base_game("102", early)])
            joined, excluded = nba.join_players(pd.DataFrame([player(), player("102", date=early)]), context)
            gates = nba.quality_gates(context, joined, excluded)
            self.assertFalse(gates["passed"], attendance)
            self.assertIn("nonzero_eligible_positive_attendance_rows", gates["failed_checks"])
            self.assertEqual(gates["eligible_positive_attendance_player_rows"], 0)
            self.assertEqual(len(joined), 2)
            self.assertTrue(joined.descriptive_outcome_eligible.all())
            self.assertTrue(excluded.empty)

    def test_neutral_and_uncompleted_status_preserved(self):
        raw = raw_schedule()
        raw.update(neutral_site=True, status_type_completed=False)
        joined, _ = nba.join_players(pd.DataFrame([player()]), game_context([raw]))
        self.assertTrue(joined.neutral_site.all())
        self.assertFalse(joined.descriptive_outcome_eligible.any())
        self.assertIn("neutral_site", nba.descriptive_summary(joined).columns)

    def test_utc_splits_separate_fit_calibration_and_holdout(self):
        dates = ["2023-12-31T23:59:59Z", "2024-01-01T00:00:00Z", "2024-12-31T19:00:00-05:00"]
        games = ["101", "102", "103"]
        context = game_context([raw_schedule(g, d) for g, d in zip(games, dates)], [base_game(g, d, season=2025) for g, d in zip(games, dates)], season=2025)
        joined, _ = nba.join_players(pd.DataFrame([player(g, date=d, season=2025) for g, d in zip(games, dates)]), context)
        self.assertEqual(joined.evaluation_split.tolist(), ["fit_pre_2024", "calibration_2024", "holdout_2025_plus"])
        self.assertEqual(len(nba.descriptive_summary(joined)), 3)

    def test_whole_game_conflict_does_not_spread_to_unrelated_game(self):
        context = game_context([raw_schedule(), raw_schedule("102")], [base_game(), base_game("102")])
        joined, excluded = nba.join_players(pd.DataFrame([player(team_id="44"), player(person="2"), player("102")]), context)
        self.assertEqual(joined.game_id.tolist(), ["102"])
        self.assertEqual(len(excluded), 2)

    def test_csv_schema_header_hash_and_null_contract(self):
        compressed = gzip.compress(b"game_id,points\n101,8\n")
        schema = {"null_token": nba.NULL, "rows": 1, "columns": [{"name": "game_id"}, {"name": "points"}]}
        declared = {"bytes": len(compressed), "sha256": hashlib.sha256(compressed).hexdigest(), "columns": 2, "rows": 1}
        self.assertEqual(nba.validate_csv_contract(compressed, schema, declared, ["game_id"]), ["game_id", "points"])
        with self.assertRaises(ValueError):
            nba.validate_csv_contract(compressed, {**schema, "null_token": ""}, declared, ["game_id"])
        with self.assertRaises(ValueError):
            nba.validate_csv_contract(compressed, schema, declared, ["missing_column"])
        with self.assertRaises(ValueError):
            nba.validate_csv_contract(compressed, {**schema, "columns": list(reversed(schema["columns"]))}, declared, ["game_id"])

    def test_schedule_metadata_rejects_missing_or_ambiguous_inventory(self):
        with self.assertRaises(ValueError):
            nba.schedule_pins({"source_assets": []})

    def test_cloud_guard_before_network_or_writes(self):
        with patch.object(nba, "require_github_hosted_runner", side_effect=RuntimeError("blocked")), patch.object(nba.requests, "get") as request, patch.object(Path, "mkdir") as mkdir, patch.object(pd.DataFrame, "to_csv") as writer:
            with self.assertRaises(RuntimeError):
                nba.main()
            with self.assertRaises(RuntimeError):
                nba.Remote(Path("never-created"))
            with self.assertRaises(RuntimeError):
                nba.write_table(pd.DataFrame(), Path("never-written.csv.gz"))
            request.assert_not_called()
            mkdir.assert_not_called()
            writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
