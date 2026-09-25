"""Synthetic, in-memory policy tests. No downloads, GPU calls or real fitting."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("train_gpu", Path(__file__).with_name("train_gpu.py"))
training = importlib.util.module_from_spec(spec)
spec.loader.exec_module(training)


def schema(sport="nba"):
    return {"sport": sport, "test_start": "2025-01-01T00:00:00Z",
            "features": ["f_prior_appearances"] + ["f_" + t + s for t in training.TARGETS[sport]
                                                    for s in ["_mean10", "_std10"]],
            "targets": training.TARGETS[sport]}


def frame(dates, sport="nba"):
    result = pd.DataFrame({"sport": sport, "game_date": dates,
                           "game_id": ["00%02d" % i for i in range(len(dates))],
                           "player_id": "0007", "position": "QB"})
    for f in schema(sport)["features"]:
        result[f] = 12.0 if f == "f_prior_appearances" else 2.0
    for target in training.TARGETS[sport]:
        result["target_" + target] = 3.0
    return result


def cuda_config():
    return {"learner": {"generic_param": {"device": "cuda:0"},
                        "gradient_booster": {"gbtree_train_param": {"tree_method": "hist", "updater": "grow_gpu_hist"}}}}


class SyntheticGPUExperimentTests(unittest.TestCase):
    def test_owner_machine_is_rejected_without_platform_gpu_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(training.platform, "system", return_value="Darwin"), \
                patch.object(training.subprocess, "run", side_effect=AssertionError("GPU query forbidden")):
            with self.assertRaisesRegex(RuntimeError, "Azure"):
                training.require_azure_job()

    def test_linux_without_actual_azure_job_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(training.platform, "system", return_value="Linux"):
            with self.assertRaises(RuntimeError):
                training.require_azure_job()

    def test_exact_temporal_boundaries(self):
        data = frame(["2023-12-31T23:59:59Z", "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z",
                      "2025-01-01T00:00:00Z"])
        data["game_date"] = pd.to_datetime(data.game_date, utc=True)
        parts = training.periods(data)
        self.assertEqual(parts["fit"].tolist(), [True, False, False, False])
        self.assertEqual(parts["calibration"].tolist(), [False, True, True, False])
        self.assertEqual(parts["test"].tolist(), [False, False, False, True])

    def test_development_cannot_contain_test_rows(self):
        with self.assertRaisesRegex(ValueError, "partition"):
            training.prepare_frame(frame(["2025-01-01T00:00:00Z"]), schema(), "nba", "development")

    def test_holdout_cannot_contain_fit_or_calibration_rows(self):
        with self.assertRaisesRegex(ValueError, "partition"):
            training.prepare_frame(frame(["2024-12-31T23:59:59Z"]), schema(), "nba", "holdout")

    def test_string_ids_and_missing_feature_preserved(self):
        data = frame(["2023-12-31T23:59:59Z"])
        data["f_points_std10"] = np.nan
        ready = training.prepare_frame(data, schema(), "nba", "development")
        self.assertEqual(ready.game_id.iloc[0], "0000")
        self.assertEqual(ready.player_id.iloc[0], "0007")
        self.assertTrue(pd.isna(ready.f_points_std10.iloc[0]))

    def test_baseline_uppercase_sport_metadata_is_preserved(self):
        for sport in ["nba", "nfl"]:
            data = frame(["2023-12-31T23:59:59Z"], sport)
            data["sport"] = sport.upper()
            ready = training.prepare_frame(data, schema(sport), sport, "development")
            self.assertEqual(ready.sport.iloc[0], sport.upper())

    def test_naive_dates_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            training.prepare_frame(frame(["2023-12-31 23:00:00"]), schema(), "nba", "development")

    def test_duplicate_player_game_rejected(self):
        one = frame(["2023-12-31T23:00:00Z"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            training.prepare_frame(pd.concat([one, one], ignore_index=True), schema(), "nba", "development")

    def test_target_in_feature_list_rejected(self):
        bad = schema()
        bad["features"].append("target_points")
        with self.assertRaisesRegex(ValueError, "pregame"):
            training.validate_schema(bad, "nba")

    def test_cutoff_change_rejected(self):
        bad = schema()
        bad["test_start"] = "2026-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "cutoff"):
            training.validate_schema(bad, "nba")

    def test_nfl_roles_and_cold_start_eligibility(self):
        data = frame(["2024-01-01T00:00:00Z"] * 4, "nfl")
        data["position"] = ["QB", "WR", "QB", "QB"]
        data.loc[2, "f_prior_appearances"] = 9
        data.loc[3, "target_passing_yards"] = np.nan
        self.assertEqual(training.eligible_mask(data, "nfl", "passing_yards").tolist(), [True, False, False, False])

    def test_cpu_fallback_or_wrong_tree_builder_rejected(self):
        for field in ["device", "updater", "tree_method"]:
            bad = cuda_config()
            if field == "device":
                bad["learner"]["generic_param"][field] = "cpu"
            else:
                bad["learner"]["gradient_booster"]["gbtree_train_param"][field] = "grow_quantile_histmaker"
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "GPU training not verified"):
                training.verify_gpu_config(bad)
        self.assertEqual(training.verify_gpu_config(json.dumps(cuda_config()))["device"], "cuda:0")

    def test_fixed_training_has_no_evaluation_or_early_stopping_input(self):
        calls = []
        class FakeBooster:
            def save_config(self):
                return json.dumps(cuda_config())
        class FakeXGBoost:
            @staticmethod
            def train(params, matrix, **kwargs):
                calls.append((copy.deepcopy(params), matrix, kwargs))
                return FakeBooster()
        sentinel = object()
        training.strict_train(FakeXGBoost, sentinel, training.ROUNDS)
        self.assertEqual(calls[0][2], {"num_boost_round": 180})
        self.assertIs(calls[0][1], sentinel)
        self.assertEqual(calls[0][0], training.PARAMS)

    def test_zero_baseline_error_does_not_create_infinite_improvement(self):
        actual = np.array([1., 2.])
        result = training.evaluate(actual, np.array([0., 3.]), actual, np.array([0., 1.]), np.array([2., 3.]))
        self.assertIsNone(result["mae_improvement_pct"])
        json.dumps(result, allow_nan=False)

    def test_negative_nfl_yards_are_not_clipped(self):
        self.assertFalse(training.nonnegative("nfl", "rushing_yards"))
        self.assertTrue(training.nonnegative("nfl", "passing_tds"))
        self.assertTrue(training.nonnegative("nba", "points"))

    def test_exactly_nine_frozen_heads(self):
        config = training.frozen_experiment()
        self.assertEqual(sum(len(v) for v in config["targets"].values()), 9)
        self.assertFalse(config["holdout_tuning"])
        self.assertFalse(config["early_stopping"])
        self.assertIn("reused benchmark", config["benchmark_status"])


if __name__ == "__main__":
    unittest.main()
