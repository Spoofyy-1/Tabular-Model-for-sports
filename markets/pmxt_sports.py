"""Cloud-only NBA/NFL PMXT pilot with exact contract identity and quote clocks.

No local real data, Kalshi data, point-data joins, model training or trading.
Primary pages establish two candidate events; matching archived quotes must be
verified by the hosted run. Current rules are hashed, never republished as text.
"""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tennis"))
from market_data import (Fetcher, FIELDS, SNAPSHOT_FIELDS, process_archive, utc_time,
                         json_list, norm, now, failure_reason, require_github_hosted_runner)
from activity_data import Activity, ACTIVITY_FIELDS

PILOTS = [
    {"sport": "NBA", "slug": "nba-cha-orl-2026-04-17", "hour": "2026-04-17T23",
     "scheduled_start": "2026-04-17T23:30:00Z", "rule_date": "April 17",
     "teams": ("Charlotte Hornets", "Orlando Magic"),
     "aliases": (("Hornets", "Charlotte Hornets", "CHA"), ("Magic", "Orlando Magic", "ORL")),
     "props": (("LaMelo Ball", "points", "22.5", ">"), ("Paolo Banchero", "points", "22.5", ">"))},
    {"sport": "NFL", "slug": "nfl-ne-sea-2026-09-10", "hour": "2026-09-09T15",
     "scheduled_start": "2026-09-10T00:20:00Z", "rule_date": "September 9",
     "teams": ("New England Patriots", "Seattle Seahawks"),
     "aliases": (("Patriots", "New England Patriots", "NE"), ("Seahawks", "Seattle Seahawks", "SEA")),
     "props": (("Drake Maye", "passing_yards", "150", ">="),)},
]
EXTRA = ["sport", "market_kind", "player_name", "stat", "threshold_value", "threshold_operator",
         "scheduled_start_utc", "pregame_cutoff_basis", "inactive_policy", "rules_sha256",
         "historical_rule_version_verified"]
EVENT_FIELDS = ["outcome_label" if k == "outcome_player" else k for k in FIELDS] + EXTRA
BOOK_FIELDS = ["outcome_label" if k == "outcome_player" else k for k in SNAPSHOT_FIELDS] + EXTRA
MINUTE_FIELDS = ["outcome_label" if k == "outcome_player" else k for k in ACTIVITY_FIELDS] + EXTRA


def contract_identity(market):
    condition = market.get("conditionId", "")
    labels, tokens = json_list(market.get("outcomes")), json_list(market.get("clobTokenIds"))
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", condition) or len(labels) != 2 or len(tokens) != 2:
        raise ValueError("Invalid binary contract identity")
    if len(set(tokens)) != 2 or any(not isinstance(t, str) or not re.fullmatch(r"[0-9]{1,80}", t) for t in tokens):
        raise ValueError("Invalid outcome token identities")
    return condition.lower(), labels, tokens


def resolve(event, pilot):
    if not isinstance(event, dict) or event.get("slug") != pilot["slug"]:
        raise ValueError("Event slug mismatch")
    title = norm(event.get("title", ""))
    if not all(norm(aliases[0]) in title for aliases in pilot["aliases"]):
        raise ValueError("Event team identities disagree")
    maps, audit = [], Counter()
    for item in event.get("markets", []):
        if not isinstance(item, dict):
            continue
        rules = item.get("description") or ""
        rule_norm = norm(rules)
        if norm(pilot["rule_date"]) not in rule_norm or norm(pilot["sport"]) not in rule_norm:
            continue
        # Explicitly reject half/quarter contracts even if their labels overlap.
        if re.search(r"\b(first|second|1st|2nd)\s+half\b|\bquarter\b", rules, re.I):
            continue
        kind, player, stat, threshold, operator, inactive = None, None, None, None, None, None
        mapped_labels = None
        if item.get("sportsMarketType") == "moneyline" or item.get("slug") == pilot["slug"]:
            kind = "full_game_moneyline"
        else:
            matches = []
            for name, target, value, comparator in pilot["props"]:
                suffix = (r"\s+(?:scores|records)\s+more\s+than\s+" + re.escape(value) + r"\s+points\b"
                          if target == "points" else r"\s+records\s+" + re.escape(value) + r"\s+or\s+more\s+passing\s+yards\b")
                if re.search(re.escape(name) + suffix, rules, re.I):
                    matches.append((name, target, value, comparator))
            if len(matches) == 1:
                kind, (player, stat, threshold, operator) = "player_prop", matches[0]
                reviewed_inactive = "No" if pilot["sport"] == "NBA" else "Under"
                if re.search(r"(?:inactive|does not (?:play|take the court)).{0,250}?resolve(?: to)?\s*[\"“']" + reviewed_inactive + r"\b", rules, re.I | re.S):
                    inactive = reviewed_inactive
        if kind is None:
            continue
        try:
            condition, labels, tokens = contract_identity(item)
            if kind == "full_game_moneyline":
                mapped_labels = []
                for label in labels:
                    teams = [pilot["teams"][i] for i, aliases in enumerate(pilot["aliases"])
                             if norm(label) in {norm(x) for x in aliases}]
                    if len(teams) != 1:
                        raise ValueError("Unverified team outcome")
                    mapped_labels.append(teams[0])
                if set(mapped_labels) != set(pilot["teams"]):
                    raise ValueError("Duplicate team outcome")
            else:
                expected = {"yes", "no"} if pilot["sport"] == "NBA" else {"over", "under"}
                if {str(v).lower() for v in labels} != expected:
                    raise ValueError("Prop outcome semantics differ from reviewed rules")
                mapped_labels = [str(v) for v in labels]
            schedule = item.get("gameStartTime") or item.get("eventStartTime")
            if schedule and utc_time(schedule) != utc_time(pilot["scheduled_start"]):
                raise ValueError("Scheduled start differs from reviewed primary page")
        except (TypeError, ValueError):
            audit["identity_or_schedule_exclusions"] += 1
            continue
        maps.append({"event_slug": pilot["slug"], "condition_id": condition, "tokens": dict(zip(tokens, mapped_labels)),
            "sport": pilot["sport"], "market_kind": kind, "player_name": player, "stat": stat,
            "threshold_value": threshold, "threshold_operator": operator,
            "scheduled_start_utc": pilot["scheduled_start"], "pregame_cutoff_basis": "reviewed_primary_page_scheduled_start_not_actual_tipoff",
            "inactive_policy": inactive, "rules_sha256": hashlib.sha256(rules.encode()).hexdigest(),
            "rules_url": "https://polymarket.com/event/" + pilot["slug"],
            "historical_rule_version_verified": False, "metadata_observed_at_utc": now(),
            "stat_dataset_identity_join_verified": False})
    # Ambiguous duplicates cannot silently create alternative meanings for IDs.
    tokens = [token for m in maps for token in m["tokens"]]
    if len(tokens) != len(set(tokens)) or len({m["condition_id"] for m in maps}) != len(maps):
        raise ValueError("Repeated market/token identities")
    audit["verified_moneylines"] = sum(m["market_kind"] == "full_game_moneyline" for m in maps)
    audit["verified_props"] = sum(m["market_kind"] == "player_prop" for m in maps)
    audit["requested_specific_props"] = len(pilot["props"])
    if audit["verified_moneylines"] > 1:
        raise ValueError("Multiple full-game moneylines")
    return maps, dict(audit)


class Writer:
    def __init__(self, writer, maps, kind, activity, counts):
        self.writer, self.kind, self.activity, self.counts = writer, kind, activity, counts
        self.maps = {m["condition_id"]: m for m in maps}

    def row(self, value):
        mapping = self.maps[value["condition_id"]]
        out = dict(value)
        out["outcome_label"] = out.pop("outcome_player")
        out.update({k: (r"\N" if mapping.get(k) is None else mapping[k]) for k in EXTRA})
        return out

    def writerow(self, value):
        mapping = self.maps[value["condition_id"]]
        cutoff = utc_time(mapping["scheduled_start_utc"])
        if utc_time(value["source_time_utc"]) >= cutoff or utc_time(value["received_time_utc"]) >= cutoff:
            self.counts[self.kind + "_excluded_at_or_after_scheduled_start"] += 1
            return
        self.writer.writerow(self.row(value))
        self.counts[self.kind + "_exported"] += 1
        # Activity uses only licensed quote observations; its internal outcome
        # label is renamed for the public NBA/NFL table (it need not be a player).
        if self.kind == "events":
            self.activity.event(value)
        else:
            self.activity.snapshot(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/markets/sports_pilot")
    args = parser.parse_args()
    require_github_hosted_runner()
    output = args.output_dir.resolve()
    if not output.is_relative_to(Path(os.environ["GITHUB_WORKSPACE"]).resolve()):
        raise RuntimeError("Output must remain inside hosted workspace")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("Use an empty output directory")
    fetcher, all_maps, audits, totals = Fetcher(), [], [], Counter()
    with gzip.open(output / "events.csv.gz", "wt", encoding="utf-8", newline="") as events, \
            gzip.open(output / "snapshots.csv.gz", "wt", encoding="utf-8", newline="") as books, \
            gzip.open(output / "activity_minutes.csv.gz", "wt", encoding="utf-8", newline="") as minutes:
        csv.DictWriter(events, EVENT_FIELDS).writeheader()
        csv.DictWriter(books, BOOK_FIELDS).writeheader()
        minute_writer = csv.DictWriter(minutes, MINUTE_FIELDS)
        minute_writer.writeheader()
        with tempfile.TemporaryDirectory(prefix="pmxt-sports-", dir=os.environ["RUNNER_TEMP"]) as directory:
            for pilot in PILOTS:
                audit = {"sport": pilot["sport"], "event_slug": pilot["slug"], "hour": pilot["hour"]}
                path = None
                try:
                    maps, identity_audit = resolve(fetcher.metadata(pilot["slug"]), pilot)
                    audit["identity_audit"] = identity_audit
                    all_maps.extend(maps)
                    if not maps:
                        raise ValueError("No verified game or prop contracts")
                    path = fetcher.archive(pilot["hour"], Path(directory))
                    counts, activity = Counter(), Activity()
                    event_part, book_part = Path(directory) / "events.csv.gz", Path(directory) / "books.csv.gz"
                    with gzip.open(event_part, "wt", encoding="utf-8", newline="") as ep, \
                            gzip.open(book_part, "wt", encoding="utf-8", newline="") as bp:
                        ew = Writer(csv.DictWriter(ep, EVENT_FIELDS), maps, "events", activity, counts)
                        bw = Writer(csv.DictWriter(bp, BOOK_FIELDS), maps, "snapshots", activity, counts)
                        audit["archive_audit"] = process_archive(path, maps, pilot["hour"], ew, bw)
                    with gzip.open(event_part, "rt", encoding="utf-8", newline="") as source:
                        shutil.copyfileobj(source, events)
                    with gzip.open(book_part, "rt", encoding="utf-8", newline="") as source:
                        shutil.copyfileobj(source, books)
                    for row in activity.rows():
                        out = ew.row(row)
                        minute_writer.writerow({k: r"\N" if v is None else v for k, v in out.items()})
                        counts["activity_minutes_exported"] += 1
                    audit.update(status="processed", counts=dict(counts), activity_audit=dict(activity.audit))
                    totals.update(counts)
                except Exception as exc:
                    audit.update(status="unavailable_or_excluded", error_type=type(exc).__name__, reason=failure_reason(exc))
                finally:
                    if path is not None:
                        path.unlink(missing_ok=True)
                    audits.append(audit)
    fetcher.session.close()
    summary = {"created_at_utc": now(), "status": "quotes_collected" if totals["events_exported"] else "coverage_audit_only",
        "counts": dict(totals), "league_audits": audits, "verified_contracts": len(all_maps),
        "source_bytes": fetcher.budget.bytes, "request_count": fetcher.budget.requests,
        "license": "CC-BY-4.0", "attribution": "PMXT (pmxt.dev)", "point_data_joined": False,
        "betting_roi_evaluated": False, "stat_dataset_identity_join_verified": False,
        "asof_status": "Source/receipt quote clocks preserved; current rules are not proven historical versions",
        "volume_interpretation": "Observed deduplicated quote messages; no stable unique trade ID, possible indistinguishable duplicates and incomplete coverage",
        "schedule_interpretation": "Both quote clocks must precede reviewed scheduled start; actual start and historical fee/depth availability still need audit"}
    for name, value in [("summary.json", summary), ("market_map.json", {"markets": all_maps}),
                        ("source_manifest.json", {"license": "CC-BY-4.0", "attribution": "PMXT", "sources": fetcher.sources}),
                        ("schema.json", {"events.csv.gz": EVENT_FIELDS, "snapshots.csv.gz": BOOK_FIELDS,
                                         "activity_minutes.csv.gz": MINUTE_FIELDS, "null_encoding": r"\N", "ids_are_strings": True})]:
        (output / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (output / "LICENSE.txt").write_text("PMXT archive derivatives, CC BY 4.0: https://creativecommons.org/licenses/by/4.0/ . Attribution: PMXT (https://pmxt.dev), https://archive.pmxt.dev/Polymarket/v2 . Changes: exact NBA/NFL contract filtering, quote normalization, snapshot statistics and observed minute activity. No raw venue rule text, Kalshi data or Match Charting Project data included.\n")
    print(json.dumps({"status": summary["status"], "counts": dict(totals), "verified_contracts": len(all_maps),
                      "source_bytes": fetcher.budget.bytes, "betting_roi_evaluated": False}), flush=True)


if __name__ == "__main__":
    main()
