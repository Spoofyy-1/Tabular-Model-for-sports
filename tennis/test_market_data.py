"""Synthetic quote/identity tests only; no network, archives or real matches."""
import copy
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("tennis_market_data", Path(__file__).with_name("market_data.py"))
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)

PILOT = {"slug": "synthetic-a-b-2026-04-17", "tour": "SYNTHETIC", "tournament": "Synthetic Open",
         "players": ("Player Alpha", "Player Beta"), "aliases": (("Player Alpha", "Alpha"), ("Player Beta", "Beta"))}


def metadata():
    return {"slug": PILOT["slug"], "markets": [{"slug": PILOT["slug"], "sportsMarketType": "moneyline",
        "description": "Player Alpha plays Player Beta at the Synthetic Open on April 17, 2026.",
        "conditionId": "0x" + "a" * 64, "outcomes": '["Alpha","Beta"]',
        "clobTokenIds": '["0000001","0000002"]', "secondsDelay": 3}]}


def mapping():
    return market.resolve_event(metadata(), PILOT)


def row():
    return {"market": ("0x" + "a" * 64).encode(), "asset_id": "0000001", "event_type": "book",
            "timestamp": datetime(2026, 4, 17, 14, 0, 0, tzinfo=timezone.utc),
            "timestamp_received": datetime(2026, 4, 17, 14, 0, 1, tzinfo=timezone.utc),
            "bids": '[["0.40","10"],["0.42","5"]]', "asks": '[["0.45","7"],["0.50","12"]]'}


class TennisMarketTests(unittest.TestCase):
    def test_hosted_guard_rejects_local_before_network_initialization(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Local execution is disabled"):
                market.Fetcher()

    def test_exact_mapping_preserves_ids_and_does_not_export_rule_text(self):
        result = mapping()
        self.assertEqual(result["tokens"]["0000001"], "Player Alpha")
        self.assertNotIn("description", result)
        self.assertEqual(len(result["rules_sha256"]), 64)
        self.assertFalse(result["historical_delay_verified"])

    def test_duplicate_moneyline_or_wrong_tournament_rejected(self):
        event = metadata()
        event["markets"].append(copy.deepcopy(event["markets"][0]))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            market.resolve_event(event, PILOT)
        event = metadata()
        event["markets"][0]["description"] = "Player Alpha Player Beta April 17, 2026 other tournament"
        with self.assertRaises(ValueError):
            market.resolve_event(event, PILOT)

    def test_numeric_tokens_or_ambiguous_outcomes_rejected(self):
        for key, value in [("clobTokenIds", [1, 2]), ("outcomes", ["Yes", "No"]),
                           ("outcomes", ["Alpha", "Alpha"])]:
            event = metadata()
            event["markets"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                market.resolve_event(event, PILOT)

    def test_set_moneyline_is_not_match_moneyline(self):
        event = metadata()
        event["markets"][0]["sportsMarketType"] = "tennis_first_set_winner"
        with self.assertRaises(ValueError):
            market.resolve_event(event, PILOT)

    def test_snapshot_bbo_comes_from_actual_depth(self):
        data = row()
        data["best_bid"] = "0.99"
        output, snapshot = market.normalize_event(data, mapping(), "2026-04-17T14")
        self.assertEqual(output["native_best_bid"], Decimal("0.99"))
        self.assertEqual(snapshot["best_bid"], Decimal("0.42"))
        self.assertEqual(snapshot["best_ask_size"], Decimal("7"))
        self.assertEqual(snapshot["bid_total_size"], Decimal("15"))
        self.assertEqual(snapshot["source_to_receive_ms"], 1000)
        self.assertTrue(snapshot["two_sided_uncrossed"])

    def test_delta_or_trade_is_never_full_snapshot(self):
        for kind in ["price_change", "last_trade_price"]:
            data = row()
            data.update(event_type=kind, bids=None, asks=None, price="0.44", size="1")
            _, snapshot = market.normalize_event(data, mapping(), "2026-04-17T14")
            self.assertIsNone(snapshot)

    def test_naive_or_outside_hour_timestamps_rejected(self):
        for value in ["2026-04-17T14:00:00", "2026-04-17T15:00:00Z"]:
            data = row()
            data["timestamp_received"] = value
            with self.assertRaises(ValueError):
                market.normalize_event(data, mapping(), "2026-04-17T14")

    def test_negative_receive_lag_flagged_without_inventing_time(self):
        data = row()
        data["timestamp"] = "2026-04-17T14:00:02Z"
        output, snapshot = market.normalize_event(data, mapping(), "2026-04-17T14")
        self.assertEqual(output["source_to_receive_ms"], -1000)
        self.assertTrue(snapshot["negative_receive_lag"])

    def test_crossed_and_empty_books_not_usable_shape(self):
        self.assertFalse(market.snapshot_stats('[["0.6","1"]]', '[["0.5","1"]]')["two_sided_uncrossed"])
        self.assertFalse(market.snapshot_stats("[]", "[]")["two_sided_uncrossed"])
        with self.assertRaises(ValueError):
            market.snapshot_stats(None, "[]")

    def test_invalid_duplicate_or_nonfinite_depth_rejected(self):
        for bids in ['[["0.4","-1"]]', '[["1.4","2"]]', '[["NaN","2"]]',
                     '[["0.4","2"],["0.4","3"]]']:
            with self.subTest(bids=bids), self.assertRaises(ValueError):
                market.snapshot_stats(bids, "[]")

    def test_request_and_byte_limits_cannot_expand(self):
        budget = market.Budget()
        budget.admit(400_000_000, market.MAX_OBJECT_BYTES)
        with self.assertRaises(ValueError):
            budget.admit(400_000_001, market.MAX_OBJECT_BYTES)
        budget.received(390_000_000)
        with self.assertRaises(ValueError):
            budget.admit(110_000_001, market.MAX_OBJECT_BYTES)
        for _ in range(100):
            budget.request()
        with self.assertRaises(ValueError):
            budget.request()
        with self.assertRaises(ValueError):
            budget.received(110_000_001)
        self.assertEqual(budget.bytes, 390_000_000)

    def test_diagnostics_do_not_republish_arbitrary_response_text(self):
        self.assertEqual(market.failure_reason(ValueError("private synthetic row contents")), "ValueError")
        self.assertEqual(market.failure_reason(ValueError("HTTP_403")), "HTTP_403")


if __name__ == "__main__":
    unittest.main()
