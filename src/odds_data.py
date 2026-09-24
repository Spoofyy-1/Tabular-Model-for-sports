"""Collect bounded historical-odds research samples on GitHub-hosted Actions only.

This module performs no I/O when imported. It deliberately cannot establish
strategy profitability: neither source establishes historical fees, fillable
size, contemporaneous rule versions, or a complete market universe.

Run in a GitHub-hosted job:
    python src/odds_data.py --output-dir "$RUNNER_TEMP/sports-props-odds"

No credentials, account creation, trades, paid endpoints, or PrizePicks private
endpoints are used. Raw and derived data must remain in remote runner storage
or uploaded GitHub artifacts, never downloaded to the local workspace.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MAX_DOWNLOAD_BYTES = 20_000_000
MAX_TOTAL_DOWNLOAD_BYTES = 50_000_000
PARLAY_REPO = "JacobiusMakes/sports-odds-datasets"
PARLAY_PATH = "data/prop_closing_lines_sample_50k.csv"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SAMPLES = (
    {
        "series": "KXNBAPTS",
        "ticker": "KXNBAPTS-26JUN13NYKSAS-SASVWEMBANYAMA1-30",
        "league": "NBA",
        "player": "Victor Wembanyama",
        "stat": "points",
        "date": "2026-06-12",
    },
    {
        "series": "KXNFLPASSYDS",
        "ticker": "KXNFLPASSYDS-26FEB08SEANE-SEASDARNOLD14-300",
        "league": "NFL",
        "player": "Sam Darnold",
        "stat": "passing_yards",
        "date": "2026-02-07",
    },
)

STAT_MAP = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "threes",
    "player_pts_rebs": "points_rebounds",
    "player_pts_asts": "points_assists",
    "player_pts_rebs_asts": "points_rebounds_assists",
    "player_rebs_asts": "rebounds_assists",
    "player_pass_yards": "passing_yards",
    "player_pass_yds": "passing_yards",
    "player_rush_yards": "rushing_yards",
    "player_rec_yds": "receiving_yards",
    "player_receptions": "receptions",
}
CONVENTIONAL_BOOKS = {
    "fanduel", "draftkings", "betmgm", "caesars", "bet365", "fanatics",
    "betrivers", "espnbet", "hardrockbet", "pointsbet", "pinnacle", "bovada",
    "betonlineag", "williamhill_us",
}


def require_hosted_runner() -> None:
    if not (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    ):
        raise RuntimeError(
            "Dataset access is disabled locally. Run only on a GitHub-hosted "
            "Actions runner with GITHUB_ACTIONS=true and "
            "RUNNER_ENVIRONMENT=github-hosted."
        )


def validated_output_dir(value: str) -> Path:
    require_hosted_runner()
    target = Path(value).resolve()
    roots = [Path(os.environ[k]).resolve() for k in ("RUNNER_TEMP", "GITHUB_WORKSPACE")
             if os.environ.get(k)]
    if not any(target == root or root in target.parents for root in roots):
        raise ValueError("Output must be inside RUNNER_TEMP or GITHUB_WORKSPACE.")
    return target


class Downloader:
    def __init__(self) -> None:
        require_hosted_runner()
        self.total_bytes = 0
        self.requests = []

    def get(self, url: str) -> bytes:
        require_hosted_runner()
        request = Request(url, headers={"User-Agent": "sports-props-research/1.0"})
        # Do not retry denied requests through different hosts, proxies or VPNs.
        with urlopen(request, timeout=45) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Response exceeds the per-download bound.")
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
        self.total_bytes += len(body)
        if len(body) > MAX_DOWNLOAD_BYTES or self.total_bytes > MAX_TOTAL_DOWNLOAD_BYTES:
            raise ValueError("Download budget exceeded.")
        self.requests.append({
            "url": url, "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        })
        return body

    def json(self, url: str):
        return json.loads(self.get(url))


def timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        # Unzoned values cannot establish pregame information availability.
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def number(value):
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


def write_json(path: Path, value) -> None:
    require_hosted_runner()
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def write_jsonl(path: Path, rows) -> None:
    require_hosted_runner()
    with path.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")


def collect_parlay(downloader: Downloader, output: Path):
    require_hosted_runner()
    # Resolve a commit first so source contents remain reproducible.
    commit = downloader.json(f"https://api.github.com/repos/{PARLAY_REPO}/commits/main")["sha"]
    root = f"https://raw.githubusercontent.com/{PARLAY_REPO}/{commit}/"
    license_body = downloader.get(root + "LICENSE")
    readme_body = downloader.get(root + "README.md")
    csv_body = downloader.get(root + PARLAY_PATH)
    rows = list(csv.DictReader(io.StringIO(csv_body.decode("utf-8-sig"))))
    selected = [r for r in rows if r.get("sport_key") in {
        "basketball_nba", "americanfootball_nfl"}]
    (output / "PARLAY_LICENSE.txt").write_bytes(license_body)
    (output / "PARLAY_SOURCE_README.md").write_bytes(readme_body)
    write_jsonl(output / "parlay_nba_nfl_raw.jsonl", selected)
    candidates, audits = [], []
    timing = {"strictly_before_start": 0, "at_or_after_start": 0, "missing_or_invalid": 0}
    for index, row in enumerate(selected):
        observed, start = timestamp(row.get("snapshot_time")), timestamp(row.get("commence_time"))
        timing["missing_or_invalid" if not observed or not start else
               "strictly_before_start" if observed < start else "at_or_after_start"] += 1
        reasons = []
        if not observed or not start:
            reasons.append("missing_or_unzoned_timing")
        elif observed >= start:
            reasons.append("not_verified_pregame")
        if row.get("market_key") not in STAT_MAP:
            reasons.append("unsupported_or_non_player_market")
        if row.get("source") not in CONVENTIONAL_BOOKS:
            reasons.append("product_or_payout_requires_separate_verification")
        player = row.get("player_name", "").strip()
        if (len(player.split()) < 2 or any(c.isdigit() for c in player)
                or "(" in player or player.lower().startswith(("over ", "under ", "between "))
                or player in [row.get("home_team"), row.get("away_team")]):
            reasons.append("selection_not_verified_athlete")
        label = row.get("market_label", "").lower()
        if any(x in label for x in ("quarter", "half", "1q", "2q", "3q", "4q", "1h", "2h")):
            reasons.append("period_market_requires_separate_mapping")
        line = number(row.get("line"))
        if line is None:
            reasons.append("missing_threshold")
        # Summer, simulated, and pre-season events need a real schedule match.
        audits.append({"source_row_index": index, "rejection_reasons": reasons,
                       "official_schedule_and_player_join_verified": False})
        if reasons:
            continue
        for side in ("over", "under"):
            american = number(row.get(f"{side}_price"))
            if american is None or abs(american) < 100:
                continue
            decimal = 1 + (american / 100 if american > 0 else 100 / abs(american))
            candidates.append({
                "observation_time": observed.isoformat(),
                "event_time": start.isoformat(), "event": None,
                "event_label": f"{row['away_team']} at {row['home_team']}",
                "league": "NBA" if row["sport_key"] == "basketball_nba" else "NFL",
                "player": player, "player_id": None,
                "stat": STAT_MAP[row["market_key"]], "line": line, "side": side,
                "price": decimal, "price_format": "decimal_odds",
                "original_american_odds": american,
                "payout": decimal, "payout_unit": "gross_return_per_1_staked",
                "product": "sportsbook_player_prop_candidate", "rules": None,
                "source": row["source"], "provider": "ParlayAPI",
                "source_url": root + PARLAY_PATH, "source_row_index": index,
                "fee": None, "quoted_size": None,
                "official_schedule_and_player_join_verified": False,
                "historical_rules_verified": False, "usable_for_roi": False,
                "quality_status": "candidate_requires_schedule_player_product_and_rules_validation",
            })
    write_jsonl(output / "parlay_row_audit.jsonl", audits)
    return candidates, {
        "source_id": "parlayapi_ccby_sample", "commit": commit,
        "license": "CC-BY-4.0", "attribution": "ParlayAPI (https://parlay-api.com)",
        "full_sample_rows": len(rows), "nba_nfl_source_rows": len(selected),
        "timing": timing, "normalized_candidate_side_rows": len(candidates),
        "transformation": "NBA/NFL filter, timing and market screening, one row per side; American-to-decimal conversion; no implied-probability columns reused.",
    }


def collect_kalshi(downloader: Downloader, output: Path):
    require_hosted_runner()
    rows, summaries = [], []
    cutoff = downloader.json(KALSHI_BASE + "/historical/cutoff")
    write_json(output / "kalshi_historical_cutoff.json", cutoff)
    for spec in KALSHI_SAMPLES:
        ticker = spec["ticker"]
        market_url = KALSHI_BASE + "/historical/markets/" + ticker
        market_response = downloader.json(market_url)
        market = market_response["market"]
        start = int(datetime.fromisoformat(spec["date"]).replace(tzinfo=timezone.utc).timestamp())
        url = market_url + "/candlesticks?" + urlencode({
            "start_ts": start, "end_ts": start + 86400, "period_interval": 60})
        candles = downloader.json(url)
        write_json(output / (spec["series"] + "_market.json"), market_response)
        write_json(output / (spec["series"] + "_candles.json"), candles)
        for candle in candles.get("candlesticks", []):
            # Candle close is available only at interval end, never its start.
            observed = datetime.fromtimestamp(candle["end_period_ts"], timezone.utc)
            ask, bid = number(candle["yes_ask"].get("close")), number(candle["yes_bid"].get("close"))
            for side, price, derivation in (
                ("yes", ask, "yes_ask.close"),
                ("no", 1 - bid if bid is not None else None, "1 - yes_bid.close"),
            ):
                if price is None or not 0 < price < 1:
                    continue
                rows.append({
                    "observation_time": observed.isoformat(), "event_time": None,
                    "event": market.get("event_ticker"), "event_label": market.get("title"),
                    "league": spec["league"], "player": spec["player"], "player_id": None,
                    "stat": spec["stat"], "line": market.get("floor_strike"), "side": side,
                    "price": price, "price_format": "usd_per_contract",
                    "payout": 1, "payout_unit": "usd_per_winning_contract",
                    "product": "binary_event_contract", "rules": market.get("rules_primary"),
                    "source": "Kalshi", "source_url": url, "market_ticker": ticker,
                    "price_derivation": derivation, "interval_minutes": 60,
                    "fee": None, "quoted_size": None, "historical_rules_verified": False,
                    "official_schedule_and_player_join_verified": False,
                    "usable_for_roi": False,
                    "quality_status": "historical_quote_sample_missing_depth_fees_schedule_and_rule_version",
                })
        summaries.append({"ticker": ticker, "candles": len(candles.get("candlesticks", []))})
    return rows, {"source_id": "kalshi_public_history", "markets": summaries,
                  "redistribution_license": "not established; retain as private research artifact"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--include-kalshi", action="store_true", help="Optional research collector; review redistribution terms before public publishing")
    args = parser.parse_args()
    output = validated_output_dir(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    downloader = Downloader()
    all_rows, summaries, errors = [], [], []
    collectors = [collect_parlay]
    if args.include_kalshi:
        collectors.append(collect_kalshi)
    for collect in collectors:
        try:
            rows, summary = collect(downloader, output)
            all_rows.extend(rows)
            summaries.append(summary)
        except Exception as exc:
            errors.append({"collector": collect.__name__, "error": str(exc)})
    write_jsonl(output / "normalized_quote_candidates.jsonl", all_rows)
    manifest = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "historical-data feasibility samples; no profitability backtest",
        "training_cutoff_exclusive": "2025-01-01T00:00:00Z",
        "test_start_inclusive": "2025-01-01T00:00:00Z",
        "sources": summaries, "downloads": downloader.requests, "errors": errors,
        "total_downloaded_bytes": downloader.total_bytes,
        "normalized_rows": len(all_rows), "roi_eligible_rows": 0,
    }
    write_json(output / "sample_manifest.json", manifest)
    # Aggregate logs only. Do not emit datasets or quote rows to the task.
    print(json.dumps({k: manifest[k] for k in
                      ("total_downloaded_bytes", "normalized_rows", "roi_eligible_rows", "errors")}))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
