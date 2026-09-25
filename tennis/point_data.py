#!/usr/bin/env python3
"""Cloud-only, noncommercial tennis point research from Match Charting Project.

CC BY-NC-SA 4.0 research data. No training, wagering, prices, executable trading
signals, or independently timed live points. Never run against real data locally.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

REPO = "JeffSackmann/tennis_MatchChartingProject"
PIN = "1813a1309b7ed7ebf1c7e884b32bf675d00e4edf"
RAW = f"https://raw.githubusercontent.com/{REPO}/{PIN}/"
LICENSE = "CC-BY-NC-SA-4.0"
METADATA_ALIASES = {"player1_name": ["player1", "player1name"], "player2_name": ["player2", "player2name"], "match_date": ["date", "matchdate"], "surface": ["surface"], "best_of": ["bestof"], "tournament": ["tournament", "event"], "round": ["round"]}
POINT_COLUMNS = {"match_id", "Pt", "Set1", "Set2", "Gm1", "Gm2", "Gm#", "TB?", "Svr", "PtWinner"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fetch(name, cache, limit=90_000_000):
    require_github_hosted_runner()
    if not cache.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise RuntimeError("Tennis source cache must remain under RUNNER_TEMP")
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / name
    url = RAW + name
    for attempt in range(3):
        digest, size = hashlib.sha256(), 0
        try:
            with requests.get(url, stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                with path.open("wb") as stream:
                    for block in response.iter_content(1024 * 1024):
                        size += len(block)
                        if size > limit:
                            raise ValueError("Source exceeds bounded tennis input size")
                        digest.update(block)
                        stream.write(block)
                return path, {"source_url": url, "file": name, "bytes": size, "sha256": digest.hexdigest(), "retrieved_at_utc": now(), "repository_commit": PIN, "license": LICENSE}
        except requests.RequestException:
            path.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def number(frame, column):
    if column not in frame:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").astype("float64")


def flag(frame, column):
    if column not in frame:
        return pd.Series(pd.NA, index=frame.index, dtype="boolean")
    return frame[column].astype("string").str.upper().map({"TRUE": True, "FALSE": False, "1": True, "0": False}).astype("boolean")


def metadata(raw, group):
    if "match_id" not in raw:
        raise ValueError("MCP match metadata lacks match_id")
    out = pd.DataFrame({"match_id": raw.match_id.astype("string"), "competition_group": group})
    normalized = {}
    for name in raw.columns:
        key = re.sub(r"[^a-z0-9]", "", str(name).lower())
        normalized.setdefault(key, []).append(name)
    resolved = {}
    for target, aliases in METADATA_ALIASES.items():
        columns = [name for alias in aliases for name in normalized.get(alias, [])]
        if len(columns) > 1:
            raise ValueError("Ambiguous source metadata field for " + target)
        resolved[target] = columns[0] if columns else None
        out[target] = raw[columns[0]].astype("string") if columns else pd.Series(pd.NA, index=raw.index, dtype="string")
    out["match_date"] = pd.to_datetime(out.match_date, errors="coerce", format="mixed")
    out["best_of"] = pd.to_numeric(out.best_of, errors="coerce")
    out["source_match_date_precision"] = "calendar_date"
    out["tour_sanction_verified"] = False
    names_present = out.player1_name.notna() & out.player2_name.notna()
    doubles_hint = out.player1_name.str.contains(r"[&/]", na=False) | out.player2_name.str.contains(r"[&/]", na=False)
    out["singles_metadata_eligible"] = names_present & ~doubles_hint
    status_cols = [name for name in raw.columns if re.sub(r"[^a-z0-9]", "", name.lower()) in {"status", "result", "score", "matchstatus"}]
    out["source_retirement_or_walkover_flag"] = False
    for name in status_cols:
        out["source_retirement_or_walkover_flag"] |= raw[name].astype("string").str.contains(r"\bRET\b|\bW/O\b|\bDEF\b|\bABD\b|retired|walkover", case=False, regex=True, na=False)
    original_rows = len(out)
    out = out.drop_duplicates()
    exact_duplicates = original_rows - len(out)
    missing = out.match_id.isna() | out.match_id.eq("").fillna(False)
    conflicting = out.match_id.duplicated(keep=False) & ~missing
    resolved["key_quality"] = {"normalized_exact_duplicates_removed": exact_duplicates,
        "missing_match_key_rows_excluded": int(missing.sum()),
        "conflicting_match_key_rows_excluded": int(conflicting.sum())}
    # Original metadata remains in source_matches.csv.gz. Never choose one
    # conflicting identity/date based on input order.
    out = out.loc[~missing & ~conflicting].copy()
    if out.empty:
        raise ValueError("No unambiguous MCP match metadata remains")
    return out, resolved


def point_states(raw, matches):
    raw = raw.copy()
    # Producer dictionaries and historical exports can differ in punctuation.
    # Resolve only a unique exact normalized header; never infer from outcomes.
    for required in POINT_COLUMNS:
        if required not in raw:
            normalize = lambda name: re.sub(r"[^a-z0-9]", "", str(name).lower())
            alternatives = [name for name in raw if normalize(name) == normalize(required)]
            if len(alternatives) == 1:
                raw = raw.rename(columns={alternatives[0]: required})
    if not POINT_COLUMNS.issubset(raw):
        raise ValueError("MCP points missing documented fields: " + str(POINT_COLUMNS - set(raw)) + "; source columns: " + str(list(raw.columns)))
    out = pd.DataFrame(index=raw.index)
    out["match_id"] = raw.match_id.astype("string")
    out["source_point_number"] = number(raw, "Pt")
    game_parts = raw["Gm#"].astype("string").str.extract(r"^\s*(\d+)(?:\s*\((\d+)\))?\s*$")
    out["source_game_number"] = pd.to_numeric(game_parts[0], errors="coerce").astype("float64")
    out["source_point_number_in_game"] = pd.to_numeric(game_parts[1], errors="coerce").astype("float64")
    for source, target in [("Set1", "pre_sets_p1"), ("Set2", "pre_sets_p2"), ("Gm1", "pre_games_p1"), ("Gm2", "pre_games_p2"), ("Svr", "pre_server_player")]:
        out[target] = number(raw, source)
    out["pre_tiebreak"] = flag(raw, "TB?")
    out["label_point_winner_player"] = number(raw, "PtWinner")
    out["label_first_serve_in"] = flag(raw, "1stIn")
    out["label_second_serve_in"] = flag(raw, "2ndIn")
    out["label_ace"] = flag(raw, "isAce")
    out["label_double_fault"] = flag(raw, "isDouble")
    out["label_rally_count"] = number(raw, "rallyCount")
    out["source_server_winner_flag"] = flag(raw, "isSvrWinner")
    out = out.sort_values(["match_id", "source_point_number"], na_position="last").reset_index(drop=True)
    out = out.merge(matches, on="match_id", how="left", validate="many_to_one")
    if out.duplicated(["match_id", "source_point_number"]).any():
        raise ValueError("Duplicate point numbers within source match; reconciliation required")
    by_match = out.groupby("match_id", sort=False, dropna=False)
    prior_point = by_match.source_point_number.shift(1)
    prior_game = by_match.source_game_number.shift(1)
    first = by_match.cumcount().eq(0)
    contiguous = out.source_point_number.eq(prior_point + 1) | (first & out.source_point_number.eq(1))
    legal_game_step = out.source_game_number.eq(prior_game) | out.source_game_number.eq(prior_game + 1) | (first & out.source_game_number.eq(1))
    score_columns = ["pre_sets_p1", "pre_sets_p2", "pre_games_p1", "pre_games_p2"]
    integral_scores = out[score_columns].ge(0).all(axis=1) & out[score_columns].mod(1).eq(0).all(axis=1)
    current_state_valid = out.pre_server_player.isin([1, 2]) & out.source_game_number.ge(1) & out.pre_tiebreak.notna() & integral_scores
    # The validity of the current point's outcome does not affect its pre-point scores.
    bad_winner_before = ~by_match.label_point_winner_player.shift(1).isin([1, 2]) & ~first
    bad = (~contiguous | ~legal_game_step | ~current_state_valid | bad_winner_before).astype(int)
    out["pre_score_prefix_valid"] = bad.groupby(out.match_id, dropna=False).cumsum().eq(0)
    out["label_server_won_point"] = out.label_point_winner_player.eq(out.pre_server_player).astype("boolean").where(out.label_point_winner_player.isin([1, 2]) & out.pre_server_player.isin([1, 2]))
    out["source_winner_flag_agrees"] = out.source_server_winner_flag.eq(out.label_server_won_point).where(out.source_server_winner_flag.notna())
    for who in [1, 2]:
        wins = out.label_point_winner_player.eq(who).astype(int)
        prior_wins = wins.groupby([out.match_id, out.source_game_number], dropna=False).cumsum() - wins
        out[f"pre_p{who}_points_in_game"] = prior_wins.astype("Float64").where(out.pre_score_prefix_valid)
    # Validate a game boundary against only the preceding point's outcome.
    # A new source game ID cannot silently reset an unfinished or corrupt game.
    same_game = out.source_game_number.eq(prior_game) & ~first
    new_game = out.source_game_number.eq(prior_game + 1) & ~first
    transition_columns = score_columns + ["pre_p1_points_in_game", "pre_p2_points_in_game", "label_point_winner_player", "pre_tiebreak", "pre_server_player"]
    previous = out.groupby("match_id", sort=False, dropna=False)[transition_columns].shift(1)
    previous_post1 = previous.pre_p1_points_in_game + previous.label_point_winner_player.eq(1).astype(int)
    previous_post2 = previous.pre_p2_points_in_game + previous.label_point_winner_player.eq(2).astype(int)
    previous_tb = previous.pre_tiebreak.fillna(False)
    minimum = pd.Series(np.where(previous_tb, 7, 4), index=out.index)
    previous_winner1 = previous_post1.ge(minimum) & (previous_post1 - previous_post2).ge(2)
    previous_winner2 = previous_post2.ge(minimum) & (previous_post2 - previous_post1).ge(2)
    final_games1 = previous.pre_games_p1 + previous_winner1.fillna(False).astype(int)
    final_games2 = previous.pre_games_p2 + previous_winner2.fillna(False).astype(int)
    set_winner1 = previous_winner1 & (previous_tb | (final_games1.ge(6) & (final_games1 - final_games2).ge(2)))
    set_winner2 = previous_winner2 & (previous_tb | (final_games2.ge(6) & (final_games2 - final_games1).ge(2)))
    set_ended = (set_winner1 | set_winner2).fillna(False)
    unchanged_scores = out[score_columns].eq(previous[score_columns]).all(axis=1)
    same_game_valid = unchanged_scores & out.pre_tiebreak.eq(previous.pre_tiebreak) & (out.pre_tiebreak.eq(True) | out.pre_server_player.eq(previous.pre_server_player))
    boundary_valid = (previous_winner1 | previous_winner2).fillna(False)
    boundary_valid &= out.pre_games_p1.eq(final_games1.where(~set_ended, 0)) & out.pre_games_p2.eq(final_games2.where(~set_ended, 0))
    boundary_valid &= out.pre_sets_p1.eq(previous.pre_sets_p1 + set_winner1.fillna(False).astype(int)) & out.pre_sets_p2.eq(previous.pre_sets_p2 + set_winner2.fillna(False).astype(int))
    first_valid = out[score_columns].eq(0).all(axis=1) & out.pre_server_player.eq(1)
    game_point_valid = out.source_point_number_in_game.isna() | out.source_point_number_in_game.eq(out.pre_p1_points_in_game + out.pre_p2_points_in_game + 1)
    invalid_transition = (first & ~first_valid) | (same_game & ~same_game_valid.fillna(False)) | (new_game & ~boundary_valid.fillna(False)) | ~game_point_valid.fillna(False)
    already_finished_standard_game = out.pre_tiebreak.eq(False).fillna(False) & (((out.pre_p1_points_in_game >= 4) & ((out.pre_p1_points_in_game - out.pre_p2_points_in_game) >= 2)) | ((out.pre_p2_points_in_game >= 4) & ((out.pre_p2_points_in_game - out.pre_p1_points_in_game) >= 2)))
    invalid_score_seen = (already_finished_standard_game.fillna(False) | invalid_transition).astype(int).groupby(out.match_id, dropna=False).cumsum().gt(0)
    out["pre_score_prefix_valid"] &= ~invalid_score_seen
    for who in [1, 2]:
        out[f"pre_p{who}_points_in_game"] = out[f"pre_p{who}_points_in_game"].where(out.pre_score_prefix_valid)
    server_points = out.pre_p1_points_in_game.where(out.pre_server_player.eq(1), out.pre_p2_points_in_game)
    returner_points = out.pre_p2_points_in_game.where(out.pre_server_player.eq(1), out.pre_p1_points_in_game)
    valid_standard = out.pre_score_prefix_valid & out.pre_tiebreak.eq(False).fillna(False)
    out["pre_break_point"] = (returner_points.ge(3) & returner_points.gt(server_points)).astype("boolean").where(valid_standard)
    out["pre_game_point_for_server"] = (server_points.ge(3) & server_points.gt(returner_points)).astype("boolean").where(valid_standard)
    out["pre_server_points_in_game"] = server_points
    out["pre_returner_points_in_game"] = returner_points
    out["research_only_noncommercial"] = True
    out["historical_wallclock_available"] = False
    out["evaluation_split"] = "date_unresolved"
    out.loc[out.match_date < pd.Timestamp("2024-01-01"), "evaluation_split"] = "fit_pre_2024"
    out.loc[(out.match_date >= pd.Timestamp("2024-01-01")) & (out.match_date < pd.Timestamp("2025-01-01")), "evaluation_split"] = "calibration_2024"
    out.loc[out.match_date >= pd.Timestamp("2025-01-01"), "evaluation_split"] = "holdout_2025_plus"
    ambiguous_dates = out.match_date.dt.strftime("%Y-%m-%d").isin(["2023-12-31", "2024-01-01", "2024-12-31", "2025-01-01"])
    out.loc[ambiguous_dates, "evaluation_split"] = "date_precision_unresolved"
    return out


def early_labels(states):
    """Retrospective labels only; conservative finished-match evidence."""
    ordered = states.sort_values(["match_id", "source_point_number"])
    last = ordered.groupby("match_id", as_index=False, sort=False).tail(1).copy()
    p1post = last.pre_p1_points_in_game + last.label_point_winner_player.eq(1).astype(int)
    p2post = last.pre_p2_points_in_game + last.label_point_winner_player.eq(2).astype(int)
    regular_game_winner1 = (p1post.ge(4) & (p1post - p2post).ge(2)).fillna(False)
    regular_game_winner2 = (p2post.ge(4) & (p2post - p1post).ge(2)).fillna(False)
    games1 = last.pre_games_p1 + regular_game_winner1.astype(int)
    games2 = last.pre_games_p2 + regular_game_winner2.astype(int)
    set1 = games1.ge(6) & (games1 - games2).ge(2) & regular_game_winner1
    set2 = games2.ge(6) & (games2 - games1).ge(2) & regular_game_winner2
    required_sets = (last.best_of + 1) / 2
    terminal = (set1 & (last.pre_sets_p1 + 1).eq(required_sets)) | (set2 & (last.pre_sets_p2 + 1).eq(required_sets))
    last["finished_match_evidence"] = (terminal & last.pre_tiebreak.eq(False).fillna(False) & last.pre_score_prefix_valid & last.best_of.isin([3, 5]) & ~last.source_retirement_or_walkover_flag.fillna(True)).fillna(False)
    first_set = states.pre_sets_p1.eq(0) & states.pre_sets_p2.eq(0)
    after_four = states.loc[first_set & (states.pre_games_p1 + states.pre_games_p2).eq(4) & states.pre_score_prefix_valid].sort_values(["match_id", "source_point_number"]).groupby("match_id", as_index=False).head(1)
    keep = ["match_id", "competition_group", "match_date", "surface", "player1_name", "player2_name", "singles_metadata_eligible"]
    result = last[keep + ["finished_match_evidence"]].merge(after_four[["match_id", "pre_games_p1", "pre_games_p2"]], on="match_id", how="left", validate="one_to_one")
    result = result.rename(columns={"pre_games_p1": "label_p1_games_after_four", "pre_games_p2": "label_p2_games_after_four"})
    result["label_early_deficit_eligible"] = result.finished_match_evidence & result.singles_metadata_eligible.fillna(False) & result.label_p1_games_after_four.notna()
    result["label_p1_trailing_after_four_games"] = result.label_p1_games_after_four.lt(result.label_p2_games_after_four).astype("boolean").where(result.label_early_deficit_eligible)
    result["label_p2_trailing_after_four_games"] = result.label_p2_games_after_four.lt(result.label_p1_games_after_four).astype("boolean").where(result.label_early_deficit_eligible)
    result["player1_initial_server_from_source_contract"] = True
    return result.sort_values(["match_date", "competition_group", "match_id"]).reset_index(drop=True)


def count_player_context(states):
    # Only pre-2025 descriptive data. No holdout ranking or tuning.
    eligible = states.match_date.lt(pd.Timestamp("2024-12-31")) & states.pre_score_prefix_valid & states.singles_metadata_eligible.fillna(False) & states.label_server_won_point.notna()
    source_disagrees = states.source_winner_flag_agrees.eq(False).fillna(False)
    frame = states.loc[eligible & ~source_disagrees].copy()
    chunks = []
    for side in [1, 2]:
        piece = frame[["competition_group", "match_id"]].copy()
        piece["player_name"] = frame[f"player{side}_name"]
        serve = frame.pre_server_player.eq(side)
        win = frame.label_point_winner_player.eq(side)
        bp = frame.pre_break_point.fillna(False)
        nonbp = frame.pre_break_point.eq(False).fillna(False)
        tb = frame.pre_tiebreak.fillna(False)
        for name, mask in {"serve_points": serve, "serve_points_won": serve & win, "break_points_faced": serve & bp, "break_points_saved": serve & bp & win, "nonbp_serve_points": serve & nonbp, "nonbp_serve_points_won": serve & nonbp & win, "tiebreak_serve_points": serve & tb, "tiebreak_serve_points_won": serve & tb & win, "tiebreak_return_points": ~serve & tb, "tiebreak_return_points_won": ~serve & tb & win}.items():
            piece[name] = mask.astype(int)
        chunks.append(piece)
    if not chunks:
        return pd.DataFrame()
    joined = pd.concat(chunks, ignore_index=True)
    metrics = [name for name in joined if name not in {"competition_group", "player_name", "match_id"}]
    aggregation = {name: (name, "sum") for name in metrics}
    aggregation["charted_matches"] = ("match_id", "nunique")
    return joined.groupby(["competition_group", "player_name"], as_index=False).agg(**aggregation)


def wilson(wins, attempts):
    if attempts <= 0:
        return (float("nan"), float("nan"))
    z = 1.959963984540054
    p = wins / attempts
    denominator = 1 + z*z/attempts
    center = (p + z*z/(2*attempts)) / denominator
    half = z * math.sqrt(p*(1-p)/attempts + z*z/(4*attempts*attempts)) / denominator
    return center-half, center+half


def player_summary(parts, labels):
    sums = pd.concat(parts, ignore_index=True).groupby(["competition_group", "player_name"], as_index=False).sum(numeric_only=True)
    for stem, won, attempted in [("break_save", "break_points_saved", "break_points_faced"), ("nonbp_serve_win", "nonbp_serve_points_won", "nonbp_serve_points"), ("tiebreak_serve_win", "tiebreak_serve_points_won", "tiebreak_serve_points"), ("tiebreak_return_win", "tiebreak_return_points_won", "tiebreak_return_points")]:
        sums[f"{stem}_rate"] = sums[won] / sums[attempted].replace(0, np.nan)
        intervals = [wilson(w, n) for w, n in zip(sums[won], sums[attempted])]
        sums[f"{stem}_wilson95_low"] = [v[0] for v in intervals]
        sums[f"{stem}_wilson95_high"] = [v[1] for v in intervals]
    sums["break_save_shrunk_toward_own_nonbp"] = (sums.break_points_saved + 20*sums.nonbp_serve_win_rate) / (sums.break_points_faced + 20)
    sums["break_save_shrunk_difference_vs_nonbp"] = sums.break_save_shrunk_toward_own_nonbp - sums.nonbp_serve_win_rate
    sums["clutch_screen_eligible"] = sums.break_points_faced.ge(30) & sums.serve_points.ge(200) & sums.charted_matches.ge(10) & sums.nonbp_serve_points.ge(100)
    early = labels.loc[labels.label_early_deficit_eligible & labels.match_date.lt(pd.Timestamp("2024-12-31"))]
    for who, orientation in [(1, "served_first"), (2, "returned_first")]:
        table = early[["competition_group", f"player{who}_name", f"label_p{who}_trailing_after_four_games"]].rename(columns={f"player{who}_name": "player_name"})
        table["early_matches"] = 1
        table["early_trailing_matches"] = table[f"label_p{who}_trailing_after_four_games"].fillna(False).astype(int)
        table = table.groupby(["competition_group", "player_name"], as_index=False)[["early_matches", "early_trailing_matches"]].sum().rename(columns={"early_matches": f"early_{orientation}_matches", "early_trailing_matches": f"early_{orientation}_trailing_matches"})
        sums = sums.merge(table, on=["competition_group", "player_name"], how="left", validate="one_to_one")
        n = sums[f"early_{orientation}_matches"]
        w = sums[f"early_{orientation}_trailing_matches"]
        sums[f"early_{orientation}_trailing_rate"] = w / n
        sums[f"early_{orientation}_trailing_shrunk_rate"] = (w + 1) / (n + 2)
        sums[f"early_{orientation}_screen_eligible"] = n.ge(10)
        intervals = [wilson(x, y) if pd.notna(y) else (np.nan, np.nan) for x, y in zip(w, n)]
        sums[f"early_{orientation}_wilson95_low"] = [v[0] for v in intervals]
        sums[f"early_{orientation}_wilson95_high"] = [v[1] for v in intervals]
    return sums.sort_values(["competition_group", "player_name"]).reset_index(drop=True)


def write_csv(frame, path):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"rows": len(frame), "schema": {name: str(dtype) for name,dtype in frame.dtypes.items()}, "missing_values": {name: int(value) for name,value in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", nargs="+", choices=["m", "w"], default=["m", "w"])
    args = parser.parse_args()
    if len(args.groups) != len(set(args.groups)):
        raise ValueError("Repeated competition groups are not permitted")
    cache = Path(os.environ["RUNNER_TEMP"]) / "tennis_mcp"
    output = ROOT / "data/tennis/mcp_research_only"
    output.mkdir(parents=True, exist_ok=True)
    manifests, tables, counts, labels_parts, player_parts = [], {}, [], [], []
    seen_matches = set()
    for name in ["README.md", "data_dictionary.txt"]:
        path, provenance = fetch(name, cache, 1_000_000)
        (output / ("SOURCE_" + name)).write_bytes(path.read_bytes())
        manifests.append(provenance)
    (output / "LICENSE.txt").write_text("CC BY-NC-SA 4.0. Source data copyright Jeff Sackmann / Tennis Abstract Match Charting Project and contributors. Attribution required, noncommercial use only, share adaptations under the same license. Changes: normalized CSV export, derived pre-point states, retrospective labels and descriptive aggregates. Full legal terms: https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode\n")
    for gender in args.groups:
        group = "mens_singles" if gender == "m" else "womens_singles"
        path, provenance = fetch(f"charting-{gender}-matches.csv", cache)
        raw_matches = pd.read_csv(path, dtype="string", keep_default_na=False, na_values=[""], encoding="utf-8-sig")
        matches, resolved = metadata(raw_matches, group)
        provenance.update(rows=len(raw_matches), resolved_metadata_columns=resolved)
        manifests.append(provenance)
        tables[f"{group}/source_matches.csv.gz"] = write_csv(raw_matches, output / group / "source_matches.csv.gz")
        tables[f"{group}/matches.csv.gz"] = write_csv(matches, output / group / "matches.csv.gz")
        for era in ["to-2009", "2010s", "2020s"]:
            name = f"charting-{gender}-points-{era}.csv"
            path, provenance = fetch(name, cache)
            raw_points = pd.read_csv(path, dtype="string", keep_default_na=False, na_values=[""], encoding="utf-8-sig")
            source_match_ids = set(raw_points.match_id.dropna())
            if seen_matches.intersection(source_match_ids):
                raise ValueError("Matches overlap across source point files; reconcile before aggregation")
            seen_matches.update(source_match_ids)
            numeric_point = pd.to_numeric(raw_points.Pt, errors="coerce")
            raw_points = raw_points.assign(_numeric_order=numeric_point).sort_values(["match_id", "_numeric_order"]).drop(columns="_numeric_order")
            tables[f"{group}/{era}/source_points.csv.gz"] = write_csv(raw_points, output / group / era / "source_points.csv.gz")
            states = point_states(raw_points, matches)
            labels = early_labels(states)
            tables[f"{group}/{era}/point_states.csv.gz"] = write_csv(states, output / group / era / "point_states.csv.gz")
            labels_parts.append(labels)
            player_parts.append(count_player_context(states))
            count = {"group": group, "era": era, "point_rows": len(states), "matches": int(states.match_id.nunique()), "date_min": states.match_date.min().isoformat() if states.match_date.notna().any() else None, "date_max": states.match_date.max().isoformat() if states.match_date.notna().any() else None, "valid_prefix_rows": int(states.pre_score_prefix_valid.sum()), "metadata_unmapped_rows": int(states.competition_group.isna().sum()), "first_serve_status_rows": int(states.label_first_serve_in.notna().sum()), "source_winner_disagreement_rows": int(states.source_winner_flag_agrees.eq(False).fillna(False).sum()), "early_labels_eligible_matches": int(labels.label_early_deficit_eligible.sum()), "split_counts": {str(k): int(v) for k,v in states.evaluation_split.value_counts().items()}}
            counts.append(count)
            provenance.update(rows=len(raw_points), schema={name: str(dtype) for name,dtype in raw_points.dtypes.items()})
            manifests.append(provenance)
            print(json.dumps(count), flush=True)
            del raw_points, states
        del raw_matches, matches
    labels = pd.concat(labels_parts, ignore_index=True).sort_values(["match_date", "competition_group", "match_id"])
    summaries = player_summary(player_parts, labels)
    tables["match_early_deficit_labels.csv.gz"] = write_csv(labels, output / "match_early_deficit_labels.csv.gz")
    tables["player_summary.csv.gz"] = write_csv(summaries, output / "player_summary.csv.gz")
    top = {}
    for group, frame in summaries.groupby("competition_group"):
        candidates = frame.loc[frame.clutch_screen_eligible].sort_values("break_save_shrunk_difference_vs_nonbp", ascending=False).head(5)
        top[group] = {"descriptive_break_save_screen": json.loads(candidates[["player_name", "charted_matches", "break_points_faced", "break_points_saved", "nonbp_serve_win_rate", "break_save_shrunk_toward_own_nonbp", "break_save_shrunk_difference_vs_nonbp", "break_save_wilson95_low", "break_save_wilson95_high"]].to_json(orient="records"))}
        for orientation in ["served_first", "returned_first"]:
            selected = frame.loc[frame[f"early_{orientation}_screen_eligible"]].sort_values(f"early_{orientation}_trailing_shrunk_rate", ascending=False).head(5)
            top[group][f"early_trailing_{orientation}_screen"] = json.loads(selected[["player_name", f"early_{orientation}_matches", f"early_{orientation}_trailing_matches", f"early_{orientation}_trailing_rate", f"early_{orientation}_wilson95_low", f"early_{orientation}_wilson95_high"]].to_json(orient="records"))
    summary = {"created_at_utc": now(), "source_repository": REPO, "source_commit": PIN, "license": LICENSE, "research_only_noncommercial": True, "commercial_betting_eligibility_claimed": False, "point_rows": sum(row["point_rows"] for row in counts), "matches_with_points": len(seen_matches), "player_summary_rows": len(summaries), "qualified_descriptive_clutch_players": int(summaries.clutch_screen_eligible.sum()), "source_bytes": sum(item["bytes"] for item in manifests), "partitions": counts, "descriptive_summary_end_exclusive": "2024-12-31", "cutoff_precision_note": "Calendar dates lack source timezone; December31,2024 is excluded conservatively from pre-2025 descriptive summaries", "candidate_screens": top,
        "limitations": ["Volunteer-charted match selection is not representative of all players or surfaces; rankings describe only this sample.", "Men's/women's files remain separate. ATP/WTA sanctioned-tour membership is not verified from a gender label; other professional singles circuits may be included.", "Break-point/non-break-point differences do not establish causal clutch ability; opponents, surface, fatigue and selection confound them.", "Wilson intervals and explicit shrinkage expose uncertainty; minimum samples do not remove selection bias.", "Early-deficit labels require four first-set games plus conservative finished-match scoring evidence; terminal tiebreaks, incomplete sequences and retirement flags are excluded.", "Points have sequence order but no executable wallclock, betting prices, fill latency, liquidity or fees.", "First-serve/result fields and point winners are outcomes, not pre-point features. No new tennis models are trained.", "Source original fields remain available for audit; model consumers must explicitly whitelist pre-point features and respect the research-only license."]}
    (output / "data_summary.json").write_text(json.dumps(summary, indent=2))
    (output / "schema.json").write_text(json.dumps({"csv_null_encoding": r"\N", "tables": tables, "temporal_roles": {"pre_*": "before this point, subject to source score/prefix validation", "label_*": "current or future outcomes; never unshifted pre-point predictors", "source_*": "provenance or source fields; not automatic features", "player_summary": "descriptive pre-2025 aggregate, not point-in-time historical player ability"}}, indent=2))
    (output / "source_manifest.json").write_text(json.dumps({"attribution": "Jeff Sackmann / Tennis Abstract Match Charting Project and volunteer contributors", "license": LICENSE, "changes": "Normalized tabular export, reconstructed pre-point score counts, retrospective early-deficit labels, descriptive player aggregates", "assets": manifests}, indent=2))
    print(json.dumps({key: summary[key] for key in ["point_rows", "matches_with_points", "player_summary_rows", "qualified_descriptive_clutch_players", "license"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
