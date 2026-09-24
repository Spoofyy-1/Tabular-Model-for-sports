#!/usr/bin/env python3
"""Download and normalize ESPN-derived hoopR NBA boxscores (2002 onward).

No credentials, scraping, invented games, or imputed boxscore zeros. Season is
the season END year. Same-game statistics and lineup flags are outcomes, not
pregame predictors. Execution is restricted to GitHub-hosted Actions runners.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import pandas as pd
import requests

RELEASE_ROOT = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
SOURCE_ROOT = "https://github.com/sportsdataverse/hoopR-nba-data"
ROOT = Path(__file__).resolve().parents[1]
STAT_MAP = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "three_point_field_goals_made": "threes", "minutes": "minutes",
    "steals": "steals", "blocks": "blocks", "turnovers": "turnovers",
    "field_goals_made": "field_goals_made", "field_goals_attempted": "field_goals_attempted",
    "three_point_field_goals_attempted": "threes_attempted",
    "free_throws_made": "free_throws_made", "free_throws_attempted": "free_throws_attempted",
    "offensive_rebounds": "offensive_rebounds", "defensive_rebounds": "defensive_rebounds",
    "fouls": "fouls", "plus_minus": "plus_minus",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def require_github_hosted_runner():
    """Refuse data collection/storage on local machines and self-hosted runners."""
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError(
            "NBA data collection is permitted only on GitHub-hosted Actions runners "
            "(GITHUB_ACTIONS=true and RUNNER_ENVIRONMENT=github-hosted). "
            "Local and self-hosted execution are disabled."
        )


def fetch_asset(spec):
    require_github_hosted_runner()
    kind, season, cache = spec
    prefix = "player_box" if kind == "player" else "team_box"
    filename = f"{prefix}_{season}.parquet"
    url = f"{RELEASE_ROOT}/espn_nba_{kind}_boxscores/{filename}"
    path = cache / filename
    sidecar = cache / (filename + ".provenance.json")
    if not path.exists():
        for attempt in range(4):
            try:
                response = requests.get(url, timeout=(15, 90))
                response.raise_for_status()
                if len(response.content) > 20_000_000:
                    raise ValueError("Unexpectedly large boxscore asset")
                path.write_bytes(response.content)
                sidecar.write_text(json.dumps({"downloaded_at_utc": utc_now(),
                    "source_url": url, "response_last_modified": response.headers.get("Last-Modified")}, indent=2))
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
    provenance = json.loads(sidecar.read_text()) if sidecar.exists() else {
        "downloaded_at_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "timestamp_basis": "cache file creation/last-write timestamp from initial verified download"}
    provenance.update(source_url=url, local_cache=str(path), bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), season=season, dataset=kind)
    df = pd.read_parquet(path)
    provenance["raw_rows"] = len(df)
    return kind, season, df, provenance


def identifier(series):
    return pd.to_numeric(series, errors="coerce").astype("Int64").astype("string")


def normalize_common(raw, team=False):
    out = pd.DataFrame(index=raw.index)
    out["sport"] = "NBA"
    out["game_id"] = identifier(raw.game_id)
    out["game_date"] = pd.to_datetime(raw.game_date_time, utc=True, errors="coerce")
    out["game_local_date"] = raw.game_date.astype("string")
    out["season"] = pd.to_numeric(raw.season, errors="coerce").astype("Int64")
    out["season_type"] = pd.to_numeric(raw.season_type, errors="coerce").astype("Int64")
    out["team_id"] = identifier(raw.team_id)
    out["team"] = raw.team_abbreviation.astype("string")
    out["team_name"] = raw.team_display_name.astype("string")
    out["opponent_id"] = identifier(raw.opponent_team_id)
    out["opponent"] = raw.opponent_team_abbreviation.astype("string")
    side = raw["team_home_away" if team else "home_away"]
    out["is_home"] = side.map({"home": True, "away": False}).astype("boolean")
    out["team_score"] = pd.to_numeric(raw.team_score, errors="coerce")
    out["opponent_score"] = pd.to_numeric(raw.opponent_team_score, errors="coerce")
    return out


def normalize_player(raw):
    out = normalize_common(raw)
    out["player_id"] = identifier(raw.athlete_id)
    out["player_name"] = raw.athlete_display_name.astype("string")
    out["position"] = raw.athlete_position_abbreviation.astype("string")
    for original, normalized in STAT_MAP.items():
        out[normalized] = pd.to_numeric(raw.get(original), errors="coerce")
    out["did_not_play"] = raw.did_not_play.astype("boolean")
    out["boxscore_observed"] = (~out.did_not_play & out.points.notna()).astype("boolean")
    out["starter_actual"] = raw.starter.astype("boolean")
    out["ejected_actual"] = raw.ejected.astype("boolean")
    out["source_active_flag"] = raw.active.astype("boolean")
    out["source_reason"] = raw.get("reason", pd.Series(index=raw.index, dtype="string")).astype("string")
    return out


def normalize_team(raw):
    out = normalize_common(raw, team=True)
    for original, normalized in STAT_MAP.items():
        if original in raw:
            out[normalized] = pd.to_numeric(raw[original], errors="coerce")
    out["points"] = out.team_score
    out["rebounds"] = pd.to_numeric(raw.total_rebounds, errors="coerce")
    for col in ["total_turnovers", "team_turnovers", "field_goal_pct", "free_throw_pct", "three_point_field_goal_pct"]:
        out[col] = pd.to_numeric(raw.get(col), errors="coerce")
    return out


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=2002)
    parser.add_argument("--end", type=int, default=2026)
    cache_root = Path(os.environ.get("SPORTS_PROPS_CACHE", os.environ.get("RUNNER_TEMP", "/tmp")))
    parser.add_argument("--cache", type=Path, default=cache_root / "nba")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "nba")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    specs = [(kind, year, args.cache) for year in range(args.start, args.end + 1) for kind in ["player", "team"]]
    frames = {"player": [], "team": []}
    provenance = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for kind, year, frame, asset in pool.map(fetch_asset, specs):
            frames[kind].append(normalize_player(frame) if kind == "player" else normalize_team(frame))
            provenance.append(asset)
            print(f"NBA {kind} {year}: {len(frame):,} rows", flush=True)
    player = pd.concat(frames["player"], ignore_index=True)
    team = pd.concat(frames["team"], ignore_index=True)
    invalid = player.game_id.isna() | player.player_id.isna() | player.game_date.isna()
    rejected = player.loc[invalid].copy()
    rejected.to_parquet(args.output / "rows_missing_identifiers.parquet", index=False)
    player = player.loc[~invalid].copy()
    player_duplicates = int(player.duplicated(["game_id", "player_id"]).sum())
    team_duplicates = int(team.duplicated(["game_id", "team_id"]).sum())
    if player_duplicates or team_duplicates:
        raise ValueError(f"Duplicate keys: player={player_duplicates}, team={team_duplicates}; manual reconciliation required")
    player = player.sort_values(["game_date", "game_id", "player_id"]).reset_index(drop=True)
    team = team.sort_values(["game_date", "game_id", "team_id"]).reset_index(drop=True)
    player.to_parquet(args.output / "player_games.parquet", index=False, compression="zstd")
    team.to_parquet(args.output / "team_games.parquet", index=False, compression="zstd")
    home = team.loc[team.is_home.fillna(False), ["game_id", "game_date", "game_local_date", "season", "season_type", "team_id", "team", "opponent_id", "opponent", "team_score", "opponent_score"]].copy()
    home = home.rename(columns={"team_id": "home_team_id", "team": "home_team", "opponent_id": "away_team_id", "opponent": "away_team", "team_score": "home_score", "opponent_score": "away_score"})
    home.to_parquet(args.output / "games.parquet", index=False, compression="zstd")
    observed = player.loc[player.boxscore_observed].copy()
    totals = observed.groupby(["game_id", "team_id"], as_index=False).agg(
        recorded_player_points=("points", "sum"), observed_players=("player_id", "size"))
    reconciliation = team[["game_id", "game_date", "season", "team_id", "team", "points"]].merge(
        totals, on=["game_id", "team_id"], how="left", validate="one_to_one")
    reconciliation["score_gap"] = reconciliation.points - reconciliation.recorded_player_points
    reconciliation["player_box_matches_team_score"] = reconciliation.score_gap.eq(0)
    reconciliation.to_parquet(args.output / "team_score_reconciliation.parquet", index=False, compression="zstd")
    core = ["points", "rebounds", "assists", "threes", "steals", "blocks", "turnovers"]
    qa = {
        "observed_target_missingness": {c: int(observed[c].isna().sum()) for c in core + ["minutes"]},
        "negative_core_counts": {c: int((observed[c] < 0).sum()) for c in core + ["minutes"]},
        "points_shooting_identity_mismatches": int((observed.points != 2 * observed.field_goals_made + observed.threes + observed.free_throws_made).sum()),
        "team_games_with_no_observed_players": int(reconciliation.recorded_player_points.isna().sum()),
        "team_games_with_nonzero_score_gap": int((reconciliation.score_gap.notna() & reconciliation.score_gap.ne(0)).sum()),
        "flagged_game_ids": sorted(reconciliation.loc[~reconciliation.player_box_matches_team_score, "game_id"].unique().tolist()),
        "season_type_counts": {str(k): int(v) for k, v in player.season_type.value_counts().items()},
        "note": "Retained source values unchanged. The score reconciliation exposes incomplete/conflicting boxes; model builders can predeclare exclusion of affected games. It is a data-quality screen, never a prediction feature."
    }
    (args.output / "quality_report.json").write_text(json.dumps(qa, indent=2))
    counts = player.groupby("season").agg(player_rows=("player_id", "size"), unique_players=("player_id", "nunique"), games=("game_id", "nunique"), observed_boxscores=("boxscore_observed", "sum"), dnp_rows=("did_not_play", "sum"), first_game=("game_date", "min"), last_game=("game_date", "max"))
    counts.to_csv(args.output / "season_counts.csv")
    cutoff = pd.Timestamp("2025-01-01", tz="UTC")
    summary = {"generated_at_utc": utc_now(), "seasons": [args.start, args.end],
        "player_rows": len(player), "observed_player_games": int(player.boxscore_observed.sum()),
        "dnp_rows": int(player.did_not_play.sum()), "players": int(player.player_id.nunique()),
        "games": int(player.game_id.nunique()), "team_rows": len(team), "game_schedule_rows": len(home),
        "quarantined_missing_identifier_rows": len(rejected), "duplicate_player_keys": player_duplicates,
        "date_min": player.game_date.min().isoformat(), "date_max": player.game_date.max().isoformat(),
        "evaluation_cutoff_utc": cutoff.isoformat(),
        "pre_2025_observed_rows": int((player.boxscore_observed & (player.game_date < cutoff)).sum()),
        "2025_plus_observed_rows": int((player.boxscore_observed & (player.game_date >= cutoff)).sum()),
        "missingness": {c: int(player[c].isna().sum()) for c in player.columns},
        "target_min_max": {c: [float(player[c].min()), float(player[c].max())] for c in STAT_MAP.values()},
        "notes": ["All source-listed season types retained: filter season_type for desired competition.",
            "DNP rows retain null outcome statistics; do not treat them as zero prop outcomes.",
            "boxscore_observed means points observed and did_not_play is false; it does not imply a listed sportsbook market.",
            "source_active_flag/position/source_reason can reflect mutable roster metadata, not historical pregame knowledge.",
            "Same-game scores/statistics, starter_actual and ejected_actual are retrospective; lag before prediction.",
            "Minutes reflect the provider's rounded boxscore minutes and can be missing for very brief appearances.",
            "This is ESPN-derived game data, not historical PrizePicks/Kalshi/Polymarket odds or results."]}
    (args.output / "data_summary.json").write_text(json.dumps(summary, indent=2))
    licenses = []
    for repo, name in [("hoopR-nba-data", "LICENSE.md"), ("sportsdataverse-data", "LICENSE")]:
        url = f"https://raw.githubusercontent.com/sportsdataverse/{repo}/main/{name}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        local = args.output / (repo + "-LICENSE.txt")
        local.write_text(response.text)
        licenses.append({"url": url, "retrieved_at_utc": utc_now(), "local_file": local.name})
    manifest = {"created_at_utc": utc_now(), "attribution": "NBA data collected from ESPN and compiled by hoopR/SportsDataverse; normalized by this pipeline.",
        "upstream_repository": SOURCE_ROOT, "loader_reference": "https://github.com/sportsdataverse/hoopR/blob/main/R/load_nba.R",
        "license_note": "Producer repository publishes CC BY 4.0; distribution repository publishes MIT. Included verbatim copies; these do not establish ownership of third-party ESPN/NBA marks or content.",
        "licenses": licenses, "assets": provenance}
    (args.output / "source_manifest.json").write_text(json.dumps(manifest, indent=2))
    dictionary = """# NBA data dictionary

Attribution: ESPN-derived NBA boxscores compiled by hoopR / SportsDataverse.
See source_manifest.json for exact download URLs, UTC timestamps, hashes and licenses.
Data collection and storage run only on GitHub-hosted Actions runners. Raw cache is
`SPORTS_PROPS_CACHE/nba` when configured, otherwise `RUNNER_TEMP/nba`.
The evaluation cutoff is January 1, 2025 at 00:00 UTC; rows on/after it are held out.

- `player_games.parquet`: one row per source-listed player-game, unique `(game_id, player_id)`; DNP rows retained.
- `team_games.parquet`: one row per team-game, unique `(game_id, team_id)`; same-game team context for lagging.
- `games.parquet`: one row per game with known home designation, UTC start, teams and final scores.
- `rows_missing_identifiers.parquet`: source rows excluded only because game/player/date keys are missing; no invented IDs.
- `game_date`: UTC timestamp, derived from ESPN game start; `game_local_date` retains the provider's Eastern calendar date.
- `season`: season END year (2002 means 2001-02); `season_type`: ESPN type (2 regular season, 3 postseason; inspect source counts for others).
- `game_id`, `player_id`, `team_id`, `opponent_id`: ESPN identifiers encoded as strings. Franchise IDs are preferable to changing abbreviations.
- `team`, `opponent`, `team_name`, `player_name`, `position`: source labels, potentially mutable roster attributes. Position is not a verified point-in-time injury/role source.
- `is_home`: nullable boolean, missing when designation is unknown.
- `points`, `rebounds`, `assists`, `threes`, `steals`, `blocks`, `turnovers`, `fouls`, shooting and rebound counts: actual game outcomes; missing remains missing.
- `minutes`: source minutes, often rounded integers. Do not infer DNP solely from zero/missing minutes.
- `did_not_play`: explicit source DNP flag. `boxscore_observed`: false for explicit DNP or missing points; useful training outcome eligibility flag.
- `starter_actual`, `ejected_actual`, scores and same-game statistics: known after the game; MUST NOT be used unlagged as pregame predictors.
- `source_active_flag`, `source_reason`: preserved only for provenance, not reliable historical lineup/injury predictors.
- `season_counts.csv`, `data_summary.json`: coverage, split counts, missingness and quality checks.
- `quality_report.json`, `team_score_reconciliation.parquet`: missingness and identity checks; team totals that disagree with recorded player points are retained and explicitly flagged for conservative training exclusions.
- `quality_report.json.flagged_game_ids`: unique game IDs with at least one missing or inconsistent player-score sum; exclude these games before constructing rolling histories or evaluating models. Never use this retrospective quality flag as a predictive feature.

No statistical outcomes were filled with zero. A recorded zero is a genuine source value. No moneyline/prop quotes, historical lineups, timestamps of injury announcements, or bettable-market eligibility are supplied here.
"""
    (args.output / "DATA_DICTIONARY.md").write_text(dictionary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ["missingness", "target_min_max", "notes"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
