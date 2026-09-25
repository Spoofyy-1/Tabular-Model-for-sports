"""Bounded PMXT tennis quote audit; genuine GitHub-hosted runners only.

No point-data imports, trading, profitability simulation or local sports data.
Only PMXT CC BY 4.0 derivatives and minimal venue identity metadata are exported.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

HOURS = ("2026-04-17T14", "2026-04-17T15")
MAX_BYTES, MAX_OBJECT_BYTES, MAX_REQUESTS = 500_000_000, 390_000_000, 100
LICENSE = "CC-BY-4.0"
PILOTS = (
    {"slug": "atp-fils-musetti-2026-04-17", "tour": "ATP", "tournament": "Barcelona Open",
     "players": ("Arthur Fils", "Lorenzo Musetti"),
     "aliases": (("Arthur Fils", "Fils", "A. Fils"), ("Lorenzo Musetti", "Musetti", "L. Musetti"))},
    {"slug": "wta-swiatek-andreev-2026-04-17", "tour": "WTA", "tournament": "Porsche Tennis Grand Prix",
     "players": ("Iga Swiatek", "Mirra Andreeva"),
     "aliases": (("Iga Swiatek", "Swiatek", "I. Swiatek"), ("Mirra Andreeva", "Andreeva", "M. Andreeva"))},
)
FIELDS = ["archive_hour_utc", "event_slug", "condition_id", "token_id", "outcome_player",
          "event_type", "source_time_utc", "received_time_utc", "source_to_receive_ms",
          "price", "size", "side", "native_best_bid", "native_best_ask", "native_fee_rate_bps"]
SNAPSHOT_FIELDS = FIELDS[:9] + ["best_bid", "best_ask", "best_bid_size", "best_ask_size",
                  "bid_levels", "ask_levels", "bid_total_size", "ask_total_size",
                  "spread", "two_sided_uncrossed", "negative_receive_lag"]
REQUIRED = {"market", "asset_id", "event_type", "timestamp", "timestamp_received", "bids", "asks"}
OPTIONAL = {"price", "size", "side", "best_bid", "best_ask", "fee_rate_bps"}


def now():
    return datetime.now(timezone.utc).isoformat()


def failure_reason(exc):
    # Only our bounded messages; never publish remote response bodies/row values.
    message = str(exc)
    prefixes = ("HTTP_", "Object exceeds", "Request budget", "Download byte", "Archive schema",
                "Archive token", "Archive exceeds", "HEAD/GET", "Incomplete archive", "Expected exactly",
                "Event slug", "Ambiguous outcome", "Invalid condition", "Unverified player",
                "Outcome identity", "Metadata object", "Pilot matching", "Token belongs")
    return message[:160] if message.startswith(prefixes) else type(exc).__name__


def norm(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.lower())


def json_list(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, list):
        raise ValueError("Expected metadata array")
    return value


def resolve_event(event, pilot):
    """Exact reviewed event identity + explicit outcome aliases; never fuzzy."""
    if not isinstance(event, dict) or event.get("slug") != pilot["slug"]:
        raise ValueError("Event slug mismatch")
    candidates = []
    for market in event.get("markets", []):
        if not isinstance(market, dict):
            continue
        kind = market.get("sportsMarketType")
        if kind != "moneyline" and not (kind in (None, "") and market.get("slug") == pilot["slug"]):
            continue
        rules = market.get("description") or event.get("description") or ""
        text = norm(rules)
        if not all(norm(p) in text for p in pilot["players"]) or norm(pilot["tournament"]) not in text:
            continue
        # Rule date, not endDate/expiry: both reviewed examples have later expiry.
        if not ("april172026" in text or "20260417" in text):
            continue
        outcomes, tokens = json_list(market.get("outcomes")), json_list(market.get("clobTokenIds"))
        condition = market.get("conditionId", "")
        if len(outcomes) != 2 or len(tokens) != 2 or len(set(tokens)) != 2:
            raise ValueError("Ambiguous outcome/token array")
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", condition):
            raise ValueError("Invalid condition ID")
        players = []
        for label in outcomes:
            matches = [pilot["players"][i] for i, aliases in enumerate(pilot["aliases"])
                       if norm(label) in {norm(a) for a in aliases}]
            if len(matches) != 1:
                raise ValueError("Outcome identity is not uniquely verified")
            players.append(matches[0])
        if set(players) != set(pilot["players"]) or any(not isinstance(t, str) or not re.fullmatch(r"[0-9]{1,80}", t) for t in tokens):
            raise ValueError("Unverified player mapping or non-string token")
        candidates.append({"event_slug": pilot["slug"], "tour": pilot["tour"],
            "tournament": pilot["tournament"], "match_date": "2026-04-17", "condition_id": condition.lower(),
            "tokens": dict(zip(tokens, players)), "rules_url": "https://polymarket.com/event/" + pilot["slug"],
            "rules_sha256": hashlib.sha256(rules.encode()).hexdigest(),
            "metadata_observed_at_utc": now(), "current_seconds_delay": market.get("secondsDelay"),
            "historical_delay_verified": False, "point_identity_join_status": "not_attempted"})
    if len(candidates) != 1:
        raise ValueError("Expected exactly one verified match-moneyline contract")
    return candidates[0]


def utc_time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, int) and not isinstance(value, bool):
        result = datetime.fromtimestamp(value / 1000, timezone.utc)
    else:
        raise ValueError("Unrecognized source timestamp")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Naive timestamps are not accepted")
    result = result.astimezone(timezone.utc)
    if not 2020 <= result.year <= 2100:
        raise ValueError("Timestamp outside supported range")
    return result


def decimal(value, price=False):
    if isinstance(value, bool):
        raise ValueError("Boolean numeric value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid numeric quote") from None
    if not result.is_finite() or result < 0 or (price and result > 1):
        raise ValueError("Quote outside supported bounds")
    return result


def levels(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or len(value) > 20000:
        raise ValueError("Missing or oversized book snapshot")
    result = {}
    for item in value:
        if isinstance(item, dict):
            price, size = item.get("price"), item.get("size")
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            price, size = item
        else:
            raise ValueError("Unknown snapshot level shape")
        price, size = decimal(price, True), decimal(size)
        if price in result:
            raise ValueError("Duplicate price levels")
        if size > 0:
            result[price] = size
    return result


def snapshot_stats(bids, asks):
    bids, asks = levels(bids), levels(asks)
    bid, ask = max(bids) if bids else None, min(asks) if asks else None
    return {"best_bid": bid, "best_ask": ask,
        "best_bid_size": bids.get(bid), "best_ask_size": asks.get(ask),
        "bid_levels": len(bids), "ask_levels": len(asks),
        "bid_total_size": sum(bids.values(), Decimal(0)), "ask_total_size": sum(asks.values(), Decimal(0)),
        "spread": ask - bid if bid is not None and ask is not None else None,
        "two_sided_uncrossed": bid is not None and ask is not None and bid <= ask}


def normalize_event(row, mapping, hour):
    condition = row.get("market")
    if isinstance(condition, bytes):
        condition = condition.decode("ascii")
    if not isinstance(condition, str) or condition.lower() != mapping["condition_id"]:
        raise ValueError("Condition mismatch")
    token = row.get("asset_id")
    if not isinstance(token, str) or token not in mapping["tokens"]:
        raise ValueError("Token mismatch")
    event_type = row.get("event_type")
    if event_type not in {"book", "price_change", "last_trade_price", "tick_size_change"}:
        raise ValueError("Unknown event type")
    source, received = utc_time(row.get("timestamp")), utc_time(row.get("timestamp_received"))
    start = utc_time(hour + ":00:00Z")
    if not start <= received < start + timedelta(hours=1):
        raise ValueError("Receipt timestamp outside archive hour")
    lag = (received - source).total_seconds() * 1000
    output = {"archive_hour_utc": hour, "event_slug": mapping["event_slug"],
        "condition_id": mapping["condition_id"], "token_id": token, "outcome_player": mapping["tokens"][token],
        "event_type": event_type, "source_time_utc": source.isoformat(), "received_time_utc": received.isoformat(),
        "source_to_receive_ms": lag, "side": row.get("side")}
    for source_key, destination in [("price", "price"), ("size", "size"), ("best_bid", "native_best_bid"),
                                     ("best_ask", "native_best_ask"), ("fee_rate_bps", "native_fee_rate_bps")]:
        value = row.get(source_key)
        output[destination] = None if value is None else decimal(value, source_key in {"price", "best_bid", "best_ask"})
    snapshot = None
    if event_type == "book":
        snapshot = {k: output[k] for k in FIELDS[:9]}
        snapshot.update(snapshot_stats(row.get("bids"), row.get("asks")))
        snapshot["negative_receive_lag"] = lag < 0
    return output, snapshot


class Budget:
    def __init__(self):
        self.bytes, self.requests = 0, 0

    def request(self):
        if self.requests >= MAX_REQUESTS:
            raise ValueError("Request budget exhausted")
        self.requests += 1

    def admit(self, size, limit):
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= limit or self.bytes + size > MAX_BYTES:
            raise ValueError("Object exceeds remaining download budget")

    def received(self, size):
        if size < 0 or self.bytes + size > MAX_BYTES:
            raise ValueError("Download byte budget exhausted")
        self.bytes += size


class Fetcher:
    def __init__(self):
        require_github_hosted_runner()
        import requests
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "sports-props-tennis-quote-audit/1", "Accept-Encoding": "identity"})
        self.budget, self.sources = Budget(), []

    def response(self, method, url):
        require_github_hosted_runner()
        self.budget.request()
        response = self.session.request(method, url, stream=True, timeout=(15, 90), allow_redirects=False)
        if response.status_code != 200:
            code = response.status_code
            response.close()
            raise ValueError("HTTP_%d" % code)
        return response

    def metadata(self, slug):
        url = "https://gamma-api.polymarket.com/events/slug/" + slug
        with self.response("GET", url) as response:
            data = bytearray()
            for block in response.iter_content(65536):
                self.budget.received(len(block))
                data.extend(block)
                if len(data) > 5_000_000:
                    raise ValueError("Metadata object too large")
        self.sources.append({"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                             "kind": "identity_metadata_not_raw_redistributed"})
        return json.loads(data)

    def archive(self, hour, directory):
        url = "https://r2v2.pmxt.dev/polymarket_orderbook_" + hour + ".parquet"
        with self.response("HEAD", url) as response:
            size = int(response.headers.get("Content-Length", "0"))
        self.budget.admit(size, MAX_OBJECT_BYTES)
        path = directory / (hour + ".parquet")
        digest, received = hashlib.sha256(), 0
        try:
            with self.response("GET", url) as response:
                if int(response.headers.get("Content-Length", "0")) != size:
                    raise ValueError("HEAD/GET size mismatch")
                with path.open("wb") as output:
                    for block in response.iter_content(65536):
                        received += len(block)
                        self.budget.received(len(block))
                        if received > size:
                            raise ValueError("Archive exceeds declared size")
                        digest.update(block)
                        output.write(block)
            if received != size:
                raise ValueError("Incomplete archive transfer")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.sources.append({"url": url, "bytes": received, "sha256": digest.hexdigest(),
                             "license": LICENSE, "kind": "pmxt_archive"})
        return path


def process_archive(path, mappings, hour, event_writer, snapshot_writer):
    require_github_hosted_runner()
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    parquet = pq.ParquetFile(path)
    names = set(parquet.schema_arrow.names)
    if not REQUIRED.issubset(names):
        raise ValueError("Archive schema lacks required snapshot fields")
    by_token = {token: mapping for mapping in mappings for token in mapping["tokens"]}
    if len(by_token) != sum(len(m["tokens"]) for m in mappings):
        raise ValueError("Token belongs to multiple pilot markets")
    counts = Counter()
    previous, seen = {}, set()
    for batch in parquet.iter_batches(batch_size=65536, columns=sorted(REQUIRED | (OPTIONAL & names))):
        counts["scanned_rows"] += batch.num_rows
        token_column = batch.column(batch.schema.get_field_index("asset_id"))
        if not (pa.types.is_string(token_column.type) or pa.types.is_large_string(token_column.type)):
            raise ValueError("Archive token IDs must be strings")
        selected = batch.filter(pc.is_in(token_column, value_set=pa.array(list(by_token), type=token_column.type)))
        for row in selected.to_pylist():
            counts["matching_token_rows"] += 1
            try:
                output, snapshot = normalize_event(row, by_token[row["asset_id"]], hour)
            except (ValueError, TypeError, UnicodeError, KeyError):
                counts["rejected_rows"] += 1
                continue
            key = tuple(str(output.get(k)) for k in FIELDS) + (str(row.get("bids")), str(row.get("asks")))
            fingerprint = hashlib.sha256(repr(key).encode()).digest()
            if fingerprint in seen:
                counts["duplicate_rows"] += 1
                continue
            if len(seen) >= 2_000_000:
                raise ValueError("Pilot matching-event safety limit reached")
            seen.add(fingerprint)
            token, received = output["token_id"], utc_time(output["received_time_utc"])
            if token in previous:
                gap = (received - previous[token]).total_seconds()
                counts["receive_order_reversals"] += int(gap < 0)
                counts["event_gaps_over_60_seconds"] += int(gap > 60)
            previous[token] = received
            counts["negative_receive_lag"] += int(output["source_to_receive_ms"] < 0)
            counts["events_" + output["event_type"]] += 1
            counts["exported_events"] += 1
            event_writer.writerow({k: (r"\N" if output.get(k) is None else output[k]) for k in FIELDS})
            if snapshot is not None:
                snapshot_writer.writerow({k: (r"\N" if snapshot.get(k) is None else snapshot[k]) for k in SNAPSHOT_FIELDS})
                counts["snapshots"] += 1
                counts["two_sided_uncrossed_snapshots"] += int(snapshot["two_sided_uncrossed"])
    return {"hour": hour, "status": "processed", "counts": dict(counts),
            "source_schema": {field.name: str(field.type) for field in parquet.schema_arrow},
            "gap_interpretation": "Observed silence, not proof of missing updates; no book carried across gaps"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/tennis/pmxt_market_research")
    args = parser.parse_args()
    require_github_hosted_runner()
    output = args.output_dir.resolve()
    if not output.is_relative_to(Path(os.environ["GITHUB_WORKSPACE"]).resolve()):
        raise RuntimeError("Outputs must stay inside the hosted workspace")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("Use an empty output directory")
    fetcher, mappings, diagnostics, hours = Fetcher(), [], [], []
    for pilot in PILOTS:
        try:
            mappings.append(resolve_event(fetcher.metadata(pilot["slug"]), pilot))
        except Exception as exc:
            diagnostics.append({"stage": "identity", "event_slug": pilot["slug"], "error_type": type(exc).__name__, "reason": failure_reason(exc)})
    with gzip.open(output / "pmxt_events.csv.gz", "wt", encoding="utf-8", newline="") as events, \
            gzip.open(output / "pmxt_snapshots.csv.gz", "wt", encoding="utf-8", newline="") as snapshots:
        event_writer, snapshot_writer = csv.DictWriter(events, FIELDS), csv.DictWriter(snapshots, SNAPSHOT_FIELDS)
        event_writer.writeheader()
        snapshot_writer.writeheader()
        if mappings:
            with tempfile.TemporaryDirectory(prefix="tennis-pmxt-", dir=os.environ["RUNNER_TEMP"]) as directory:
                for hour in HOURS:
                    path = None
                    try:
                        path = fetcher.archive(hour, Path(directory))
                        # Stage each hour so a processing failure cannot publish
                        # uncounted partial CSV rows under an audit-only status.
                        event_part, snapshot_part = Path(directory) / "events.csv.gz", Path(directory) / "snapshots.csv.gz"
                        with gzip.open(event_part, "wt", encoding="utf-8", newline="") as event_stream, \
                                gzip.open(snapshot_part, "wt", encoding="utf-8", newline="") as snapshot_stream:
                            report = process_archive(path, mappings, hour, csv.DictWriter(event_stream, FIELDS),
                                                     csv.DictWriter(snapshot_stream, SNAPSHOT_FIELDS))
                        with gzip.open(event_part, "rt", encoding="utf-8", newline="") as source:
                            shutil.copyfileobj(source, events)
                        with gzip.open(snapshot_part, "rt", encoding="utf-8", newline="") as source:
                            shutil.copyfileobj(source, snapshots)
                        hours.append(report)
                    except Exception as exc:
                        hours.append({"hour": hour, "status": "unavailable_or_excluded", "error_type": type(exc).__name__, "reason": failure_reason(exc)})
                    finally:
                        if path is not None:
                            path.unlink(missing_ok=True)
    fetcher.session.close()
    counts = Counter()
    for hour in hours:
        counts.update(hour.get("counts", {}))
    summary = {"created_at_utc": now(), "status": "quotes_collected" if counts.get("exported_events", 0) else "coverage_audit_only",
        "pilot_events_requested": len(PILOTS), "verified_moneyline_markets": len(mappings), "requested_hours": list(HOURS),
        "hours": hours, "counts": dict(counts), "diagnostics": diagnostics,
        "source_bytes": fetcher.budget.bytes, "request_count": fetcher.budget.requests,
        "limits": {"total_bytes": MAX_BYTES, "object_bytes": MAX_OBJECT_BYTES, "requests": MAX_REQUESTS},
        "license": LICENSE, "attribution": "PMXT (pmxt.dev), CC BY 4.0",
        "point_clock_status": "not_available", "point_data_joined": False,
        "execution_backtest_status": "blocked_missing_point_clock", "betting_roi_evaluated": False,
        "fees_and_historical_delay_verified": False, "market_state_at_quote_verified": False,
        "asof_status": "Quotes retain source/receive times; current identity metadata is not a historical rule/fee snapshot"}
    def write_json(name, value):
        (output / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    write_json("market_map.json", {"markets": mappings})
    write_json("market_summary.json", summary)
    write_json("source_manifest.json", {"license": LICENSE, "attribution": "PMXT", "sources": fetcher.sources,
        "changes": "Exact market filtering, normalized event CSV and snapshot price/depth summaries; no point-data join"})
    write_json("schema.json", {"csv_null_encoding": r"\N", "id_columns_are_strings": True,
        "pmxt_events.csv.gz": FIELDS, "pmxt_snapshots.csv.gz": SNAPSHOT_FIELDS,
        "native_fee_rate_bps": "Nullable trade-event field; not a complete historical fee schedule",
        "two_sided_uncrossed": "Book shape check only, not a fill or profitability determination"})
    (output / "LICENSE.txt").write_text("PMXT archive derivatives: Creative Commons Attribution 4.0 International. Attribution: PMXT (https://pmxt.dev). Source: https://archive.pmxt.dev/Polymarket/v2 . License: https://creativecommons.org/licenses/by/4.0/ . Changes: exact tennis moneyline filtering, event normalization and book statistics. Venue metadata is restricted to identifiers, rule URLs/hashes and current delay diagnostics; raw rule text is not redistributed. This archive contains no Match Charting Project data.\n")
    print(json.dumps({"status": summary["status"], "verified_markets": len(mappings), "counts": dict(counts),
                      "source_bytes": fetcher.budget.bytes, "betting_roi_evaluated": False}), flush=True)


if __name__ == "__main__":
    main()
