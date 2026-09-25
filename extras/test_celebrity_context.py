"""Synthetic source parsing tests; no network or real attendance records."""
import os
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import celebrity_context as context
import pandas as pd


def document(title="Fictional gallery", date="2022-09-11", text=""):
    return {"title": title, "text": text, "publication": {"source_published_date": date}}


class CelebrityContextTests(unittest.TestCase):
    def test_scoped_jsonld_articlebody_preserves_vip_boundaries(self):
        url = "https://www.nba.com/game/alpha-vs-beta-0022200999"
        body = {"@type": "NewsArticle", "url": url,
                "articleBody": "VIP WATCH\nExample Actor was courtside.\nUP NEXT\nUnrelated Person was on hand."}
        html = '<script type="application/ld+json">' + json.dumps(body) + '</script>'
        doc = context.extract_document(html, url, include_nba_structured=True)
        self.assertEqual(context.nba_watch_blocks(doc["nba_structured_blocks"]), ["Example Actor was courtside."])
        self.assertEqual(context.extract_document(html, url)["nba_structured_blocks"], [])

    def test_nextdata_recap_needs_exact_game_scope_and_excludes_recommendations(self):
        url = "https://www.nba.com/game/alpha-vs-beta-0022200999"
        value = {"props": {"pageProps": {"gameId": "0022200999", "gameRecap": {
            "content": "<h3>VIP WATCH</h3><p>Example Actor was in attendance.</p>",
            "recommendations": [{"articleBody": "VIP WATCH Other Person was courtside."}]}}}}
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(value) + '</script>'
        doc = context.extract_document(html, url, include_nba_structured=True)
        self.assertEqual(context.nba_watch_blocks(doc["nba_structured_blocks"]), ["Example Actor was in attendance."])
        wrong = html.replace('"0022200999"', '"0022200998"')
        self.assertEqual(context.extract_document(wrong, url, include_nba_structured=True)["nba_structured_blocks"], [])

    def test_nfl_game_link_unique_schedule_and_adjacent_actor_clause(self):
        text = "The Chiefs visited the Jets in the reported game, ending 23-20."
        page = {"linked_games": [{"url": "https://www.nfl.com/games/chiefs-at-jets-2022-reg-2?active-tab=watch", "context": text}],
                "blocks": [text, "Video showed entering the stadium's security area with actors Example Actor and Fictional Guest. Other Person was elsewhere.",
                           "A week earlier, Another Person attended a concert."]}
        schedule = pd.DataFrame([{"season": "2022", "week": "2", "game_type": "REG", "away_team": "KC", "home_team": "NYJ",
                                  "game_id": "synthetic-game", "gameday": "2022-02-02", "gametime": "20:20", "espn": "001"}])
        event, actual_context = context.nfl_linked_event(page, schedule)
        self.assertEqual(event["event_key"], "nflverse:synthetic-game")
        self.assertEqual(event["event_start_utc"], "2022-02-03T01:20:00+00:00")
        self.assertEqual(context.nfl_actor_arrivals(page, actual_context)[0][0], "Example Actor and Fictional Guest")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            context.nfl_linked_event(page, pd.concat([schedule, schedule], ignore_index=True))
        schedule.loc[0, "gametime"] = ""
        with self.assertRaises(ValueError):
            context.nfl_linked_event(page, schedule)

    def test_nfl_future_or_nonadjacent_mentions_are_not_attendance(self):
        page = {"blocks": ["Current game.", "Future Guest will be entering the stadium's security area with actors Example Actor.",
                           "Other content.", "A video showed entering the stadium's security area with actors Wrong Person."]}
        self.assertEqual(context.nfl_actor_arrivals(page, "Current game."), [])

    def test_timestamp_needs_timezone_and_date_never_becomes_midnight(self):
        unknown = context.publication_metadata([("2022-09-11", "test")], "", "https://example.test")
        self.assertIsNone(unknown["source_published_at_utc"])
        self.assertEqual(unknown["publication_precision"], "date_only")
        explicit = context.publication_metadata([], "Sunday, 11 September 2022 06:43 PM EDT", "https://example.test")
        self.assertEqual(explicit["source_published_at_utc"], "2022-09-11T22:43:00+00:00")

    def test_date_from_url_is_marked_low_precision(self):
        value = context.publication_metadata([], "", "https://example.test/articles/2022-09-11/gallery")
        self.assertEqual(value["publication_precision"], "url_date_only")
        self.assertIsNone(value["source_published_at_utc"])

    def test_nba_section_boundaries_exclude_other_match_and_future(self):
        blocks = ["Another Actor attended yesterday.", "VIP WATCH", "Example Actor was in attendance.",
                  "Future Guest will be on hand next week.", "UP NEXT", "Wrong Person was courtside."]
        self.assertEqual(context.nba_watch_blocks(blocks), ["Example Actor was in attendance."])

    def test_caption_date_and_gender_are_local_to_caption(self):
        text = "Example Actor and Fictional Guest during a women's singles championship match at the 2022 US Open on Saturday, Sep. 10, 2022 in Flushing, NY. (Camera Person/USTA)"
        value = context.usta_caption(text, document(date="2022-09-11"))
        self.assertEqual(value["event_date"], "2022-09-10")
        self.assertEqual(value["tour"], "WTA")
        self.assertEqual(value["prefix"].strip(), "Example Actor and Fictional Guest")
        self.assertNotIn("Camera Person", value["prefix"])

    def test_opponents_not_extracted_as_spectators(self):
        text = "Example Actor and Fictional Guest attend the men's final match between Player One and Player Two at the US Open on September 11, 2022."
        value = context.usta_caption(text, document())
        self.assertEqual(value["prefix"].strip(), "Example Actor and Fictional Guest")
        self.assertEqual(value["event_key"], "usopen:2022-09-11:men:singles_final")

    def test_quarterfinal_cannot_identify_unique_match(self):
        text = "Example Actor during a women's singles quarterfinal match at the 2022 US Open on Sep. 6, 2022."
        self.assertIsNone(context.usta_caption(text, document()))

    def test_unknown_date_and_conflicting_tournament_year_fail_closed(self):
        missing = "Example Actor during a men's singles championship match at the 2022 US Open."
        conflict = missing + " On September 11, 2023."
        self.assertIsNone(context.usta_caption(missing, document()))
        self.assertIsNone(context.usta_caption(conflict, document()))

    def test_dedicated_final_gallery_requires_matching_explicit_weekday(self):
        text = "Example Actor during a men's singles championship match at the 2022 US Open."
        page = document("Celebrities at the 2022 US Open men's final", text="Seen in Arthur Ashe Stadium on Sunday.")
        self.assertEqual(context.usta_caption(text, page)["event_date"], "2022-09-11")
        page["text"] = "Seen in Arthur Ashe Stadium on Saturday."
        self.assertIsNone(context.usta_caption(text, page))

    def test_row_never_grants_pregame_or_identity_permission(self):
        event = {"event_date": "2022-09-11", "event_key": "synthetic", "sport": "tennis"}
        row = context.attendance_row(event, "Example Actor", "synthetic evidence", {"url": "https://example.test", "sha256": "synthetic"}, document(), "2026-01-01T00:00:00Z", "synthetic")
        self.assertFalse(row["automatic_training_join_allowed"])
        self.assertFalse(row["eligible_for_pregame_feature"])
        self.assertFalse(row["identity_independently_verified"])
        self.assertNotIn("evidence_text", row)

    def test_local_entrypoint_rejected_before_network_or_storage(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(context.requests, "get") as network, patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                context.main()
            network.assert_not_called()
            mkdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
