"""Synthetic-only NBA injury checks: no network, PDF, dataset or file access."""
from datetime import datetime, date, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_injuries as inj


def games_fixture():
    return pd.DataFrame({"game_id": ["g1", "g2"], "game_local_date": ["2024-12-31", "2025-01-01"], "game_date": ["2024-12-31T20:00:00Z", "2025-01-02T00:00:00Z"], "home_team": ["BOS", "NY"], "away_team": ["GS", "SA"]})


class InjuryReports(unittest.TestCase):
    def test_local_guard_precedes_network_and_directory_creation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(inj.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                inj.main()
            network.assert_not_called()
            mkdir.assert_not_called()

    def test_csv_writer_guard_precedes_file_write(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(pd.DataFrame, "to_csv") as writer:
            with self.assertRaises(RuntimeError):
                inj.csv_table(pd.DataFrame(), Path("synthetic-unused.csv.gz"))
            writer.assert_not_called()

    def test_legacy_and_new_url_cadence(self):
        self.assertTrue(inj.report_url(datetime(2024, 1, 1, 17, 30)).endswith("2024-01-01_05PM.pdf"))
        self.assertTrue(inj.report_url(datetime(2025, 12, 22, 17, 30)).endswith("2025-12-22_05_30PM.pdf"))

    def test_header_verification_uses_dst(self):
        winter = inj.verify_header("Injury Report: 01/10/24 05:30 PM", datetime(2024, 1, 10, 17, 30))
        summer = inj.verify_header("Injury Report: 06/10/24 05:30 PM", datetime(2024, 6, 10, 17, 30))
        self.assertEqual(winter.hour, 22)
        self.assertEqual(summer.hour, 21)
        with self.assertRaises(ValueError):
            inj.verify_header("Injury Report: 01/10/24 05:00 PM", datetime(2024, 1, 10, 17, 30))

    def test_bounded_report_selection(self):
        games = games_fixture()
        self.assertEqual(inj.report_dates(games, date(2024, 1, 1), date(2024, 12, 31), 1), [date(2024, 12, 31)])
        with self.assertRaises(ValueError):
            inj.report_dates(games, date(2024, 1, 1), date(2025, 12, 31), 1)

    def test_ambiguous_games_not_mapped(self):
        source = games_fixture()
        source = pd.concat([source, source.iloc[[0]]], ignore_index=True)
        lookup, excluded = inj.prepare_games(source)
        self.assertEqual(excluded, 2)
        self.assertEqual(lookup.espn_game_id.tolist(), ["g2"])

    def test_early_game_and_holdout_labels_preserve_missing_player_ids(self):
        lookup, _ = inj.prepare_games(games_fixture())
        raw = pd.DataFrame([
            ["12/31/2024", "03:00 (ET)", "GSW@BOS", "Boston Celtics", "Example, One", "Questionable", "Injury/Illness - Test; Test"],
            ["01/01/2025", "07:00 (ET)", "SAS@NYK", "New York Knicks", None, None, "NOT YET SUBMITTED"],
            ["01/01/2025", "07:00 (ET)", "SAS@NYK", "New York Knicks", "Example, Two", "Mystery", "unknown"],
        ], columns=inj.SOURCE_COLUMNS)
        out = inj.normalized_report(raw, datetime(2024, 12, 31, 22, 30, tzinfo=timezone.utc), "https://example.invalid/report.pdf", "abc", "2026-01-01T00:00:00Z", lookup)
        self.assertEqual(out.parse_valid.tolist(), [True, True, False])
        self.assertEqual(out.header_precedes_game_start.tolist(), [False, True, True])
        self.assertEqual(out.evaluation_split.tolist(), ["calibration_2024", "holdout_2025_plus", "holdout_2025_plus"])
        self.assertEqual(out.entry_type.tolist(), ["player_status", "team_not_submitted", "unresolved"])
        self.assertTrue(out.espn_player_id.isna().all())
        self.assertTrue(out.report_header_timestamp_verified.all())
        self.assertFalse(out.historical_web_availability_independently_verified.any())

    def test_schema_mismatch_rejected(self):
        lookup, _ = inj.prepare_games(games_fixture())
        with self.assertRaises(ValueError):
            inj.normalized_report(pd.DataFrame({"unknown": []}), datetime.now(timezone.utc), "url", "hash", "now", lookup)


if __name__ == "__main__":
    unittest.main()
