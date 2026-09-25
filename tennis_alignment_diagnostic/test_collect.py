"""Fabricated, in-memory fixtures only; no source requests or dataset files."""
import copy
import csv
from datetime import date
import gzip
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tennis_alignment_diagnostic import collect as c
from tennis_weather.test_collect import entities, match


def candidate():
    return c.baseline.candidate("Q128304298", entities())


def metric(rows, name):
    values = [row["count"] for row in rows if row["metric"] == name]
    if len(values) != 1:
        raise AssertionError("Expected one metric " + name)
    return values[0]


def frozen_rows(data):
    data = copy.deepcopy(data)
    for qid, entity in data.items():
        for prop, claims in entity.get("claims", {}).items():
            for index, claim in enumerate(claims):
                claim["id"] = qid + "$synthetic-" + prop + "-" + str(index)
    return c.baseline.fact_rows(data, "synthetic")


class SnapshotAndTerms(unittest.TestCase):
    def test_schema_null_and_required_columns_checked(self):
        schema = {"null_token": r"\N", "tables": {"wikidata_cc0/claims.csv.gz": {"columns": c.baseline.FACT_COLUMNS, "rows": 0},
            "wikidata_cc0/labels.csv.gz": {"columns": c.baseline.LABEL_COLUMNS, "rows": 0}}}
        self.assertEqual(c.schema_contract(json.dumps(schema).encode(), "snapshot")["wikidata_cc0/claims.csv.gz"], 0)
        schema["null_token"] = "NA"
        with self.assertRaises(ValueError): c.schema_contract(json.dumps(schema).encode(), "snapshot")
        schema["null_token"] = r"\N"; schema["tables"]["wikidata_cc0/labels.csv.gz"]["columns"] = ["entity_id"]
        with self.assertRaises(ValueError): c.schema_contract(json.dumps(schema).encode(), "snapshot")
    def test_frozen_claims_replay_without_fabricated_reference_objects(self):
        facts, labels = frozen_rows(entities())
        restored = c.replay_entities(facts, labels)
        valid, rejected = c.baseline.candidate_preflight(restored)
        self.assertEqual(set(valid), {"Q128304298"})
        self.assertEqual(len(rejected), 1)
        for value in restored.values():
            for rows in value["claims"].values():
                for claim in rows:
                    self.assertNotIn("references", claim)
                    self.assertEqual(claim["source_reference_count"], 0)

    def test_duplicate_frozen_claim_and_label_records_rejected(self):
        facts, labels = frozen_rows(entities())
        with self.assertRaises(ValueError): c.replay_entities(facts + [facts[0]], labels)
        with self.assertRaises(ValueError): c.replay_entities(facts, labels + [labels[0]])

    def test_multilingual_aliases_are_added_exactly_not_generated(self):
        data = {"labels": {"en": {"value": "Avery Synthetic"}},
            "aliases": {"en": [{"value": "Avery Alternate"}], "mul": [{"value": "Avery Example"}, {"value": "Avery"}]}}
        self.assertNotIn("avery example", c.policy_names(data, "current"))
        self.assertIn("avery example", c.policy_names(data, "both_alias_languages"))
        self.assertNotIn("avery", c.policy_names(data, "both_alias_languages"))
        with self.assertRaises(ValueError): c.policy_names(data, "fuzzy")

    def test_unselected_default_label_is_not_in_alias_only_policy(self):
        data = {"labels": {"en": {"value": "Avery Synthetic"}, "mul": {"value": "Avery Example"}}}
        self.assertNotIn("avery example", c.policy_names(data, "both_alias_languages"))

    def test_omission_count_is_distinct_after_normalization(self):
        data = entities()
        data["Q10001"]["aliases"] = {"en": [{"value": "Avery Current"}], "mul": [{"value": "Avery Added"}, {"value": "  AVERY  ADDED "}]}
        rows = [row for row in c.term_counts(data) if row["candidate_catalog_id"] == "Q128304298" and row["participant_slot"] == 1]
        self.assertEqual(metric(rows, "omitted_distinct_eligible_aliases"), 1)
        self.assertEqual(metric(rows, "mul_normalization_duplicate_aliases"), 1)


class GateSemantics(unittest.TestCase):
    def test_unique_current_match_is_diagnostic_only(self):
        rows, outcome, selected = c.diagnose_candidate(candidate(), entities(), [match()], "normalized", "current")
        self.assertEqual(outcome, "one")
        self.assertEqual(metric(rows, "candidate_rows"), 1)
        self.assertEqual(metric(rows, "candidate_cardinality_one"), 1)
        self.assertEqual(len(selected), 1)
        self.assertFalse(any("accepted" in row["metric"] for row in rows))
        self.assertEqual(metric(rows, "exact_identity_direct_player_order"), 0)
        self.assertEqual(metric(rows, "exact_identity_swapped_player_order"), 1)

    def test_independent_gates_do_not_disappear_after_tournament_failure(self):
        wrong = match(); wrong["tournament"] = "Synthetic different tournament"
        rows, outcome, _ = c.diagnose_candidate(candidate(), entities(), [wrong], "normalized", "current")
        self.assertEqual(outcome, "zero")
        self.assertEqual(metric(rows, "independent_exact_day"), 1)
        self.assertEqual(metric(rows, "through_exact_day_rows"), 0)
        self.assertEqual(metric(rows, "first_failure_tournament"), 1)
        self.assertEqual(metric(rows, "first_failure_exact_day"), 0)

    def test_funnel_first_failures_conserve_rows(self):
        a, b, d = match(), match(), match()
        b.update(match_id="another", **{"round": "SF"})
        d.update(match_id="third", match_date="2024-06-02")
        rows, _, _ = c.diagnose_candidate(candidate(), entities(), [a, b, d], "normalized", "current")
        failed = sum(row["count"] for row in rows if row["metric"].startswith("first_failure_"))
        self.assertEqual(failed + metric(rows, "candidate_rows"), 3)

    def test_multiple_matches_distinguish_rows_from_ids(self):
        for second_id, expected_ids in [("synthetic-match", 1), ("second-match", 2)]:
            second = match(); second["match_id"] = second_id
            rows, outcome, _ = c.diagnose_candidate(candidate(), entities(), [match(), second], "normalized", "current")
            self.assertEqual(outcome, "multiple")
            self.assertEqual(metric(rows, "candidate_rows"), 2)
            self.assertEqual(metric(rows, "candidate_distinct_nonempty_ids"), expected_ids)

    def test_single_candidate_id_elsewhere_is_rejected(self):
        other = match(); other["round"] = "SF"
        rows, outcome, _ = c.diagnose_candidate(candidate(), entities(), [match(), other], "normalized", "current")
        self.assertEqual(outcome, "repeated_id")
        self.assertEqual(metric(rows, "single_candidate_id_repeated_in_entire_metadata"), 1)

    def test_any_exact_identity_quality_failure_blocks_policy(self):
        bad = match(); bad.update(match_id="bad", singles_metadata_eligible="False")
        rows, outcome, _ = c.diagnose_candidate(candidate(), entities(), [match(), bad], "normalized", "current")
        self.assertEqual(outcome, "quality_blocked")
        self.assertEqual(metric(rows, "candidate_rows"), 1)
        self.assertEqual(metric(rows, "existing_policy_blocked_by_any_identity_quality_failure"), 1)

    def test_default_alias_can_change_counterfactual_without_fuzzy_matching(self):
        data = entities()
        data["Q10001"]["labels"]["en"]["value"] = "Avery Synthetic"
        data["Q10001"]["aliases"] = {"en": [{"value": "Avery Current"}], "mul": [{"value": "Avery Example"}]}
        item = c.baseline.candidate("Q128304298", data)
        _, baseline, _ = c.diagnose_candidate(item, data, [match()], "normalized", "current")
        rows, alternative, _ = c.diagnose_candidate(item, data, [match()], "normalized", "both_alias_languages")
        self.assertEqual((baseline, alternative), ("zero", "one"))
        self.assertTrue(all(row["counterfactual_only"] for row in rows))
        self.assertEqual(metric(rows, "exact_identity_pairs_requiring_selected_alias"), 1)

    def test_added_alias_collision_blocks_counterfactual(self):
        data = entities()
        data["Q10001"]["aliases"] = {"en": [{"value": "Avery Current"}], "mul": [{"value": "Blair Sample"}]}
        item = c.baseline.candidate("Q128304298", data)
        rows, outcome, _ = c.diagnose_candidate(item, data, [match()], "normalized", "both_alias_languages")
        self.assertEqual(outcome, "name_collision")
        self.assertEqual(metric(rows, "name_policy_collision_blocked"), 1)

    def test_score_and_winner_fields_never_affect_metrics(self):
        left = match(); left.update(winner="SECRET_A", score="SECRET_B")
        right = match(); right.update(winner="different", score="different")
        result1 = c.diagnose_candidate(candidate(), entities(), [left], "normalized", "current")[0]
        result2 = c.diagnose_candidate(candidate(), entities(), [right], "normalized", "current")[0]
        self.assertEqual(result1, result2)
        self.assertNotIn("SECRET", json.dumps(result1))
        self.assertNotIn("Avery", json.dumps(result1))


class SourceMetadata(unittest.TestCase):
    def raw(self):
        return {"match_id": "synthetic", "Player 1": "Avery Example", "Player 2": "Blair Sample", "Date": "20240601", "Tournament": "Wimbledon", "Round": "F", "Score": "do-not-use"}

    def test_raw_projection_and_date_parsing(self):
        rows, counts = c.raw_identity_rows([self.raw()])
        self.assertEqual(rows[0]["match_date"], "2024-06-01T00:00:00")
        self.assertNotIn("Score", rows[0])
        self.assertEqual(counts["source_date_nonempty_parse_failures"], 0)

    def test_raw_header_ambiguity_rejected(self):
        row = self.raw(); row["Player1"] = "Another Example"
        with self.assertRaises(ValueError): c.raw_identity_rows([row])

    def test_invalid_source_dates_counted_without_inventing_dates(self):
        row = self.raw(); row["Date"] = "invalid"
        rows, counts = c.raw_identity_rows([row])
        self.assertIsNone(rows[0]["match_date"])
        self.assertEqual(counts["source_date_nonempty_parse_failures"], 1)
        self.assertEqual(counts["before_parse_date_representation_invalid_syntax"], 1)

    def test_raw_date_null_empty_and_compact_before_parse_are_separate(self):
        rows = []
        for value in (None, "", "invalid", "20240601"):
            row = self.raw(); row["Date"] = value; rows.append(row)
        parsed, counts = c.raw_identity_rows(rows)
        self.assertEqual(sum(row["match_date"] is None for row in parsed), 3)
        for category in ("null", "empty", "invalid_syntax", "compact_yyyymmdd"):
            self.assertEqual(counts["before_parse_date_representation_" + category], 1)

    def test_same_id_changed_identity_is_visible_with_canonical_dates(self):
        raw = match(); normalized = match()
        raw["match_date"] = "2024-06-01T00:00:00"
        rows = c.compare_metadata([raw], [normalized])
        self.assertEqual(metric(rows, "shared_ids_exact_identity_tuple_sets_agree"), 1)
        for column in ("player1_name", "match_date", "tournament", "round"):
            changed = dict(normalized); changed[column] = "2024-06-02" if column == "match_date" else "Changed Synthetic Value"
            rows = c.compare_metadata([raw], [changed])
            self.assertEqual(metric(rows, "shared_ids_identity_tuple_sets_conflict"), 1)
            self.assertEqual(metric(rows, "source_export_rows_id_present_but_identity_tuple_absent"), 1)

    def test_literal_text_changes_remain_visible_in_integrity_comparison(self):
        raw, normalized = match(), match()
        normalized["player1_name"] = "  " + raw["player1_name"].upper().replace(" ", "  ") + " "
        self.assertEqual(c.baseline.normalized(raw["player1_name"]), c.baseline.normalized(normalized["player1_name"]))
        rows = c.compare_metadata([raw], [normalized])
        self.assertEqual(metric(rows, "shared_ids_exact_identity_tuple_sets_agree"), 0)
        self.assertEqual(metric(rows, "shared_ids_identity_tuple_sets_conflict"), 1)

    def test_csv_projection_retains_only_identity_and_preserves_null_empty(self):
        row = self.raw(); row["Player 1"] = ""; row["Player 2"] = r"\N"
        stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
        rows = c.read_identity_csv(gzip.compress(stream.getvalue().encode()), raw_source=True)
        self.assertNotIn("Score", rows[0])
        self.assertEqual(rows[0]["Player 1"], "")
        self.assertIsNone(rows[0]["Player 2"])
        with self.assertRaises(ValueError): c.read_identity_csv(gzip.compress(b"match_id,match_id\na,b\n"))

    def test_source_export_has_no_normalized_quality_assumption(self):
        rows, _ = c.raw_identity_rows([self.raw()])
        report, outcome, _ = c.diagnose_candidate(candidate(), entities(), rows, "source_export", "current")
        self.assertEqual(outcome, "one")
        self.assertTrue(all(row["counterfactual_only"] for row in report))
        self.assertFalse(any("quality" in row["metric"] for row in report))

    def test_null_and_empty_and_date_representations_are_distinct(self):
        one, two = match(), match()
        one.update(match_id=None, tournament=None, match_date=None)
        two.update(match_id="", tournament="", match_date="")
        rows = c.metadata_counts([one, two], "normalized")
        self.assertEqual(metric(rows, "match_id_null"), 1)
        self.assertEqual(metric(rows, "match_id_empty"), 1)
        self.assertEqual(c.date_status("2024-06-01T00:00:00Z"), "offset_aware")
        self.assertEqual(c.date_status("2024-06-01T12:30:00"), "nonmidnight_naive")


class TransportAndPublication(unittest.TestCase):
    def test_summary_contains_bounded_gate_and_alias_counts_without_source_values(self):
        data = entities()
        data["Q10001"]["aliases"] = {"en": [{"value": "Avery Current"}], "mul": [{"value": "Avery Added"}]}
        wrong = match(); wrong["round"] = "SF"
        gates, _, _ = c.diagnose_candidate(candidate(), data, [wrong], "normalized", "current")
        summary = c.summary_aggregates(c.term_counts(data), [], gates, c.metadata_counts([wrong], "normalized"))
        self.assertEqual(summary["research_only"]["matching_gate_counts"]["Q128304298"]["normalized"]["current"]["first_failure_final_round"], 1)
        self.assertEqual(summary["cc0"]["participant_term_counts"]["Q128304298"]["1"]["omitted_distinct_eligible_aliases"], 1)
        for value in ("Avery", "Blair", "synthetic-match", "2024-06-01"):
            self.assertNotIn(value, json.dumps(summary))
        with self.assertRaises(ValueError): c.summary_aggregates([], [], gates * 100, [])

    def test_guard_precedes_network_and_output(self):
        with patch.object(c, "require_github_hosted_runner", side_effect=RuntimeError("blocked")), patch.object(c.requests, "get") as get:
            with self.assertRaisesRegex(RuntimeError, "blocked"): c.main()
            with self.assertRaisesRegex(RuntimeError, "blocked"): c.Remote().get("https://github.com/test", 10)
            get.assert_not_called()

    def test_live_source_hosts_are_not_allowed(self):
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "get") as get:
            for url in ["https://www.wikidata.org/w/api.php", "https://archive-api.open-meteo.com/", "https://overpass-api.de/api/interpreter", "http://github.com/test", "https://user@github.com/test"]:
                with self.assertRaises(ValueError): c.Remote().get(url, 10)
            get.assert_not_called()

    def test_request_budget_stops_before_network(self):
        remote = c.Remote(); remote.requests = c.MAX_REQUESTS
        with patch.object(c, "require_github_hosted_runner"), patch.object(c.requests, "get") as get:
            with self.assertRaises(RuntimeError): remote.get("https://github.com/test", 10)
            get.assert_not_called()

    def test_no_valid_prerequisite_skips_mcp_download_and_accepts_no_join(self):
        data = entities(); data["Q128304298"]["claims"].pop("P276")
        claims, labels = frozen_rows(data)
        prefix = c.PINS["snapshot"]["prefix"]
        schema = {"null_token": r"\N", "tables": {"wikidata_cc0/claims.csv.gz": {"columns": c.baseline.FACT_COLUMNS, "rows": len(claims)},
            "wikidata_cc0/labels.csv.gz": {"columns": c.baseline.LABEL_COLUMNS, "rows": len(labels)}}}
        members = {prefix + "wikidata_cc0/claims.csv.gz": b"synthetic-claims", prefix + "wikidata_cc0/labels.csv.gz": b"synthetic-labels", prefix + "wikidata_cc0/LICENSE.txt": b"synthetic notice",
            prefix + "schema.json": json.dumps(schema).encode()}
        with patch.object(c, "require_github_hosted_runner"), patch.object(c, "bundle", return_value=members) as bundles, \
             patch.object(c.baseline, "read_csv", side_effect=[claims, labels]), patch.object(c, "publish") as publish, \
             patch.object(c.requests, "get", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new_callable=io.StringIO):
            c.main()
        self.assertEqual(bundles.call_count, 1)
        report = publish.call_args.args[1]
        self.assertEqual(report["accepted_match_joins"], 0)
        self.assertEqual(report["accepted_weather_rows"], 0)
        self.assertEqual(report["source_inputs"]["mcp"], "not_requested_no_valid_frozen_candidates")

    def test_output_path_symlinks_are_rejected(self):
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ValueError): c.safe_path(Path("/synthetic/out/file"), Path("/synthetic"))


if __name__ == "__main__":
    unittest.main()
