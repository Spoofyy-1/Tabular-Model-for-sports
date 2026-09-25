"""Synthetic HTML and mocked transport only; no real source data or downloads."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("staff_routines", Path(__file__).with_name("staff_routines.py"))
staff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staff)


def source(parser):
    return {"source_id": "synthetic", "url": "https://example.test/news/synthetic", "sport": "synthetic", "organization": "Example Team", "parser": parser}


def parse(body, parser, extra=""):
    html = "<html>" + extra + "<article>" + body + "</article></html>"
    return staff.parse_document(html, source(parser)["url"]), html


class StaffRoutineTests(unittest.TestCase):
    def test_naive_date_is_not_fabricated_utc(self):
        self.assertEqual(staff.date_fields("2030-06-03T09:15:00"), ("2030-06-03", None, "date_only"))
        self.assertEqual(staff.date_fields("June 3, 2030 9:15 AM"), ("2030-06-03", None, "date_only"))
        self.assertEqual(staff.date_fields("June 3, 2030 9:15 AM EDT")[1], "2030-06-03T13:15:00+00:00")
        self.assertEqual(staff.date_fields("February 30, 2030")[2], "unknown")

    def test_nba_staff_announcement_is_not_appointment_date(self):
        document, html = parse("<p>Avery Example begins her first season with the organization as the team's performance dietitian.</p>", "nets_dietitian", '<meta property="article:published_time" content="2030-06-03T09:15:00-04:00">')
        rows = staff.annotate(document, source("nets_dietitian"), html, "2031-01-01T12:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["staff_name_as_reported"], "Avery Example")
        self.assertEqual(rows[0]["role_code"], "performance_dietitian")
        self.assertIsNone(rows[0]["staff_valid_from"])
        self.assertIsNone(rows[0]["staff_valid_to"])
        self.assertIsNone(rows[0]["observation_date"])
        self.assertFalse(rows[0]["historical_publication_verified"])
        self.assertFalse(rows[0]["automatic_training_join_allowed"])
        self.assertNotIn("begins her", json.dumps(rows))

    def test_directory_is_observed_now_without_historical_backfill(self):
        html = '<meta property="article:published_time" content="2020-01-01"><table><tr><td>DIETITIAN</td><td>Avery Example</td></tr><tr><td>PHYSICIAN</td><td>Other Person</td></tr></table>'
        document = staff.parse_document(html, source("sixers_directory")["url"])
        rows = staff.annotate(document, source("sixers_directory"), html, "2031-01-01T12:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["source_published_date"])
        self.assertEqual(rows[0]["observation_date"], "2031-01-01")
        self.assertEqual(rows[0]["observation_date_precision"], "retrieval_date_listing_only")
        self.assertIsNone(rows[0]["staff_valid_from"])

    def test_nfl_advice_not_observed_adherence(self):
        document, html = parse("<p>We met Director of Team Nutrition Morgan Example.</p><p>Example discussed a small-to-medium size meal a few hours before the game.</p><p>Example encouraged the rookies to speak with him during games for snacks.</p>", "broncos_seminar")
        rows = staff.annotate(document, source("broncos_seminar"), html, "2031-01-01T12:00:00+00:00")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["staff_name_as_reported"], "Morgan Example")
        self.assertTrue(all(r["assertion_kind"] == "advice_reported" for r in rows[1:]))
        self.assertTrue(all(not r["match_adherence_observed"] and not r["performance_effect_established"] for r in rows))
        self.assertTrue(all(r["observation_date"] is None for r in rows))

    def test_ambiguous_staff_identity_emits_nothing(self):
        document, _ = parse("<p>Director of Team Nutrition Morgan Example.</p><p>Director of Team Nutrition Taylor Different.</p>", "broncos_seminar")
        self.assertEqual(staff.extract_facts(document, source("broncos_seminar")), [])

    def test_atp_preparation_plan_and_role(self):
        document, html = parse("<p>Jordan Example earned the biggest win of his career. His team was led by coach Morgan Sample, who celebrated.</p><p>The coach described footage he would watch and analytics he would look at.</p><p>We will have a light practice tomorrow afternoon.</p>", "atp_final_preparation")
        rows = staff.annotate(document, source("atp_final_preparation"), html, "2031-01-01T12:00:00+00:00")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["staff_name_as_reported"], "Morgan Sample")
        self.assertEqual(rows[0]["subject_name_as_reported"], "Jordan Example")
        self.assertEqual([r["assertion_kind"] for r in rows[1:]], ["plan_reported", "plan_reported"])

    def test_tennis_habits_deduplicate_without_causal_labels(self):
        document, _ = parse("<p>Jordan Example should feel right at home in Miami.</p><p>His fitness coach Morgan Sample and physio Alex Fiction are natives of another country.</p><p>His team breakfasts include mate.</p><p>Now all team breakfasts include mate, reports the team.</p><p>He is trying to imitate Maradona before a practice session or a match.</p>", "atp_team_routine")
        facts = staff.extract_facts(document, source("atp_team_routine"))
        self.assertEqual(len(facts), 4)
        self.assertEqual(sum(r["routine_code"] == "team_breakfast_mate" for r in facts), 1)
        self.assertEqual({r["role_code"] for r in facts if r["annotation_type"] == "staff_role"}, {"fitness_coach", "physiotherapist"})

    def test_structured_article_accepts_exact_scope_excludes_related(self):
        body = "Avery Example begins her first season with the organization as the team's performance dietitian."
        data = {"props": {"pageProps": {"story": {"slug": "synthetic", "content": {"rendered": "<p>" + body + "</p>"}}, "related": [{"slug": "synthetic", "articleBody": "Wrong Example begins her first season with the organization as the team's performance dietitian."}]}}}
        html = '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
        document = staff.parse_document(html, source("nets_dietitian")["url"])
        facts = staff.extract_facts(document, source("nets_dietitian"))
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["staff_name_as_reported"], "Avery Example")

    def test_conflicting_article_and_prose_date_not_publication(self):
        data = {"@type": "NewsArticle", "url": "https://example.test/news/other", "datePublished": "2030-01-02", "articleBody": "Avery Example begins her first season with the organization as the team's performance dietitian."}
        html = '<script type="application/ld+json">' + json.dumps(data) + '</script><article><p>The event happened on June 3, 2030.</p></article>'
        document = staff.parse_document(html, source("nets_dietitian")["url"])
        self.assertIsNone(document["source_published_date"])
        self.assertEqual(staff.extract_facts(document, source("nets_dietitian")), [])

    def test_cloud_guard_before_filesystem_or_network(self):
        with patch.object(staff, "require_github_hosted_runner", side_effect=RuntimeError("synthetic refusal")), patch.object(staff.Path, "mkdir") as mkdir, patch.object(staff.requests, "Session") as session:
            with self.assertRaisesRegex(RuntimeError, "synthetic refusal"):
                staff.main([])
            mkdir.assert_not_called()
            session.assert_not_called()

    def test_budget_refuses_before_transport(self):
        class Session:
            def get(self, *args, **kwargs):
                raise AssertionError("Network must not be called")
        budget = staff.Budget(Session())
        budget.requests = staff.MAX_REQUESTS
        with self.assertRaisesRegex(RuntimeError, "collection_budget_exhausted"):
            budget.get("https://example.test")

    def test_unbounded_chunked_response_never_exceeds_total_byte_cap(self):
        class Raw:
            def __init__(self):
                self.stream = io.BytesIO(b"synthetic-response-longer-than-cap")
            def read(self, size, decode_content=True):
                return self.stream.read(size)
        class Response:
            status_code = 200
            headers = {"Content-Type": "text/html"}
            raw = Raw()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        class Session:
            def get(self, *args, **kwargs):
                return Response()
        budget = staff.Budget(Session())
        with patch.object(staff, "MAX_BYTES", 10), patch.object(staff, "MAX_OBJECT_BYTES", 100):
            with self.assertRaisesRegex(RuntimeError, "stream_reaches_budget"):
                budget.get("https://example.test")
        self.assertEqual(budget.bytes, 10)

    def test_unattributed_nutrition_advice_is_not_assigned_to_staff(self):
        document, _ = parse("<p>Director of Team Nutrition Morgan Example.</p><p>SomeoneElse discussed a small-to-medium size meal a few hours before the game.</p>", "broncos_seminar")
        self.assertEqual(len(staff.extract_facts(document, source("broncos_seminar"))), 1)

    def test_conference_profile_exact_identity_and_team_no_tenure_backfill(self):
        config = dict(source("nba_conference_dietitian"), url="https://healthandperformancemeetings.nba.com/participants/avery-example/")
        html = '<a href="/participants/avery-example/">Avery Example</a><p>Avery is currently serving as the Performance Dietitian for the Example Team and doing research.</p>'
        document = staff.parse_document(html, config["url"])
        rows = staff.annotate(document, config, html, "2031-01-01T12:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["staff_name_as_reported"], "Avery Example")
        self.assertEqual(rows[0]["assertion_kind"], "undated_profile_role_reported")
        self.assertEqual(rows[0]["observation_date_precision"], "retrieval_date_profile_only")
        self.assertIsNone(rows[0]["staff_valid_from"])
        self.assertIsNone(rows[0]["staff_valid_to"])
        self.assertIsNone(rows[0]["source_published_date"])
        wrong = html.replace("for the Example Team", "for the Other Team")
        self.assertEqual(staff.extract_facts(staff.parse_document(wrong, config["url"]), config), [])

    def test_conference_coach_requires_full_name_and_exact_team_relationship(self):
        config = dict(source("nba_conference_coach"), url="https://healthandperformancemeetings.nba.com/participants/avery-example/")
        html = '<h1>Avery Example</h1><p>Avery Example recently concluded a season at the helm of the Example Team after being hired as the 12th head coach in franchise history on May 9, 2030.</p>'
        rows = staff.annotate(staff.parse_document(html, config["url"]), config, html, "2031-01-01T12:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["role_code"], "head_coach")
        self.assertIsNone(rows[0]["staff_valid_from"])
        self.assertIsNone(rows[0]["source_published_date"])
        wrong = html.replace("<p>Avery Example", "<p>Another Person")
        self.assertEqual(staff.extract_facts(staff.parse_document(wrong, config["url"]), config), [])

    def test_conference_ambiguous_or_external_profile_identity_excluded(self):
        config = dict(source("nba_conference_dietitian"), url="https://healthandperformancemeetings.nba.com/participants/avery-example/")
        html = '<a href="https://other.test/participants/avery-example/">Avery Example</a><p>Avery is currently serving as the Performance Dietitian for the Example Team.</p>'
        self.assertEqual(staff.extract_facts(staff.parse_document(html, config["url"]), config), [])
        html += '<a href="/participants/avery-example/">Avery Example</a><a href="/participants/avery-example/">Another Identity</a>'
        self.assertEqual(staff.extract_facts(staff.parse_document(html, config["url"]), config), [])

    def test_aggregate_source_diagnostics_never_contain_fact_rows(self):
        audit = [{"source_id": "synthetic", "sport": "NBA", "status": "collection_or_parse_failed", "rows": 0, "http_status": 403, "reason": "http_403", "requests": 1, "downloaded_bytes": 0, "staff_name_as_reported": "Never Export", "body": "Never Export"}]
        result = staff.aggregate_source_results(audit)
        self.assertEqual(result[0]["http_status"], 403)
        self.assertEqual(result[0]["rows"], 0)
        self.assertNotIn("Never Export", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
