# Data design

The pipeline collects and stores datasets on GitHub-hosted Actions runners, then publishes per-sport archives as GitHub release assets. Do not run ingestion locally or download the resulting datasets to the workstation. Raw caches use the runner's temporary storage; normalized outputs live under the runner checkout's `data/nba` and `data/nfl` directories.

## Sources and attribution

| Dataset | Source | Requested coverage |
| --- | --- | --- |
| NBA player/team boxscores | [hoopR NBA data](https://github.com/sportsdataverse/hoopR-nba-data), compiled from ESPN and distributed through [SportsDataverse releases](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_player_boxscores) | Season-ending years 2002–2026 |
| NFL player game statistics and context | [nflverse data](https://github.com/nflverse/nflverse-data), including nflfastR-derived player statistics | Seasons 1999–2025 |
| NFL schedule | [nflverse/nfldata](https://github.com/nflverse/nfldata), maintained by Lee Sharpe and contributors | Requested NFL seasons |

The [NBA producer repository](https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md) publishes CC BY 4.0; the [SportsDataverse distribution repository](https://github.com/sportsdataverse/sportsdataverse-data/blob/main/LICENSE) publishes MIT. The NBA manifest preserves both license sources and copies. [nflverse-data](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md) publishes CC BY 4.0. Do not infer the same license for every upstream source: the NFL manifest records the separate schedule repository and the [nflverse upstream-terms notice](https://nflverse.nflverse.com/). NFL snap counts originate from Pro Football Reference. These statements do not grant rights to third-party names, marks, or underlying content.

Each ingestion run records exact asset URLs, download timestamps, hashes, transformations, and validation results. Public source files can be revised, so reproducibility depends on those manifests and retained release assets, not just a season number.

## Normalized records

`player_games.parquet` uses a unique `(game_id, player_id)` key. Common fields are `sport`, `game_id`, `player_id`, `player_name`, `team`, `opponent`, `game_date`, `season`, and `is_home`, followed by observed outcomes and source metadata. Join by source IDs, never by player names.

NBA identifiers are ESPN strings. `game_date` is a UTC start timestamp; `game_local_date` retains the source calendar date. NBA `season=2025` means the 2024–25 season. Team boxscores and a game schedule are separate tables.

NFL identifiers are GSIS player IDs and source game IDs. Franchise abbreviations are normalized while original values remain available. NFL `season=2024` includes postseason games played in early 2025. Missing kickoff times use an explicitly flagged date-only representation; they must not support claims of precise intraday availability.

NBA DNP rows retain null outcomes. `boxscore_observed` requires observed points and no explicit DNP flag; missing or zero rounded minutes alone do not establish nonparticipation. NFL absent player-game records likewise do not establish a zero outcome or DNP. Invalid player IDs are quarantined, and conflicting duplicate keys stop ingestion.

## Quality and temporal controls

NBA quality reports check missing targets, negative core counts, scoring identities, and whether player-point sums reconcile with team scores. `quality_report.json.flagged_game_ids` identifies games excluded conservatively before rolling histories and evaluation. This is a retrospective data-quality exclusion, not a predictive feature. Counts from earlier inspections are illustrative; each new run is authoritative.

NFL validation reports coverage, missing dates, identifier mapping, duplicates, and target missingness. Archived 1999–2000 data carry a legacy-quality flag. The default requests include regular-season and postseason records; generated metadata identifies competition types and the final scoring cohort.

Only strictly earlier observations may enter an outcome-derived feature. Current-game minutes, snaps, starter flags, final scores, and final season aggregates must not become current-game predictors. NBA unversioned position metadata are excluded from model inputs. NFL positions from prior recorded appearances constrain target-specific roles. Historical statistical corrections remain a source limitation because complete original publication versions are unavailable.

Roster, player-master, injury, and snap tables remain separate from the default features. Injury snapshots are not a complete publication history; `date_modified` does not prove when information was available to a bettor. Snap counts are postgame outcomes and would require lagging. No final injury, roster-status, or same-game snap fields enter the baseline.

## Chronological split

| Use | Game timestamp in UTC |
| --- | --- |
| Parameter fitting | Before `2024-01-01T00:00:00Z` |
| Residual calibration | From `2024-01-01T00:00:00Z` through, but excluding, `2025-01-01T00:00:00Z` |
| Held-out evaluation | On or after `2025-01-01T00:00:00Z` |

Development therefore includes the whole 2024 calendar year. A January 2024 NFL playoff game belongs to calibration even if its season is 2023; an October 2024 NBA game also belongs to calibration even if its season is 2025. Split by `game_date`, not `season`.

The collected data contain no verified historical PrizePicks entries, Kalshi/Polymarket order books, fees at execution, or settled trade ledger. See [historical odds requirements](HISTORICAL_ODDS.md) before interpreting forecast accuracy as betting value.
