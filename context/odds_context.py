"""Audit historical prop identity/timing on GitHub-hosted runners only.

No training, bet selection, wagers, or local dataset access. Public outputs
contain licensed ParlayAPI derivatives and aggregate Kalshi coverage only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import unicodedata
from urllib.parse import urlencode

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
REPO = "Spoofyy-1/Tabular-Model-for-sports"
DEFAULT_RELEASE = "snapshot-36055555015-1"
CUTOFF = pd.Timestamp("2025-01-01", tz="UTC")
MAX_BYTES = 500_000_000
MAX_REQUESTS = 1000
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNFLPASSYDS", "KXNFLRSHYDS", "KXNFLRECYDS")
STATS = {"points", "rebounds", "assists", "threes", "points_rebounds",
         "points_assists", "points_rebounds_assists", "rebounds_assists",
         "passing_yards", "rushing_yards", "receiving_yards", "receptions"}

NBA_NAMES = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}
NFL_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def require_hosted():
    if not (os.environ.get("GITHUB_ACTIONS") == "true" and
            os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"):
        raise RuntimeError("Real data is restricted to GitHub-hosted Actions; local execution is disabled.")
    if not os.environ.get("GITHUB_WORKSPACE"):
        raise RuntimeError("GITHUB_WORKSPACE is required.")


def norm(value):
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def team(value, league):
    names = NBA_NAMES if league == "NBA" else NFL_NAMES
    mapping = {}
    for key, label in names.items():
        mapping[norm(key)] = key
        mapping[norm(label)] = key
        # Explicit abbreviations + nicknames, not fuzzy string matching.
        mapping[norm(key + " " + label.split()[-1])] = key
    aliases = ({"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA",
                "PHO": "PHX", "LA Lakers": "LAL", "LA Clippers": "LAC"}
               if league == "NBA" else
               {"LA": "LAR", "WSH": "WAS", "JAC": "JAX", "Washington Football Team": "WAS"})
    mapping.update({norm(k): v for k, v in aliases.items()})
    return mapping.get(norm(value))


def timestamp(value):
    if value is None or str(value) in ("", "NaT", "None", "nan"):
        return None
    try:
        result = pd.Timestamp(value)
        return result.tz_convert("UTC") if result.tzinfo else None
    except (TypeError, ValueError):
        return None


class Fetcher:
    def __init__(self):
        require_hosted()
        self.bytes = 0
        self.calls = 0
        self.provenance = []

    def get(self, url, max_bytes=120_000_000):
        require_hosted()
        if self.calls >= MAX_REQUESTS:
            raise RuntimeError("Request budget exhausted")
        self.calls += 1
        chunks, received = [], 0
        # Only ordinary public read requests; never retry denied access via a bypass.
        with requests.get(url, headers={"User-Agent": "sports-props-context-audit/1.0"},
                          timeout=(15, 60), stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(1024 * 1024):
                received += len(chunk)
                self.bytes += len(chunk)
                if received > max_bytes or self.bytes > MAX_BYTES:
                    raise RuntimeError("Remote download budget exceeded")
                chunks.append(chunk)
        body = b"".join(chunks)
        self.provenance.append({"url": url, "bytes": len(body),
                                "sha256": hashlib.sha256(body).hexdigest()})
        return body

    def json(self, url):
        return json.loads(self.get(url, max_bytes=30_000_000))


def archive_members(body, wanted):
    require_hosted()
    found = {}
    # Never extract arbitrary paths or model artifacts from an archive.
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive.getmembers():
            key = Path(member.name).name
            if key not in wanted:
                continue
            if not member.isfile() or member.size > 200_000_000 or key in found:
                raise ValueError("Unexpected or ambiguous archive member: " + key)
            with archive.extractfile(member) as stream:
                found[key] = stream.read()
    missing = set(wanted) - found.keys()
    if missing:
        raise ValueError("Missing archive members: " + repr(sorted(missing)))
    return found


def load_release(fetcher, tag):
    require_hosted()
    meta = fetcher.json(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}")
    assets = {a["name"]: a for a in meta["assets"]}
    tables = {}
    for sport, schedule_name in (("nba", "games.parquet"), ("nfl", "schedules.parquet")):
        asset = assets[f"dataset-{sport}.tar.gz"]
        if asset["size"] > 120_000_000:
            raise ValueError("Release asset exceeds bounded sample scope")
        members = archive_members(fetcher.get(asset["browser_download_url"]),
                                  {"player_games.parquet", schedule_name})
        tables[sport] = {
            "players": pd.read_parquet(io.BytesIO(members["player_games.parquet"])),
            "games": pd.read_parquet(io.BytesIO(members[schedule_name])),
        }
    odds = archive_members(fetcher.get(assets["historical-odds-audit.tar.gz"]["browser_download_url"]),
                           {"normalized_quote_candidates.jsonl", "parlay_nba_nfl_raw.jsonl", "PARLAY_LICENSE.txt"})
    quotes = [json.loads(x) for x in odds["normalized_quote_candidates.jsonl"].splitlines() if x.strip()]
    raw = [json.loads(x) for x in odds["parlay_nba_nfl_raw.jsonl"].splitlines() if x.strip()]
    return tables, quotes, raw, odds["PARLAY_LICENSE.txt"]


def prepare_tables(tables):
    for sport, entries in tables.items():
        games, players = entries["games"].copy(), entries["players"].copy()
        games["game_id"] = games.game_id.astype(str)
        players["game_id"] = players.game_id.astype(str)
        players["player_id"] = players.player_id.astype(str)
        games["audit_start"] = pd.to_datetime(games.game_date, utc=True, errors="coerce")
        games["audit_home"] = games.home_team.map(lambda v: team(v, sport.upper()))
        games["audit_away"] = games.away_team.map(lambda v: team(v, sport.upper()))
        players["audit_name"] = players.player_name.map(norm)
        entries["games"], entries["players"] = games, players


def audit_quotes(tables, quotes, raw, tolerance_seconds=900, minimum_lead_seconds=60):
    """Pure transformation; synthetic inputs may be used for local unit checks."""
    prepare_tables(tables)
    audits = []
    for quote_index, q in enumerate(quotes):
        reasons = []
        league = q.get("league", "").upper()
        raw_index = q.get("source_row_index")
        source = raw[raw_index] if isinstance(raw_index, int) and 0 <= raw_index < len(raw) else {}
        observed, source_start = timestamp(q.get("observation_time")), timestamp(q.get("event_time"))
        audit = dict(q, quote_index=quote_index, matched_game_id=None, matched_player_id=None,
                     matched_event_time=None, event_time_difference_seconds=None,
                     pricing_audit_status="quarantined", roi_eligible=False, usable_for_roi=False,
                     mapping_uses_retrospective_identity_only=True,
                     feature_asof_verified=False, rules_fees_depth_verified=False)
        if league.lower() not in tables:
            reasons.append("unsupported_league")
        if not source:
            reasons.append("missing_raw_source_row")
        if q.get("stat") not in STATS:
            reasons.append("unsupported_stat_or_period")
        if not observed or not source_start:
            reasons.append("missing_or_unzoned_source_time")
        home, away = team(source.get("home_team"), league), team(source.get("away_team"), league)
        if home is None or away is None:
            reasons.append("unmapped_team")
        if not reasons:
            entries = tables[league.lower()]
            games = entries["games"]
            candidates = games[(games.audit_home == home) & (games.audit_away == away) &
                               ((games.audit_start - source_start).abs().dt.total_seconds() <= tolerance_seconds)]
            if len(candidates) != 1:
                reasons.append("no_unique_schedule_match")
            else:
                game = candidates.iloc[0]
                matched_start = game.audit_start
                audit.update(matched_game_id=str(game.game_id), matched_event_time=matched_start.isoformat(),
                             event_time_difference_seconds=(source_start - matched_start).total_seconds())
                if "game_date_time_known" in game and not bool(game.game_date_time_known):
                    reasons.append("date_only_schedule")
                earlier_start = min(matched_start, source_start)
                if (observed >= earlier_start or
                        observed > earlier_start - pd.Timedelta(seconds=minimum_lead_seconds)):
                    reasons.append("not_before_both_start_times_with_buffer")
                players = entries["players"]
                matches = players[(players.game_id == str(game.game_id)) &
                                  (players.audit_name == norm(q.get("player")))]
                ids = matches.player_id.dropna().unique()
                if len(ids) != 1 or ids[0] in ("nan", "<NA>", "None"):
                    reasons.append("no_unique_game_player_identity")
                else:
                    audit["matched_player_id"] = str(ids[0])
                audit["temporal_partition"] = "development_before_2025" if matched_start < CUTOFF else "holdout_2025_onward"
                audit["lead_time_seconds"] = (matched_start - observed).total_seconds()
        # A source market key is evidence of intended stat; historical book rules
        # and full-game/OT eligibility still require a separate pricing audit.
        audit["exclusion_reasons"] = json.dumps(reasons)
        if not reasons:
            audit["pricing_audit_status"] = "eligible_for_further_pricing_audit"
        audits.append(audit)
    return audits


def kalshi_coverage(fetcher, pages):
    """Aggregate only: never persist raw exchange metadata or rule text."""
    require_hosted()
    result = []
    for series in SERIES:
        cursor, seen, months, open_dates, errors = "", set(), Counter(), [], []
        complete = False
        for _ in range(pages):
            params = {"series_ticker": series, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                body = fetcher.json(KALSHI_BASE + "/historical/markets?" + urlencode(params))
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                break
            for market in body.get("markets", []):
                ticker = market.get("ticker")
                if not ticker or ticker in seen:
                    continue
                seen.add(ticker)
                opened = timestamp(market.get("open_time"))
                if opened:
                    open_dates.append(opened.isoformat())
                    months[opened.strftime("%Y-%m")] += 1
            cursor = body.get("cursor", "")
            if not cursor:
                complete = True
                break
        result.append({"series": series, "markets_inspected": len(seen),
                       "opened_month_counts": dict(sorted(months.items())),
                       "minimum_open_time_in_inspected_sample": min(open_dates) if open_dates else None,
                       "maximum_open_time_in_inspected_sample": max(open_dates) if open_dates else None,
                       "series_enumeration_complete": complete, "errors": errors,
                       "historical_quotes_collected": 0, "raw_data_published": False})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", default=DEFAULT_RELEASE)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/context/odds")
    parser.add_argument("--kalshi-pages", type=int, default=1)
    parser.add_argument("--time-tolerance-seconds", type=int, default=900)
    parser.add_argument("--minimum-lead-seconds", type=int, default=60)
    args = parser.parse_args()
    require_hosted()
    if not 0 <= args.kalshi_pages <= 8:
        raise ValueError("kalshi-pages must be between 0 and 8")
    if not 0 <= args.time_tolerance_seconds <= 900 or args.minimum_lead_seconds < 0:
        raise ValueError("Invalid timing audit bounds")
    out = args.output_dir.resolve()
    roots = [Path(os.environ[k]).resolve() for k in ("GITHUB_WORKSPACE", "RUNNER_TEMP") if os.environ.get(k)]
    if not any(out == root or root in out.parents for root in roots):
        raise ValueError("Outputs must stay within the hosted workspace or runner temporary directory")
    out.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher()
    tables, quotes, raw, license_text = load_release(fetcher, args.release_tag)
    audits = audit_quotes(tables, quotes, raw, args.time_tolerance_seconds, args.minimum_lead_seconds)
    frame = pd.DataFrame(audits)
    frame.to_parquet(out / "quote_link_audit.parquet", index=False)
    (out / "PARLAY_LICENSE.txt").write_bytes(license_text)
    eligible = [r for r in audits if r["pricing_audit_status"] == "eligible_for_further_pricing_audit"]
    exclusion_counts = Counter(reason for row in audits for reason in json.loads(row["exclusion_reasons"]))
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_release": f"https://github.com/{REPO}/releases/tag/{args.release_tag}",
        "quote_candidates_inspected": len(audits), "eligible_for_further_pricing_audit": len(eligible),
        "roi_eligible_rows": 0, "exclusion_reason_counts": dict(exclusion_counts),
        "eligible_by_league": dict(Counter(r["league"] for r in eligible)),
        "eligible_by_stat": dict(Counter(r["stat"] for r in eligible)),
        "eligible_by_year": dict(Counter(r["matched_event_time"][:4] for r in eligible)),
        "eligible_by_temporal_partition": dict(Counter(r["temporal_partition"] for r in eligible)),
        "eligible_unique_games": len({r["matched_game_id"] for r in eligible}),
        "eligible_unique_players": len({(r["league"], r["matched_player_id"]) for r in eligible}),
        "timing_policy": {"max_schedule_disagreement_seconds": args.time_tolerance_seconds,
                          "minimum_pregame_lead_seconds": args.minimum_lead_seconds,
                          "naive_timestamps_rejected": True},
        "asof_status": "Quote time precedes both start times; retrospective event/player matching does not establish contemporaneous rules, fees, depth, status, or feature availability.",
        "license": {"quotes": "CC-BY-4.0; ParlayAPI (https://parlay-api.com); identity/timing audit is a modification",
                    "nba_nfl_identity": "Existing release source manifests govern source tables; no player outcomes exported here.",
                    "kalshi": "Public documented API; bulk redistribution license not established. Aggregate coverage only."},
        "known_gaps": ["No newly verified freely redistributable complete NBA/NFL prop panel spanning 2023/2024 to 2025+.",
                       "Kalshi series inventory is bounded; earliest date within pages is not a launch date.",
                       "No PrizePicks private API or protected endpoint accessed.",
                       "No model fitting, holdout optimization, outcome-based selection, or betting returns computed."],
        "kalshi_coverage": kalshi_coverage(fetcher, args.kalshi_pages),
        "requests": fetcher.calls, "downloaded_bytes": fetcher.bytes,
        "provenance": fetcher.provenance,
    }
    (out / "context_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("quote_candidates_inspected", "eligible_for_further_pricing_audit",
                                            "roi_eligible_rows", "requests", "downloaded_bytes")}))


if __name__ == "__main__":
    main()
