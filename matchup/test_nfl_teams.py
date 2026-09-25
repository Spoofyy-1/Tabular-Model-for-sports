"""Synthetic in-memory NFL style and leakage tests; no real source data."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import nfl_teams as nfl


def plays(game="synthetic-1", yards=10):
    rows = []
    for team, opponent in [("AAA", "BBB"), ("BBB", "AAA")]:
        for index, kind in enumerate(["pass", "run", "pass", "punt"]):
            rows.append({"game_id": game, "play_id": str(len(rows)+1), "posteam": team, "defteam": opponent,
                         "play_type": kind, "down": [1, 2, 4, 4][index], "qtr": 1, "score_differential": 0, "yardline_100": 15,
                         "pass_attempt": int(kind == "pass"), "rush_attempt": int(kind == "run"), "qb_dropback": int(kind == "pass"), "qb_scramble": 0,
                         "qb_kneel": 0, "qb_spike": 0, "no_play": 0, "shotgun": 1, "no_huddle": 0,
                         "yards_gained": yards, "air_yards": 5 if kind == "pass" else None, "sack": 0, "qb_hit": 0, "interception": 0})
    return pd.DataFrame(rows)


def panel(dates=None):
    dates = dates or ["2024-12-29T18:00:00Z", "2024-12-31T18:00:00Z", "2025-01-05T18:00:00Z"]
    raw_schedule = pd.DataFrame({"game_id": ["synthetic-"+str(i+1) for i in range(len(dates))], "game_date": dates,
                                  "home_team": "AAA", "away_team": "BBB", "season": 2024, "home_score": 20, "away_score": 10,
                                  "game_date_time_known": True, "home_coach": "Synthetic Coach A", "away_coach": "Synthetic Coach B"})
    schedule = nfl.team_schedule(raw_schedule)
    metrics = pd.concat([nfl.game_metrics(plays(game)) for game in raw_schedule.game_id], ignore_index=True)
    return schedule.merge(metrics, on=["game_id", "team", "opponent"], validate="one_to_one")


class NFLTeamTests(unittest.TestCase):
    def test_blank_team_on_auxiliary_event_cannot_create_an_opponent(self):
        raw = plays()
        bad = raw.iloc[:1].copy()
        bad["play_id"] = "99"
        bad["defteam"] = " "
        bad["play_type"] = "kickoff"
        result = nfl.game_metrics(pd.concat([raw, bad], ignore_index=True).astype("string"))
        self.assertEqual(len(result), 2)
        self.assertEqual(set(result.team), {"AAA", "BBB"})
        self.assertFalse(result.duplicated(["game_id", "team"]).any())

    def test_identical_metrics_across_source_partitions_are_collapsed(self):
        first = nfl.game_metrics(plays()).assign(source_partition_member="synthetic/season-one")
        second = first.assign(source_partition_member="synthetic/season-two")
        clean, audit, report = nfl.reconcile_metrics(pd.concat([first, second], ignore_index=True))
        self.assertEqual(len(clean), 2)
        self.assertTrue(audit.empty)
        self.assertEqual(report["identical_metric_rows_collapsed"], 2)
        self.assertTrue(clean.source_partition_members.str.contains("season-one").all())
        self.assertTrue(clean.source_partition_members.str.contains("season-two").all())

    def test_conflicting_metric_excludes_entire_game_both_teams(self):
        first = nfl.game_metrics(plays()).assign(source_partition_member="synthetic/season-one")
        conflict = first.assign(source_partition_member="synthetic/season-two")
        conflict.loc[conflict.team.eq("AAA"), "observed_off_yards"] += 1
        other = nfl.game_metrics(plays("unaffected")).assign(source_partition_member="synthetic/season-two")
        clean, audit, report = nfl.reconcile_metrics(pd.concat([first, conflict, other], ignore_index=True))
        self.assertEqual(set(clean.game_id), {"unaffected"})
        self.assertEqual(set(audit.team), {"AAA", "BBB"})
        self.assertEqual(report["quarantined_games"], 1)
        self.assertEqual(report["conflicting_game_team_keys"], 1)

    def test_within_partition_opponent_conflict_is_not_arbitrarily_chosen(self):
        first = nfl.game_metrics(plays()).assign(source_partition_member="synthetic/season-one")
        conflict = first.loc[first.team.eq("AAA")].assign(opponent="CCC")
        clean, audit, report = nfl.reconcile_metrics(pd.concat([first, conflict], ignore_index=True))
        self.assertTrue(clean.empty)
        self.assertEqual(len(audit), 3)
        self.assertEqual(report["quarantined_games"], 1)

    def test_play_and_fourth_down_denominators(self):
        row = nfl.game_metrics(plays()).set_index("team").loc["AAA"]
        self.assertEqual(row.observed_off_plays, 3)
        self.assertEqual(row.observed_off_called_passes, 2)
        self.assertEqual(row.observed_off_designed_runs, 1)
        self.assertEqual(row.observed_off_fourth_down_decisions, 2)
        self.assertEqual(row.observed_off_fourth_down_go, 1)

    def test_no_plays_kneels_and_spikes_excluded(self):
        raw = plays()
        raw.loc[0, "no_play"] = 1
        raw.loc[1, "qb_kneel"] = 1
        raw.loc[2, "qb_spike"] = 1
        row = nfl.game_metrics(raw).set_index("team").loc["AAA"]
        self.assertEqual(row.observed_off_plays, 0)
        self.assertEqual(row.observed_off_fourth_down_go, 0)

    def test_missing_shotgun_stays_unknown(self):
        raw = plays()
        raw["shotgun"] = None
        metric = nfl.game_metrics(raw).set_index("team").loc["AAA"]
        self.assertEqual(metric.observed_off_shotgun_known, 0)

    def test_current_and_future_outcomes_do_not_change_current_features(self):
        games = panel()
        before = nfl.lagged_features(games)
        changed = games.copy()
        target = changed.game_id.isin(["synthetic-2", "synthetic-3"])
        for name in changed:
            if name.startswith("observed_"):
                changed.loc[target, name] = 9999
        changed.loc[target, "label_result"] = "loss"
        after = nfl.lagged_features(changed)
        pd.testing.assert_frame_equal(before.loc[before.game_id.ne("synthetic-3")], after.loc[after.game_id.ne("synthetic-3")])
        row = before.loc[before.game_id.eq("synthetic-2") & before.team.eq("AAA")].iloc[0]
        self.assertAlmostEqual(row.pre_off_pass_play_rate_last5, 2/3)
        self.assertAlmostEqual(row.opponent_pre_def_pass_play_rate_last5, 2/3)
        self.assertFalse(any(name.startswith("label_") or name.startswith("observed_") for name in before))

    def test_same_utc_date_is_excluded_even_with_earlier_start(self):
        games = panel(["2024-12-29T01:00:00Z", "2024-12-29T20:00:00Z", "2025-01-05T18:00:00Z"])
        features = nfl.lagged_features(games)
        second = features.loc[features.game_id.eq("synthetic-2")]
        self.assertTrue(second.pre_observed_games_last10.eq(0).all())
        self.assertTrue(second.pre_off_pass_play_rate_last10.isna().all())

    def test_calendar_holdout_and_coach_audit(self):
        games = panel()
        self.assertTrue(games.loc[games.game_id.eq("synthetic-3"), "evaluation_split"].eq("holdout_2025_onward").all())
        features = nfl.lagged_features(games)
        self.assertNotIn("source_coach_name", features)
        self.assertFalse(games.source_coach_assignment_time_verified.any())

    def test_prior_calendar_date_alone_is_not_enough_for_completion(self):
        games = panel(["2024-12-29T23:00:00Z", "2024-12-30T01:00:00Z", "2025-01-05T18:00:00Z"])
        features = nfl.lagged_features(games)
        second = features.loc[features.game_id.eq("synthetic-2")]
        self.assertTrue(second.pre_observed_games_last10.eq(0).all())
        self.assertTrue(second.pre_off_pass_play_rate_last10.isna().all())

    def test_tie_is_not_a_binary_loss(self):
        raw = pd.DataFrame({"game_id": ["tie"], "game_date": ["2024-10-01T18:00:00Z"], "season": [2024],
                            "home_team": ["AAA"], "away_team": ["BBB"], "home_score": [20], "away_score": [20]})
        table = nfl.team_schedule(raw)
        self.assertTrue(table.label_result.eq("tie").all())
        self.assertTrue(table.label_team_win.isna().all())

    def test_local_main_rejected_before_network_or_output(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(nfl.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                nfl.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
