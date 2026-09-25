"""Synthetic-only tests for injury chronology, identities and missing reports."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import injury_context as context


def nba(**updates):
    row = dict(source_url="https://example.test/report", espn_game_id="001", team_source="Boston Celtics",
               matchup_key="BOS@NYK", player_name_source="Synthetic, Person", participation_status_source="Out",
               reason_source="Synthetic reason", entry_type="player_status", report_timestamp_utc="2024-01-01T16:00:00Z",
               game_date="2024-01-01T20:00:00Z")
    row.update(updates)
    return row


def nfl(**updates):
    row = dict(game_id="2024_01_ABC_DEF", team="ABC", player_id="00-000001", report_status="Questionable",
               practice_status="Limited", game_date="2024-09-01T20:00:00Z", date_modified="2024-08-30T10:00:00Z",
               game_date_time_known=True)
    row.update(updates)
    return row


class InjuryTests(unittest.TestCase):
    def test_cutoff_selects_only_prior_report_and_latest_eligible(self):
        old = nba()
        late = nba(source_url="https://example.test/report2", report_timestamp_utc="2024-01-01T19:15:00Z", participation_status_source="Available")
        snapshots, candidates = context.nba_views(pd.DataFrame([old, late]))
        self.assertEqual(len(snapshots), 2)
        minute30 = candidates.loc[candidates.candidate_cutoff_minutes_before_start.eq(30)].iloc[0]
        minute60 = candidates.loc[candidates.candidate_cutoff_minutes_before_start.eq(60)].iloc[0]
        self.assertEqual(minute30.listed_available_count, 1)
        self.assertEqual(minute60.listed_out_count, 1)
        self.assertFalse(candidates.automatic_training_join_allowed.any())
        self.assertEqual(candidates.game_id.iloc[0], "001")

    def test_not_submitted_does_not_become_healthy_zero(self):
        row = nba(player_name_source=None, entry_type="team_not_submitted", participation_status_source=None)
        snapshot, candidates = context.nba_views(pd.DataFrame([row]))
        self.assertTrue(snapshot.team_not_submitted.iloc[0])
        self.assertTrue(pd.isna(snapshot.listed_out_count.iloc[0]))
        self.assertTrue(candidates.empty)

    def test_newer_incomplete_snapshot_blocks_fallback_to_older_report(self):
        unsubmitted = nba(source_url="https://example.test/new", report_timestamp_utc="2024-01-01T19:15:00Z",
                          player_name_source=None, participation_status_source=None, entry_type="team_not_submitted")
        _, candidates = context.nba_views(pd.DataFrame([nba(), unsubmitted]))
        self.assertNotIn(30, candidates.candidate_cutoff_minutes_before_start.tolist())
        self.assertIn(60, candidates.candidate_cutoff_minutes_before_start.tolist())
        conflict = nba(source_url="https://example.test/new", report_timestamp_utc="2024-01-01T19:15:00Z")
        _, candidates = context.nba_views(pd.DataFrame([nba(), conflict, dict(conflict, participation_status_source="Available")]))
        self.assertNotIn(30, candidates.candidate_cutoff_minutes_before_start.tolist())

    def test_conflicting_game_start_across_reports_excludes_cutoffs(self):
        _, candidates = context.nba_views(pd.DataFrame([nba(), nba(source_url="https://example.test/other", game_date="2025-01-01T20:00:00Z")]))
        self.assertTrue(candidates.empty)

    def test_exact_cutoff_and_tied_newest_sources_remain_excluded(self):
        exact = nba(report_timestamp_utc="2024-01-01T19:30:00Z")
        _, candidates = context.nba_views(pd.DataFrame([exact]))
        self.assertTrue(candidates.empty)
        _, candidates = context.nba_views(pd.DataFrame([nba(), nba(source_url="https://example.test/duplicate-time")]))
        self.assertTrue(candidates.empty)

    def test_conflicting_names_block_report_counts(self):
        snapshot, candidates = context.nba_views(pd.DataFrame([nba(), nba(participation_status_source="Available")]))
        self.assertEqual(snapshot.conflicting_or_missing_player_name_rows.iloc[0], 2)
        self.assertTrue(pd.isna(snapshot.listed_out_count.iloc[0]))
        self.assertTrue(candidates.empty)

    def test_upstream_quarantined_report_blocks_counts(self):
        snapshot, candidates = context.nba_views(pd.DataFrame([nba(upstream_source_report_has_quarantined_rows=True)]))
        self.assertTrue(pd.isna(snapshot.listed_out_count.iloc[0]))
        self.assertTrue(candidates.empty)

    def test_unmatched_team_or_postgame_report_cannot_create_cutoff_row(self):
        for row in [nba(team_source="Dallas Mavericks"), nba(report_timestamp_utc="2024-01-01T21:00:00Z")]:
            _, candidates = context.nba_views(pd.DataFrame([row]))
            self.assertTrue(candidates.empty)

    def test_missing_nfl_modification_stays_unknown(self):
        frame = pd.DataFrame([nfl(date_modified=None)])
        result = context.nfl_view(frame, "synthetic")
        self.assertEqual(result.rows_missing_modification_time.iloc[0], 1)
        self.assertFalse(result.all_modifications_before_exact_start.iloc[0])
        self.assertFalse(result.automatic_training_join_allowed.iloc[0])

    def test_nfl_conflicting_duplicate_player_disables_counts(self):
        result = context.nfl_view(pd.DataFrame([nfl(), nfl(report_status="Out")]), "synthetic")
        self.assertEqual(result.conflicting_or_missing_player_id_rows.iloc[0], 2)
        self.assertTrue(pd.isna(result.listed_out_count.iloc[0]))

    def test_nfl_date_only_kickoff_is_not_exact_before_flag(self):
        result = context.nfl_view(pd.DataFrame([nfl(game_date_time_known=False)]), "synthetic")
        self.assertFalse(result.all_modifications_before_exact_start.iloc[0])

    def test_nfl_absent_or_null_kickoff_precision_is_unknown(self):
        frame = pd.DataFrame([nfl()]).drop(columns="game_date_time_known")
        self.assertFalse(context.nfl_view(frame, "synthetic").all_modifications_before_exact_start.iloc[0])
        frame = pd.DataFrame([nfl(game_date_time_known=None)])
        self.assertFalse(context.nfl_view(frame, "synthetic").all_modifications_before_exact_start.iloc[0])

    def test_nfl_blank_team_is_not_a_valid_identity(self):
        result = context.nfl_view(pd.DataFrame([nfl(team=" ")]), "synthetic")
        self.assertFalse(result.team_game_identity_valid.iloc[0])
        self.assertTrue(pd.isna(result.listed_out_count.iloc[0]))

    def test_split_by_calendar_game_time_and_exact_duplicate_removal(self):
        row = nfl(game_date="2025-01-01T00:00:00Z")
        result = context.nfl_view(pd.DataFrame([row, row]), "synthetic")
        self.assertEqual(result.evaluation_split.iloc[0], "holdout_2025_plus")
        self.assertEqual(result.source_rows.iloc[0], 1)
        self.assertEqual(result.listed_questionable_count.iloc[0], 1)

    def test_io_is_guarded_off_runner(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                context.get_metadata("https://example.test")


if __name__ == "__main__":
    unittest.main()
