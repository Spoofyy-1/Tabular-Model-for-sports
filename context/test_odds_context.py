"""Synthetic-only identity/timing tests; no files or network data are accessed."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import odds_context as odds


class OddsContextAudit(unittest.TestCase):
    def setUp(self):
        # Fail the test immediately if an unexpected network call is introduced.
        guard = patch.object(odds.requests, "get", side_effect=AssertionError("Network access is forbidden in synthetic tests"))
        guard.start()
        self.addCleanup(guard.stop)
        self.tables = {
            "nba": {
                "games": pd.DataFrame([{
                    "game_id": "synthetic-game", "home_team": "LAL", "away_team": "HOU",
                    "game_date": "2026-01-01T23:00:00Z",
                }]),
                "players": pd.DataFrame([{
                    "game_id": "synthetic-game", "player_id": "synthetic-player",
                    "player_name": "Test Athlete Jr.",
                }]),
            }
        }
        self.quote = {
            "league": "NBA", "source_row_index": 0,
            "observation_time": "2026-01-01T22:00:00Z",
            "event_time": "2026-01-01T23:05:00Z",
            "stat": "points", "player": "Test Áthlete Jr",
        }
        self.raw = [{"home_team": "LA Lakers", "away_team": "HOU Rockets"}]

    def audit(self, quote=None, tables=None, **kwargs):
        return odds.audit_quotes(copy.deepcopy(tables if tables is not None else self.tables),
                                 [dict(self.quote if quote is None else quote)],
                                 copy.deepcopy(self.raw), **kwargs)[0]

    def reasons(self, audit):
        return json.loads(audit["exclusion_reasons"])

    def test_unique_game_and_player_match_remains_ineligible_for_roi(self):
        row = self.audit()
        self.assertEqual(row["pricing_audit_status"], "eligible_for_further_pricing_audit")
        self.assertEqual(row["matched_game_id"], "synthetic-game")
        self.assertEqual(row["matched_player_id"], "synthetic-player")
        self.assertEqual(row["event_time_difference_seconds"], 300)
        self.assertEqual(row["temporal_partition"], "holdout_2025_onward")
        self.assertFalse(row["roi_eligible"])
        self.assertFalse(row["usable_for_roi"])
        self.assertFalse(row["feature_asof_verified"])
        self.assertFalse(row["rules_fees_depth_verified"])
        self.assertEqual(self.reasons(row), [])

    def test_ambiguous_player_identity_is_quarantined(self):
        tables = copy.deepcopy(self.tables)
        players = tables["nba"]["players"]
        tables["nba"]["players"] = pd.concat([
            players, players.assign(player_id="second-synthetic-player")], ignore_index=True)
        row = self.audit(tables=tables)
        self.assertEqual(row["pricing_audit_status"], "quarantined")
        self.assertIsNone(row["matched_player_id"])
        self.assertIn("no_unique_game_player_identity", self.reasons(row))

    def test_ambiguous_schedule_is_quarantined(self):
        tables = copy.deepcopy(self.tables)
        games = tables["nba"]["games"]
        tables["nba"]["games"] = pd.concat([
            games, games.assign(game_id="second-synthetic-game")], ignore_index=True)
        row = self.audit(tables=tables)
        self.assertIn("no_unique_schedule_match", self.reasons(row))
        self.assertIsNone(row["matched_game_id"])

    def test_naive_quote_or_source_start_time_is_rejected(self):
        for field in ("observation_time", "event_time"):
            with self.subTest(field=field):
                quote = dict(self.quote)
                quote[field] = quote[field].removesuffix("Z")
                row = self.audit(quote=quote)
                self.assertEqual(row["pricing_audit_status"], "quarantined")
                self.assertIn("missing_or_unzoned_source_time", self.reasons(row))

    def test_quote_after_archive_start_but_before_feed_start_is_rejected(self):
        quote = dict(self.quote, observation_time="2026-01-01T23:01:00Z")
        row = self.audit(quote=quote)
        self.assertIn("not_before_both_start_times_with_buffer", self.reasons(row))
        self.assertEqual(row["pricing_audit_status"], "quarantined")

    def test_exact_one_minute_buffer_is_allowed_but_59_seconds_is_not(self):
        row = self.audit(quote=dict(self.quote, observation_time="2026-01-01T22:59:00Z"))
        self.assertEqual(row["pricing_audit_status"], "eligible_for_further_pricing_audit")
        row = self.audit(quote=dict(self.quote, observation_time="2026-01-01T22:59:01Z"))
        self.assertIn("not_before_both_start_times_with_buffer", self.reasons(row))

    def test_quote_at_start_is_rejected_even_with_zero_buffer(self):
        row = self.audit(quote=dict(self.quote, observation_time="2026-01-01T23:00:00Z"),
                         minimum_lead_seconds=0)
        self.assertIn("not_before_both_start_times_with_buffer", self.reasons(row))

    def test_missing_player_is_not_fuzzily_matched(self):
        row = self.audit(quote=dict(self.quote, player="Test Athlete"))
        self.assertIn("no_unique_game_player_identity", self.reasons(row))

    def test_wrong_opponents_or_large_time_disagreement_are_rejected(self):
        tables = copy.deepcopy(self.tables)
        tables["nba"]["games"]["away_team"] = "BOS"
        self.assertIn("no_unique_schedule_match", self.reasons(self.audit(tables=tables)))
        quote = dict(self.quote, event_time="2026-01-01T23:16:00Z")
        self.assertIn("no_unique_schedule_match", self.reasons(self.audit(quote=quote)))

    def test_nfl_archive_columns_timezone_and_date_only_flag(self):
        tables = {"nfl": {
            "games": pd.DataFrame([{
                "game_id": "synthetic-nfl", "home_team": "LAR", "away_team": "SEA",
                "game_date": pd.Timestamp("2024-12-31T23:00:00Z"), "game_date_time_known": True,
            }]),
            "players": pd.DataFrame([{
                "game_id": "synthetic-nfl", "player_id": "synthetic-nfl-player",
                "player_name": "Synthetic Receiver",
            }]),
        }}
        quote = dict(self.quote, league="NFL", stat="receiving_yards", player="Synthetic Receiver",
                     observation_time="2024-12-31T16:00:00-05:00", event_time="2024-12-31T18:00:00-05:00")
        raw = [{"home_team": "Los Angeles Rams", "away_team": "Seattle Seahawks"}]
        row = odds.audit_quotes(copy.deepcopy(tables), [quote], raw)[0]
        self.assertEqual(row["pricing_audit_status"], "eligible_for_further_pricing_audit")
        self.assertEqual(row["temporal_partition"], "development_before_2025")
        tables["nfl"]["games"]["game_date_time_known"] = False
        row = odds.audit_quotes(tables, [quote], raw)[0]
        self.assertIn("date_only_schedule", self.reasons(row))

    def test_local_and_self_hosted_execution_are_refused(self):
        for settings in ({}, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}):
            with self.subTest(settings=settings), patch.dict(os.environ, settings, clear=True):
                with self.assertRaises(RuntimeError):
                    odds.require_hosted()


if __name__ == "__main__":
    unittest.main()
