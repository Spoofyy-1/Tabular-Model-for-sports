"""Cloud-only minute activity derivatives of the licensed PMXT pilot.

Observed messages are not unique executions or total market trading volume.
No MCP data, point alignment, model training or trading is performed.
"""
import argparse
from collections import Counter
import csv
from datetime import timedelta
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner
from market_data import utc_time, decimal, now

IDENTITY = ["event_slug", "condition_id", "token_id", "outcome_player"]
EVENT_REQUIRED = set(IDENTITY + ["received_time_utc", "source_time_utc", "event_type", "price", "size"])
SNAPSHOT_REQUIRED = set(IDENTITY + ["received_time_utc", "source_time_utc", "best_bid", "best_ask",
                         "bid_total_size", "ask_total_size", "bid_levels", "ask_levels"])
MAX_EVENTS, MAX_GROUPS = 2_000_000, 100_000
ACTIVITY_FIELDS = IDENTITY + ["received_minute_utc", "observed_event_count", "observed_book_event_count",
    "observed_price_change_event_count", "observed_last_trade_price_event_count", "observed_tick_size_change_event_count",
    "trade_size_missing_event_count", "negative_receive_lag_event_count", "observed_snapshot_count",
    "two_sided_uncrossed_snapshot_count", "crossed_snapshot_count", "observed_trade_size_sum_not_unique_volume",
    "observed_trade_price_times_size_sum_not_unique_turnover", "unique_trade_count", "venue_total_volume",
    "source_receive_lag_mean_ms", "source_receive_lag_max_ms", "snapshot_bid_depth_mean", "snapshot_ask_depth_mean",
    "snapshot_bid_levels_mean", "snapshot_ask_levels_mean", "uncrossed_spread_mean", "uncrossed_spread_min",
    "latest_observed_message_utc", "feature_available_no_earlier_than_utc", "stable_trade_message_id_available",
    "potential_indistinguishable_trade_duplicates"]


def present(value):
    return value not in (None, "", r"\N")


def moment(row):
    received = utc_time(row["received_time_utc"])
    source = utc_time(row["source_time_utc"])
    return received, (received - source).total_seconds() * 1000


def group_key(row):
    for key in IDENTITY:
        if not isinstance(row.get(key), str) or not present(row[key]):
            raise ValueError("Missing string market identity")
    received, _ = moment(row)
    return tuple(row[k] for k in IDENTITY) + (received.replace(second=0, microsecond=0).isoformat(),)


class Activity:
    def __init__(self):
        self.groups, self.seen = {}, {"events": set(), "snapshots": set()}
        self.audit = Counter()

    def accept(self, row, kind):
        """Hash identical observations; do not claim execution-ID deduplication."""
        if len(self.seen[kind]) >= MAX_EVENTS:
            raise ValueError("Activity observation bound exceeded")
        fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).digest()
        if fingerprint in self.seen[kind]:
            self.audit[kind + "_exact_duplicate_observations"] += 1
            return False
        self.seen[kind].add(fingerprint)
        return True

    def group(self, row):
        key = group_key(row)
        if key not in self.groups:
            if len(self.groups) >= MAX_GROUPS:
                raise ValueError("Activity minute bound exceeded")
            self.groups[key] = Counter()
        return self.groups[key]

    def event(self, row):
        kind = row["event_type"]
        if kind not in {"book", "price_change", "last_trade_price", "tick_size_change"}:
            raise ValueError("Unexpected PMXT event type")
        received, lag = moment(row)
        trade_size = decimal(row["size"]) if kind == "last_trade_price" and present(row.get("size")) else None
        trade_price = decimal(row["price"], True) if kind == "last_trade_price" and present(row.get("price")) else None
        group = self.group(row)
        if not self.accept(row, "events"):
            return
        group["observed_event_count"] += 1
        group["observed_" + kind + "_event_count"] += 1
        group["negative_receive_lag_event_count"] += int(lag < 0)
        group["lag_sum"] += lag
        group["lag_max"] = max(group.get("lag_max", lag), lag)
        group["latest_received"] = max(group.get("latest_received", received), received)
        if kind == "last_trade_price":
            group["trade_size_missing_event_count"] += int(trade_size is None)
            if trade_size is not None:
                group["trade_size_observed_event_count"] += 1
                group["observed_trade_size_sum_not_unique_volume"] += trade_size
            if trade_size is not None and trade_price is not None:
                group["trade_notional_observed_event_count"] += 1
                group["observed_trade_price_times_size_sum_not_unique_turnover"] += trade_price * trade_size
        self.audit["accepted_events"] += 1

    def snapshot(self, row):
        bid = decimal(row["best_bid"], True) if present(row.get("best_bid")) else None
        ask = decimal(row["best_ask"], True) if present(row.get("best_ask")) else None
        bid_depth, ask_depth = decimal(row["bid_total_size"]), decimal(row["ask_total_size"])
        bid_levels, ask_levels = decimal(row["bid_levels"]), decimal(row["ask_levels"])
        group = self.group(row)
        if not self.accept(row, "snapshots"):
            return
        group["observed_snapshot_count"] += 1
        group["bid_depth_sum"] += bid_depth
        group["ask_depth_sum"] += ask_depth
        group["bid_level_sum"] += bid_levels
        group["ask_level_sum"] += ask_levels
        valid = bid is not None and ask is not None and bid <= ask
        group["two_sided_uncrossed_snapshot_count"] += int(valid)
        group["crossed_snapshot_count"] += int(bid is not None and ask is not None and bid > ask)
        if valid:
            spread = ask - bid
            group["spread_sum"] += spread
            group["spread_min"] = min(group.get("spread_min", spread), spread)
        self.audit["accepted_snapshots"] += 1

    def rows(self):
        for key, group in sorted(self.groups.items()):
            out = dict(zip(IDENTITY + ["received_minute_utc"], key))
            n, snapshots, valid = group["observed_event_count"], group["observed_snapshot_count"], group["two_sided_uncrossed_snapshot_count"]
            for field in ["observed_event_count", "observed_book_event_count", "observed_price_change_event_count",
                          "observed_last_trade_price_event_count", "observed_tick_size_change_event_count",
                          "trade_size_missing_event_count", "negative_receive_lag_event_count", "observed_snapshot_count",
                          "two_sided_uncrossed_snapshot_count", "crossed_snapshot_count"]:
                out[field] = group[field]
            trades = group["observed_last_trade_price_event_count"]
            out.update({"observed_trade_size_sum_not_unique_volume": group["observed_trade_size_sum_not_unique_volume"] if not trades or group["trade_size_observed_event_count"] else None,
                "observed_trade_price_times_size_sum_not_unique_turnover": group["observed_trade_price_times_size_sum_not_unique_turnover"] if not trades or group["trade_notional_observed_event_count"] else None,
                "unique_trade_count": None, "venue_total_volume": None,
                "source_receive_lag_mean_ms": group["lag_sum"] / n if n else None,
                "source_receive_lag_max_ms": group["lag_max"] if n else None,
                "snapshot_bid_depth_mean": group["bid_depth_sum"] / snapshots if snapshots else None,
                "snapshot_ask_depth_mean": group["ask_depth_sum"] / snapshots if snapshots else None,
                "snapshot_bid_levels_mean": group["bid_level_sum"] / snapshots if snapshots else None,
                "snapshot_ask_levels_mean": group["ask_level_sum"] / snapshots if snapshots else None,
                "uncrossed_spread_mean": group["spread_sum"] / valid if valid else None,
                "uncrossed_spread_min": group["spread_min"] if valid else None,
                "latest_observed_message_utc": group["latest_received"].isoformat() if n else None,
                "feature_available_no_earlier_than_utc": (utc_time(key[-1]) + timedelta(minutes=1)).isoformat(),
                "stable_trade_message_id_available": False,
                "potential_indistinguishable_trade_duplicates": True})
            yield out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    args = parser.parse_args()
    require_github_hosted_runner()
    directory = args.input_dir.resolve()
    if not directory.is_relative_to(Path(os.environ["GITHUB_WORKSPACE"]).resolve()):
        raise RuntimeError("Input/output must remain on hosted workspace")
    summary = json.loads((directory / "market_summary.json").read_text())
    if summary.get("license") != "CC-BY-4.0" or summary.get("point_data_joined") is not False:
        raise ValueError("Only the separate PMXT CC BY pilot is supported")
    activity, sources = Activity(), []
    for name, required, handler in [("pmxt_events.csv.gz", EVENT_REQUIRED, activity.event),
                                     ("pmxt_snapshots.csv.gz", SNAPSHOT_REQUIRED, activity.snapshot)]:
        path = directory / name
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        sources.append({"file": name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
        with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("Activity input schema is incomplete")
            for row in reader:
                handler(row)
    rows = list(activity.rows())
    output = directory / "pmxt_activity_minutes.csv.gz"
    # Empty pilots retain the same column contract as successful collection.
    fields = ACTIVITY_FIELDS
    with gzip.open(output, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: r"\N" if v is None else v for k, v in row.items()})
    report = {"created_at_utc": now(), "license": "CC-BY-4.0", "attribution": "PMXT (pmxt.dev)",
        "minute_rows": len(rows), "counts": dict(activity.audit), "sources": sources, "columns": fields,
        "aggregation_clock": "Collector receipt UTC minute; use no earlier than the end of that minute",
        "deduplication": "Identical normalized observations hashed; no stable trade/message ID in pilot export",
        "volume_interpretation": "Observed last_trade_price event sizes only; potential indistinguishable duplicates, missing events and incomplete venue coverage",
        "absent_minutes": "Unknown coverage, never automatically zero activity",
        "point_data_joined": False, "historical_feature_complete": False, "betting_roi_evaluated": False}
    (directory / "activity_summary.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"minute_rows": len(rows), "counts": dict(activity.audit), "license": "CC-BY-4.0"}), flush=True)


if __name__ == "__main__":
    main()
