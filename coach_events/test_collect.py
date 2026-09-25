"""Synthetic-only HTML/transport tests; no downloaded source fixtures."""
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("coach_collect", Path(__file__).with_name("collect.py"))
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


def source(source_id):
    return next(value for value in collect.SOURCES if value["id"] == source_id)


def annotated(source_id, text, metadata='<meta property="article:published_time" content="2030-01-01T12:00:00-05:00">', headline=""):
    config = source(source_id)
    html = metadata + "<h1>" + headline + "</h1><article><p>" + text + "</p></article>"
    return collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T12:00:00+00:00")


class CoachTests(unittest.TestCase):
    def test_completed_appointment_parsed_not_hardcoded(self):
        rows, status = annotated("nba_nets", "The Brooklyn Nets have named Avery Example as the 24th head coach in franchise history.")
        self.assertEqual(status, "parsed")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["coach_name_as_reported"], "Avery Example")
        self.assertEqual(rows[0]["event_date"], "2030-01-01")
        self.assertEqual(rows[0]["event_type"], "appointment_announced")
        self.assertIsNone(rows[0]["effective_start_date"])
        self.assertIsNone(rows[0]["effective_end_date"])
        self.assertIsNone(rows[0]["canonical_coach_id"])
        self.assertFalse(rows[0]["automatic_training_join_allowed"])
        self.assertEqual(rows[0]["source_hash_basis"], "decoded_text_utf8")
        self.assertEqual(rows[0]["evidence_hash_basis"], "normalized_article_block_utf8")
        self.assertNotIn("have named", json.dumps(rows))

    def test_agreement_stays_distinct_from_appointment(self):
        rows, _ = annotated("nfl_chargers", "The Los Angeles Chargers today agreed to terms with Avery Example as head coach.")
        self.assertEqual(rows[0]["event_type"], "agreement_to_terms_announced")
        rows, status = annotated("nfl_chargers", "The Los Angeles Chargers may interview Avery Example as head coach.")
        self.assertEqual(rows, [])
        self.assertEqual(status, "no_qualified_appointment_evidence")

    def test_naive_publication_does_not_create_utc_time(self):
        rows, _ = annotated("nfl_seahawks", "The Seahawks have hired Avery Example as the ninth head coach.", '<div class="nfl-c-article__date">Jan 1, 2030 at 04:30 PM</div>')
        self.assertEqual(rows[0]["event_date"], "2030-01-01")
        self.assertEqual(rows[0]["publication_precision"], "date_only")
        self.assertIsNone(rows[0]["source_published_at_utc"])

    def test_conflicting_publication_dates_do_not_choose_one(self):
        meta = '<meta property="article:published_time" content="2030-01-01"><div class="nfl-c-article__date">Jan 2, 2030 at 04:30 PM</div>'
        rows, status = annotated("nfl_texans", "The Houston Texans have hired Avery Example as the team's sixth head coach.", meta)
        self.assertEqual(status, "parsed_with_date_conflict")
        self.assertIsNone(rows[0]["event_date"])
        self.assertEqual(rows[0]["date_conflict_status"], "publication_date_conflict")

    def test_weekday_mismatch_quarantines_date(self):
        rows, _ = annotated("nfl_falcons", "The Atlanta Falcons hired Avery Example as their next head coach, the organization announced Thursday.")
        self.assertIsNone(rows[0]["event_date"])
        self.assertEqual(rows[0]["date_conflict_status"], "announcement_weekday_publication_mismatch")

    def test_raptors_pronunciation_and_matching_weekday(self):
        rows, _ = annotated("nba_raptors", "The Toronto Raptors announced Tuesday they have named Avery Examplé (ex-am-play) as head coach.")
        self.assertEqual(rows[0]["coach_name_as_reported"], "Avery Examplé")
        self.assertEqual(rows[0]["event_date"], "2030-01-01")

    def test_panthers_anaphoric_team_requires_scope(self):
        text = "The team agreed to terms with Buccaneers offensive coordinator Avery Example as the seventh head coach in franchise history Tuesday."
        self.assertEqual(annotated("nfl_panthers", text)[0], [])
        rows, _ = annotated("nfl_panthers", text, headline="Panthers announce coaching agreement")
        self.assertEqual(len(rows), 1)

    def test_missing_publication_or_modified_only_never_invents_date(self):
        rows, status = annotated("nba_sixers", "NBA Champion Avery Example has officially been named head coach of the Philadelphia 76ers.", '<meta property="article:modified_time" content="2030-01-01T12:00:00Z">')
        self.assertEqual(status, "parsed_without_publication_date")
        self.assertIsNone(rows[0]["event_date"])
        self.assertEqual(rows[0]["source_modified_at_utc"], "2030-01-01T12:00:00+00:00")

    def test_duplicate_evidence_deduplicates_ambiguous_identities_excluded(self):
        text = "The Brooklyn Nets have named Avery Example as the 24th head coach."
        rows, _ = annotated("nba_nets", text + " " + text)
        self.assertEqual(len(rows), 1)
        rows, status = annotated("nba_nets", text + " The Brooklyn Nets have named Another Person as the 24th head coach.")
        self.assertEqual(rows, [])
        self.assertEqual(status, "ambiguous_appointment_identity")

    def test_related_structured_article_and_foreign_url_excluded(self):
        config = source("nba_nets")
        body = "The Brooklyn Nets have named Avery Example as the 24th head coach."
        data = {"related": [{"@type": "NewsArticle", "articleBody": body}], "story": {"@type": "NewsArticle", "url": "https://foreign.test" + collect.urlparse(config["url"]).path, "articleBody": body}}
        html = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
        self.assertEqual(collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T00:00:00Z")[0], [])

    def test_exactly_scoped_next_data_supported(self):
        config = source("nba_nets")
        data = {"props": {"pageProps": {"story": {"slug": collect.urlparse(config["url"]).path.split("/")[-1], "datePublished": "2030-01-01", "content": {"rendered": "<p>The Brooklyn Nets have named Avery Example as the 24th head coach.</p>"}}}}}
        html = '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
        rows, _ = collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T00:00:00Z")
        self.assertEqual(len(rows), 1)

    def test_unlinked_recommended_article_never_creates_event(self):
        config = source("nba_nets")
        body = "The Brooklyn Nets have named Avery Example as the 24th head coach."
        data = {"recommendedItems": [{"@type": "NewsArticle", "datePublished": "2030-01-01", "articleBody": body}]}
        html = '<main><p>The main article is about a different subject.</p><aside><article><p>' + body + '</p></article></aside></main><script type="application/ld+json">' + json.dumps(data) + '</script>'
        doc = collect.document(html, config["url"])
        self.assertIsNone(doc["source_published_date"])
        self.assertEqual(collect.extract(doc, config, html, "2031-01-01T00:00:00Z")[0], [])

    def test_unrelated_time_card_does_not_date_primary_appointment(self):
        config = source("nba_nets")
        html = '<main><article><p>The Brooklyn Nets have named Avery Example as the 24th head coach.</p></article><div class="recommendedItems"><time datetime="2030-01-01T12:00:00Z">An unrelated item</time></div></main>'
        rows, status = collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T00:00:00Z")
        self.assertEqual(status, "parsed_without_publication_date")
        self.assertIsNone(rows[0]["event_date"])

    def test_multiple_unlinked_dom_articles_are_ambiguous(self):
        config = source("nba_nets")
        html = '<main><article><p>Primary content.</p></article><article><p>The Brooklyn Nets have named Avery Example as the 24th head coach.</p></article></main>'
        self.assertEqual(collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T00:00:00Z")[0], [])

    def test_nested_recommendations_cannot_create_primary_event(self):
        config = source("nba_nets")
        claim = "The Brooklyn Nets have named Avery Example as the 24th head coach."
        for nested in ['<div class="recommendedItems"><p>' + claim + '</p></div>', '<article><p>' + claim + '</p></article>']:
            html = '<main><article><p>Different topic.</p>' + nested + '</article></main>'
            doc = collect.document(html, config["url"])
            self.assertEqual(collect.extract(doc, config, html, "2031-01-01T00:00:00Z")[0], [])

    def test_nested_secondary_structured_html_is_removed(self):
        config = source("nba_nets")
        claim = "The Brooklyn Nets have named Avery Example as the 24th head coach."
        for nested in ['<div class="recommendedItems"><p>' + claim + '</p></div>', '<article><p>' + claim + '</p></article>']:
            body = '<p>Different topic.</p>' + nested
            data = {"@type": "NewsArticle", "url": config["url"], "articleBody": body}
            html = '<script type="application/ld+json">' + json.dumps(data) + '</script>'
            self.assertEqual(collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T00:00:00Z")[0], [])

    def test_nested_recommendation_meta_cannot_date_primary_event(self):
        config = source("nba_nets")
        html = '<main><article><p>The Brooklyn Nets have named Avery Example as the 24th head coach.</p><div class="recommendedItems"><meta property="article:published_time" content="2030-01-01T12:00:00Z"></div></article></main>'
        rows, status = collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T00:00:00Z")
        self.assertEqual(status, "parsed_without_publication_date")
        self.assertIsNone(rows[0]["event_date"])

    def test_all_actual_io_entrypoints_refuse_local_execution(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                collect.Fetcher(None).get("https://example.test")
            with self.assertRaises(RuntimeError):
                collect.write_and_package(Path("must-not-exist"), Path("must-not-exist"), [], [], {})
            with self.assertRaises(RuntimeError):
                collect.main([])

    def test_fetch_403_is_not_retried(self):
        session = FakeSession([FakeResponse(403)])
        with patch.object(collect, "require_github_hosted_runner", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "http_403"):
                collect.Fetcher(session).get("https://example.test/start")
        self.assertEqual(len(session.calls), 1)

    def test_same_host_https_redirect_is_counted(self):
        session = FakeSession([FakeResponse(301, location="/start/"), FakeResponse(200, content=b"<p>synthetic</p>")])
        fetcher = collect.Fetcher(session)
        with patch.object(collect, "require_github_hosted_runner", return_value=None):
            html, url = fetcher.get("https://example.test/start")
        self.assertEqual(url, "https://example.test/start/")
        self.assertEqual(fetcher.requests, 2)
        self.assertEqual(len(fetcher.last["redirects"]), 1)

    def test_same_host_different_article_is_not_followed(self):
        session = FakeSession([FakeResponse(301, location="/another-appointment")])
        fetcher = collect.Fetcher(session)
        with patch.object(collect, "require_github_hosted_runner", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "redirect_changes_article_path"):
                fetcher.get("https://example.test/start")
        self.assertEqual(len(session.calls), 1)

    def test_extract_defensively_rejects_changed_retrieved_page(self):
        config = source("nba_nets")
        html = "<article><p>The Brooklyn Nets have named Avery Example as the 24th head coach.</p></article>"
        rows, status = collect.extract(collect.document(html, config["url"]), config, html, "2031-01-01T00:00:00Z", "https://www.nba.com/nets/news/another-story")
        self.assertEqual(rows, [])
        self.assertEqual(status, "retrieved_page_path_mismatch")

    def test_external_redirect_and_budget_refuse(self):
        session = FakeSession([FakeResponse(302, location="https://other.test/challenge")])
        fetcher = collect.Fetcher(session)
        with patch.object(collect, "require_github_hosted_runner", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "redirect_outside_allowed_origin"):
                fetcher.get("https://example.test/start")
            fetcher.requests = collect.MAX_REQUESTS
            with self.assertRaisesRegex(RuntimeError, "collection_budget_exhausted"):
                fetcher.get("https://example.test/start")
        self.assertEqual(len(session.calls), 1)

    def test_stream_read_never_exceeds_byte_budget(self):
        fetcher = collect.Fetcher(FakeSession([FakeResponse(200, content=b"synthetic body longer than cap")]))
        with patch.object(collect, "require_github_hosted_runner", return_value=None), patch.object(collect, "MAX_BYTES", 10):
            with self.assertRaisesRegex(RuntimeError, "stream_reaches_budget"):
                fetcher.get("https://example.test/start")
        self.assertEqual(fetcher.bytes, 10)


class FakeResponse:
    def __init__(self, status, location=None, content=b""):
        self.status_code = status
        self.headers = {"Content-Type": "text/html"}
        if location:
            self.headers["Location"] = location
        self.raw = self
        self.stream = io.BytesIO(content)
    def read(self, size, decode_content=True):
        return self.stream.read(size)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.responses.pop(0)


if __name__ == "__main__":
    unittest.main()
