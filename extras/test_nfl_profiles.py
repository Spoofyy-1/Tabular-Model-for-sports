import unittest
import pandas as pd
import nfl_profiles as profiles


class ProfileTests(unittest.TestCase):
    def test_career_statistics_never_enter_candidate_table(self):
        source = pd.DataFrame({"season": [2024, 2025], "pick": [1, 2], "gsis_id": ["00-0000001", "00-0000002"],
                               "hof": [False, True], "games": [9, 800], "pass_yards": [100, 10000]})
        result = profiles.candidate_table(source, "draft")
        self.assertTrue({"hof", "games", "pass_yards"}.isdisjoint(result.columns))
        self.assertEqual(result.partition_by_year_only.tolist(), ["development_through_2024", "holdout_2025_onward"])
        self.assertEqual(result.gsis_id.iloc[0], "00-0000001")

    def test_measurements_preserve_missing_values(self):
        source = pd.DataFrame({"season": [2024, 2024, 2024], "ht": ["6-02", "6-14", None], "wt": [200, None, 210]})
        result = profiles.candidate_table(source, "combine")
        self.assertEqual(result.height_inches.iloc[0], 74)
        self.assertTrue(pd.isna(result.height_inches.iloc[1]))
        self.assertTrue(pd.isna(result.weight_kg.iloc[1]))
        self.assertFalse(result.automatic_training_join_allowed.any())


if __name__ == "__main__":
    unittest.main()
