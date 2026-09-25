"""Synthetic activity aggregation tests; no file data or network calls."""
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from activity_data import Activity, ACTIVITY_FIELDS


def event(kind="last_trade_price", size="4", received="2026-04-17T14:00:02Z"):
    return {"event_slug": "synthetic-event", "condition_id": "synthetic-condition", "token_id": "0001",
            "outcome_player": "Synthetic Player", "source_time_utc": "2026-04-17T14:00:01Z",
            "received_time_utc": received, "event_type": kind, "price": "0.4", "size": size}


class ActivityTests(unittest.TestCase):
    def test_decimal_values_from_direct_arrow_pipeline_are_supported(self):
        audit = Activity()
        row = event()
        row.update(price=Decimal("0.4"), size=Decimal("4"))
        audit.event(row)
        self.assertEqual(list(audit.rows())[0]["observed_trade_size_sum_not_unique_volume"], Decimal(4))

    def test_price_change_sizes_are_never_trading_volume(self):
        audit = Activity()
        audit.event(event())
        audit.event(event("price_change", "10000"))
        result = list(audit.rows())[0]
        self.assertEqual(result["observed_trade_size_sum_not_unique_volume"], Decimal(4))
        self.assertEqual(result["observed_trade_price_times_size_sum_not_unique_turnover"], Decimal("1.6"))
        self.assertIsNone(result["venue_total_volume"])
        self.assertIsNone(result["unique_trade_count"])

    def test_identical_observations_deduplicate_but_do_not_claim_unique_trades(self):
        audit = Activity()
        audit.event(event())
        audit.event(event())
        result = list(audit.rows())[0]
        self.assertEqual(result["observed_last_trade_price_event_count"], 1)
        self.assertEqual(audit.audit["events_exact_duplicate_observations"], 1)
        self.assertTrue(result["potential_indistinguishable_trade_duplicates"])

    def test_receipt_minute_and_completed_minute_availability(self):
        audit = Activity()
        audit.event(event(received="2026-04-17T14:01:00Z"))
        result = list(audit.rows())[0]
        self.assertEqual(result["received_minute_utc"], "2026-04-17T14:01:00+00:00")
        self.assertEqual(result["feature_available_no_earlier_than_utc"], "2026-04-17T14:02:00+00:00")
        self.assertEqual(set(result), set(ACTIVITY_FIELDS))

    def test_missing_trade_size_is_not_invented(self):
        audit = Activity()
        audit.event(event(size=r"\N"))
        result = list(audit.rows())[0]
        self.assertEqual(result["trade_size_missing_event_count"], 1)
        self.assertIsNone(result["observed_trade_size_sum_not_unique_volume"])

    def test_snapshot_liquidity_uses_valid_spread_and_observed_depth(self):
        audit = Activity()
        source = event("book")
        source.update(best_bid="0.4", best_ask="0.45", bid_total_size="10", ask_total_size="5", bid_levels="2", ask_levels="1")
        audit.event(source)
        audit.snapshot(source)
        result = list(audit.rows())[0]
        self.assertEqual(result["snapshot_bid_depth_mean"], Decimal(10))
        self.assertEqual(result["uncrossed_spread_mean"], Decimal("0.05"))

    def test_naive_timestamp_rejected(self):
        audit = Activity()
        with self.assertRaises(ValueError):
            audit.event(event(received="2026-04-17T14:00:00"))


if __name__ == "__main__":
    unittest.main()
