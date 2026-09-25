"""Synthetic identity-quality tests only; no source datasets or files."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nba_crosswalk as crosswalk


class CandidateCrosswalk(unittest.TestCase):
    def test_cross_snapshot_conflicts_and_incomplete_pairs(self):
        frame = pd.DataFrame({"season": [2026, 2027, 2026, 2026, 2026, 2026], "project_source_snapshot": ["2026", "2027", "2026", "2026", "2026", "2026"], "espn_athlete_id": ["1", "1", "2", "2", "3", "bad"], "nba_player_id": ["11", "12", "22", "22", None, "44"], "match_method": ["source_method"] * 6, "match_confidence": [0.95] * 6, "source_extra_field": ["kept"] * 6})
        out = crosswalk.mark_candidates(frame)
        self.assertEqual(int(out.candidate_pair_complete.sum()), 4)
        self.assertEqual(int(out.candidate_pair_bijective_within_snapshot.sum()), 4)
        self.assertEqual(int(out.candidate_pair_bijective_across_loaded_snapshots.sum()), 2)
        self.assertEqual(int(out.source_duplicate_pair_row.sum()), 2)
        self.assertTrue(out.source_extra_field.eq("kept").all())
        self.assertFalse(out.identity_independently_verified.any())
        self.assertFalse(out.automatic_training_join_allowed.any())

    def test_many_to_one_is_not_bijective(self):
        frame = pd.DataFrame({"season": [2026, 2026], "project_source_snapshot": ["s", "s"], "espn_athlete_id": ["1", "2"], "nba_player_id": ["11", "11"], "match_method": ["same", "same"], "match_confidence": [1.0, 1.0]})
        out = crosswalk.mark_candidates(frame)
        self.assertFalse(out.candidate_pair_bijective_within_snapshot.any())
        self.assertFalse(out.candidate_pair_bijective_across_loaded_snapshots.any())

    def test_missing_required_source_fields_rejected(self):
        with self.assertRaises(ValueError):
            crosswalk.mark_candidates(pd.DataFrame({"id": []}))

    def test_local_execution_blocked_before_io(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(crosswalk.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                crosswalk.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
