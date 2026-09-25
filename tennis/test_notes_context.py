"""Synthetic in-memory note/manifest tests; no real source dataset is read."""
import gzip
import hashlib
import io
import os
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notes_context as notes


def source_frame():
    return pd.DataFrame({"match_id": ["synthetic"] * 4, "Pt": ["1", "2", "3", "4"],
                         "Svr": ["1"] * 4, "Notes": ["", None, "No medical timeout; wind earlier.", "Racket strings broke; underarm serve."]})


class NotesTests(unittest.TestCase):
    def test_no_note_creates_no_negative_example(self):
        for note in [None, pd.NA, "", " ", "Nice point"]:
            self.assertEqual(notes.mentions(note), [])

    def test_word_boundaries_do_not_match_inside_unrelated_words(self):
        self.assertEqual(notes.mentions("The shot landed on the foreground."), [])

    def test_multiple_categories_are_unverified_with_negation_hint(self):
        rows = notes.mention_rows(source_frame(), notes.PREFIX + "mens_singles/era/source_points.csv.gz", "synthetic-hash", 100)
        medical = next(row for row in rows if row["mention_category"] == "medical_or_timeout")
        self.assertEqual(medical["source_row_number"], 103)
        self.assertTrue(medical["negation_word_present"])
        self.assertTrue(medical["retrospective_word_present"])
        self.assertFalse(medical["mention_independently_verified"])
        self.assertFalse(medical["automatic_training_join_allowed"])
        self.assertFalse(medical["absence_inference_allowed"])
        self.assertTrue(medical["research_only_noncommercial"])

    def test_ids_stable_and_source_duplicate_notes_remain_traceable(self):
        frame = source_frame().iloc[[2, 2]].copy()
        member = notes.PREFIX + "womens_singles/era/source_points.csv.gz"
        first = notes.mention_rows(frame, member, "samehash", 0)
        second = notes.mention_rows(frame, member, "samehash", 0)
        self.assertEqual(first, second)
        self.assertEqual(len(set(row["source_record_id"] for row in first)), len(first))
        self.assertTrue(all(row["competition_group"] == "womens_singles" for row in first))

    def test_point_join_requires_positive_integral_identifier(self):
        self.assertEqual(notes.point_key("0002.0"), "2")
        for invalid in [None, pd.NA, "", "2.5", "0", "-1", "1e3", "infinity"]:
            self.assertIsNone(notes.point_key(invalid))

    def test_streamed_csv_hash_and_null_encoding(self):
        member_name = notes.PREFIX + "mens_singles/era/source_points.csv.gz"
        raw = gzip.compress(b"match_id,Pt,Notes\nsynthetic,1,\\N\nsynthetic,2,\nsynthetic,3,rain\n", mtime=0)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            item = tarfile.TarInfo(member_name)
            item.size = len(raw)
            tar.addfile(item, io.BytesIO(raw))
        manifest = {"files": [{"path": member_name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]}
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:gz") as tar:
            members, declared = notes.safe_members(tar, manifest)
            frame = pd.concat(notes.csv_chunks(tar, members[member_name], declared[member_name]), ignore_index=True)
        self.assertTrue(pd.isna(frame.Notes.iloc[0]))
        self.assertEqual(frame.Notes.iloc[1], "")
        self.assertEqual(frame.Notes.iloc[2], "rain")
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r:gz") as tar:
            with self.assertRaisesRegex(ValueError, "checksum"):
                list(notes.csv_chunks(tar, tar.getmember(member_name), {"sha256": "wrong"}))

    def test_archive_traversal_is_rejected(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            item = tarfile.TarInfo("../synthetic.csv.gz")
            item.size = 0
            tar.addfile(item, io.BytesIO())
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode="r") as tar:
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                notes.safe_members(tar, {"files": []})

    def test_local_entrypoint_rejected_before_network_or_storage(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(notes.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                notes.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
