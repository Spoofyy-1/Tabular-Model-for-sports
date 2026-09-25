"""Pure metadata tests. No Azure commands, authentication, data or GPUs."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import control


SUB = "11111111-1111-1111-1111-111111111111"
ENV_ID = "/subscriptions/" + SUB + "/resourceGroups/ml-training-rg/providers/Microsoft.App/managedEnvironments/ml-training-env"


class ControllerTests(unittest.TestCase):
    def live(self):
        environment = {"id": ENV_ID, "location": "westus3", "properties": {"workloadProfiles": [{"name": "observed-profile", "workloadProfileType": "Consumption-GPU-NC24-A100"}]}}
        app = {"properties": {"managedEnvironmentId": ENV_ID, "workloadProfileName": "observed-profile",
                              "template": {"containers": [{"name": "source", "resources": {"gpu": 1, "cpu": 11, "memory": "73Gi"}}]}}}
        return app, environment

    def gpu(self):
        return control.derive_gpu(*self.live())

    def test_exact_live_resources_copied_and_one_hour_bound(self):
        shape = self.gpu()
        job = control.job_body(shape, "smoke")
        control.assert_bounded_job(job, shape)
        self.assertEqual(job["properties"]["template"]["containers"][0]["resources"], {"gpu": 1, "cpu": 11, "memory": "73Gi"})
        self.assertEqual(job["properties"]["configuration"]["replicaTimeout"], 3600)
        self.assertEqual(job["properties"]["configuration"]["replicaRetryLimit"], 0)

    def test_missing_or_multiple_gpu_fails_without_cpu_fallback(self):
        for gpu in [None, 0, 2, True, "1"]:
            app, env = self.live()
            app["properties"]["template"]["containers"][0]["resources"]["gpu"] = gpu
            with self.assertRaises(control.ControlError):
                control.derive_gpu(app, env)

    def test_wrong_profile_or_environment_fails(self):
        app, env = self.live()
        env["properties"]["workloadProfiles"][0]["workloadProfileType"] = "Consumption-GPU-NC8-T4"
        with self.assertRaises(control.ControlError):
            control.derive_gpu(app, env)
        app, env = self.live()
        app["properties"]["managedEnvironmentId"] = ENV_ID + "-other"
        with self.assertRaises(control.ControlError):
            control.derive_gpu(app, env)

    def test_unowned_job_cannot_be_repurposed(self):
        for job in [None, {"tags": {}}, {"tags": {control.OWNER_KEY: "someone-else"}}]:
            with self.assertRaises(control.ControlError):
                control.assert_owned(job)

    def test_modified_bound_or_gpu_resources_rejected(self):
        shape = self.gpu()
        job = control.job_body(shape, "smoke")
        job["properties"]["configuration"]["replicaRetryLimit"] = 1
        with self.assertRaises(control.ControlError):
            control.assert_bounded_job(job, shape)
        job = control.job_body(shape, "smoke")
        job["properties"]["template"]["containers"][0]["resources"]["gpu"] = 0
        with self.assertRaises(control.ControlError):
            control.assert_bounded_job(job, shape)

    def test_training_requires_digest_and_smoke(self):
        for image, smoke in [("registry.azurecr.io/trainer:latest", "smoke-one"), ("registry.azurecr.io/sports-props@sha256:" + "a" * 64, None)]:
            with self.assertRaises(control.ControlError):
                control.job_body(self.gpu(), "training", image=image, registry="registry.azurecr.io", commit="b" * 40, smoke_execution=smoke)

    def test_smoke_requires_succeeded_and_logged_a100_80gb(self):
        template = control.job_body(self.gpu(), "smoke")["properties"]["template"]
        execution = {"name": "smoke-one", "properties": {"status": "Succeeded", "template": template}}
        evidence = control.smoke_evidence(execution, "NVIDIA A100 80GB PCIe, 81920\n")
        self.assertEqual(evidence["count"], 1)
        self.assertEqual(evidence["memory_mib"], 81920)
        reserved = control.smoke_evidence(execution, "2026-09-25T00:00:00Z NVIDIA A100-SXM4-80GB, 81151\n")
        self.assertEqual(reserved["memory_mib"], 81151)
        for logs in ["NVIDIA T4, 16384", "", "NVIDIA A100, 40960", "NVIDIA A100, 81920\nNVIDIA A100, 81920"]:
            with self.assertRaises(control.ControlError):
                control.smoke_evidence(execution, logs)
        with self.assertRaises(control.ControlError):
            control.smoke_evidence(execution, "NVIDIA A100 80GB, 81151\nNVIDIA T4, 16384")
        execution["properties"]["status"] = "Running"
        with self.assertRaises(control.ControlError):
            control.smoke_evidence(execution, "NVIDIA A100 80GB PCIe, 81920")

    def test_wrong_subscription_rejected_before_mutation(self):
        with self.assertRaises(control.ControlError):
            control.assert_subscription({"id": SUB, "name": "Wrong account"}, "Azure subscription 1", SUB)
        with self.assertRaises(control.ControlError):
            control.assert_subscription({"id": SUB, "name": "Azure subscription 1"}, "Azure subscription 1", "22222222-2222-2222-2222-222222222222")
        args = control.parse_args(["run", "--subscription-id", SUB])
        fake = object.__new__(control.Azure)
        fake.args, fake.subscription = args, SUB
        with patch.object(fake, "_call", return_value={"id": SUB, "name": "Wrong account"}) as call:
            with self.assertRaises(control.ControlError):
                fake.mutate(["containerapp", "job", "start"])
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args[0], ["account", "show"])

    def test_public_blob_container_is_rejected(self):
        control.assert_private_container({"properties": {"publicAccess": "None"}})
        for access in ["Blob", "Container"]:
            with self.assertRaises(control.ControlError):
                control.assert_private_container({"properties": {"publicAccess": access}})

    def test_reserved_test_app_name_and_missing_subscription_are_rejected(self):
        with self.assertRaises(control.ControlError):
            control.parse_args(["deploy", "--smoke", "--subscription-id", SUB, "--job", "a100-test"])
        with self.assertRaises(control.ControlError):
            control.parse_args(["run", "--smoke"])


if __name__ == "__main__":
    unittest.main()
