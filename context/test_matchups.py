"""Synthetic inputs only; no downloads, data files or runner guard bypass."""
import unittest
import pandas as pd
import nba_matchups as m


class MatchupChecks(unittest.TestCase):
    def test_utc_boundary_overrides_calendar_date(self):
        frame = pd.DataFrame({
            "source_game_date": ["2024-12-30", "2024-12-31", "2024-12-31", "2025-01-01", None],
            "game_date": [None, None, "2025-01-01T02:00:00Z", None, None],
        })
        self.assertEqual(m.partition(frame).tolist(), ["development", "boundary_unresolved", "holdout", "holdout", "date_unresolved"])

    def test_producer_id_aliases(self):
        frame = pd.DataFrame({"person_id": [123], "matchups_person_id": [456]})
        self.assertEqual(m.ids(m.column(frame, "personIdOff", "person_id")).tolist(), ["123"])
        self.assertEqual(m.ids(m.column(frame, "personIdDef", "matchups_person_id")).tolist(), ["456"])
        self.assertEqual(m.ids(pd.Series([22400001]), 10).iloc[0], "0022400001")

    def test_missing_ids_and_duplicates_reported(self):
        frame = pd.DataFrame({"game": ["g", "g", None], "player": ["p", "p", None], "source_game_date": ["2024-01-01"] * 3})
        report = m.quality(frame, ["game", "player"])
        self.assertEqual(report["duplicate_nonnull_keys"], 1)
        self.assertEqual(report["null_identifiers"], {"game": 1, "player": 1})


if __name__ == "__main__":
    unittest.main()
