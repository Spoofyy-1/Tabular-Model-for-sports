"""Synthetic publication tests: no SDK initialization, cloud I/O or sports data."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entrypoint


def environment():
    return {"AZURE_STORAGE_ACCOUNT": "syntheticstore", "AZURE_STORAGE_CONTAINER": "private-results",
            "AZURE_BLOB_PREFIX": "experiments", "SPORTS_PROPS_RUN_ID": "synthetic-one",
            "SPORTS_PROPS_GIT_SHA": "a" * 40, "SPORTS_PROPS_DATA_RELEASE": "csv-synthetic-1"}


class FakeContainer:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def upload_blob(self, name, data, **kwargs):
        if name.endswith(self.fail or "<never>"):
            raise OSError("synthetic upload failure")
        body = data.read() if hasattr(data, "read") else data
        self.calls.append((name, body, kwargs))


class EntrypointTests(unittest.TestCase):
    def test_anonymous_container_access_is_rejected(self):
        entrypoint.require_private_container({"public_access": None})
        for access in ["blob", "container"]:
            with self.assertRaisesRegex(RuntimeError, "private"):
                entrypoint.require_private_container({"public_access": access})

    def test_configuration_requires_immutable_metadata_and_safe_paths(self):
        self.assertEqual(entrypoint.configuration(environment())["git_sha"], "a" * 40)
        for key, value in [("SPORTS_PROPS_RUN_ID", "../escape"), ("SPORTS_PROPS_GIT_SHA", "main"),
                           ("SPORTS_PROPS_DATA_RELEASE", ""), ("AZURE_STORAGE_CONTAINER", "bad--container"),
                           ("SPORTS_PROPS_REPO", "other/repo")]:
            env = environment()
            env[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                entrypoint.configuration(env)

    def test_complete_requires_zero_exit_and_exactly_nine_heads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(entrypoint.trainer_result(root, 0)[0], 1)
            for status in [{"status": "complete", "completed_heads": 4},
                           {"status": "completed", "completed_heads": 9},
                           {"status": "running", "completed_heads": 9}]:
                (root / "run_status.json").write_text(json.dumps(status))
                self.assertEqual(entrypoint.trainer_result(root, 0)[0], 1)
            (root / "run_status.json").write_text('{"status":"complete","completed_heads":9}')
            self.assertEqual(entrypoint.trainer_result(root, 0), (0, None))
            self.assertEqual(entrypoint.trainer_result(root, 137)[0], 137)

    def test_corrupt_or_nonobject_status_does_not_prevent_diagnostic_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in ["{truncated", "[]", "null"]:
                (root / "run_status.json").write_text(value)
                self.assertEqual(entrypoint.trainer_result(root, 0), (1, "InvalidTrainerStatus"))

    def test_publication_hashes_artifacts_and_marks_success_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "synthetic.txt").write_text("synthetic-only")
            container = FakeContainer()
            result = entrypoint.publish_artifacts(container, "run/", root, {}, 0, None, None)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(container.calls[-1][0], "run/publication.json")
            self.assertEqual(result["artifacts"][0]["sha256"], hashlib.sha256(b"synthetic-only").hexdigest())
            self.assertTrue(all(not c[2]["overwrite"] for c in container.calls))

    def test_partial_upload_records_failure_and_only_uploaded_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("a")
            (root / "b.txt").write_text("b")
            container = FakeContainer("b.txt")
            result = entrypoint.publish_artifacts(container, "run/", root, {}, 0, None, None)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["publication_error_type"], "OSError")
            self.assertEqual([f["path"] for f in result["artifacts"]], ["a.txt"])
            self.assertEqual(container.calls[-1][0], "run/publication.json")

    def test_terminal_marker_failure_propagates_and_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                entrypoint.publish_artifacts(FakeContainer("publication.json"), "run/", Path(directory), {}, 0, None, None)

    def test_symlink_inventory_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.txt").write_text("synthetic-only")
            (root / "link.txt").symlink_to(root / "target.txt")
            container = FakeContainer()
            result = entrypoint.publish_artifacts(container, "run/", root, {}, 0, None, None)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["artifacts"], [])

    def test_owner_guard_runs_before_cloud_import_or_configuration(self):
        with patch.object(entrypoint, "require_azure_job", side_effect=RuntimeError("local rejected")), \
                patch.object(entrypoint, "configuration", side_effect=AssertionError("must not run")):
            with self.assertRaisesRegex(RuntimeError, "local rejected"):
                entrypoint.main()


if __name__ == "__main__":
    unittest.main()
