# NBA contextual expansion

The separate `context/nba_context.py` collector adds [hoopR/SportsDataverse schedules](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_schedules) and [play-by-play events](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_pbp). It runs only on GitHub-hosted Actions runners, caches under `RUNNER_TEMP`, and writes release-bound data under `data/context/nba`. No real sports data should be downloaded to the owner's machine. This collector does not train models or alter the frozen baseline.

The primary [loader and schema documentation](https://github.com/sportsdataverse/hoopR/blob/main/R/load_nba.R) specifies ESPN IDs and season-ending years. Release metadata inspected on September 24, 2026 lists 25 play-by-play Parquet assets for 2002–2026 totaling **555,539,962 compressed bytes**, plus roughly 3 MB of corresponding schedules. This is verified asset metadata, not a claim that every game/player has complete event coverage. Actual coverage, schemas and quality are measured remotely in `context_summary.json`.

## Tables and meaning

| Remote table | Grain | What it adds |
|---|---|---|
| `events/season=YYYY/events.parquet` | Source play event | ESPN game/team/actor IDs, event type/text/order, period, game clock, source wallclock, scores, shooting/scoring flags and supplied shot coordinates |
| `schedules.parquet` | Game | Venue ID/name/city/state, neutral site, attendance, capacity, scheduled start, completion state |
| `team_schedule_context.parquet` | Team-game | Opponent, venue, previous listed game date/venue, hours and UTC calendar days between listed games |
| `player_game_event_context.parquet` | Primary actor-game | Observed event counts, shooting/scoring events, coordinate availability, text-identified three-point events, overtime events and described shot distance |

IDs use the same ESPN namespace as existing NBA boxes; `game_id` plus `player_id` is the player-game join key. Actor IDs are nullable and retain source semantics. The second/third actor must **not** be interpreted as a defender. Actual overlap with the current boxes needs a separate remote join audit. No fuzzy name matching or invented IDs are used.

Event summaries include only players explicitly supplied as primary actors. Zero counted events means none matched the definition within the available source events, not zero true game activity. Shooting flags may include different types of shooting plays across provider eras, so these counts are **not official field-goal attempts or makes**. Three-point and distance measures are text-derived proxies; missing coordinates and distances remain missing. Full event text is retained to permit auditable improvements. Duplicated non-null event IDs are reported, not silently removed; do not use those partitions for model histories until duplicates are reconciled.

## Time and leakage boundaries

Game dates before 2024 are fit data, calendar 2024 is calibration, and 2025 onward is holdout. These labels use UTC game start, not season year, so games from the 2024–25 season split correctly. Unknown dates receive a separate label. `source_game_date_precision` distinguishes source timestamps from calendar-date-only fallbacks. Date-only records on either side of the 2024 or 2025 year boundary receive `date_precision_unresolved`; hourly schedule gaps are missing whenever either endpoint is date-only. No context collector operation tunes against the holdout.

Play events, attendance, completion state and player event summaries describe what actually happened. Only earlier completed games can supply them to a later prediction. Same-game attendance must never enter a pregame training feature. A venue/clock in a current archive does not prove that the value was published at the historical betting cutoff; the collector marks historical publication time unverified. Source wallclock is not a certified ingestion or news timestamp. Rest intervals refer to listed starts, not verified player rest, injuries, travel distance, or travel direction. UTC calendar-day gaps must not be presented as local-calendar back-to-backs without a validated timezone conversion.

## Provenance and licensing

`context_summary.json` captures per-season row counts, game/date coverage, train/calibration/holdout counts, schemas, missingness, duplicate keys, schedule overlap, exact source URLs, retrieval times, SHA-256 hashes, input sizes and license copies. The [producer license](https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md) states CC BY 4.0 for data, code and documentation; the [distribution repository](https://github.com/sportsdataverse/sportsdataverse-data/blob/main/LICENSE) uses MIT. Included notices attribute ESPN-derived data to hoopR/SportsDataverse and describe our normalization/aggregation. This does not establish independent rights to third-party marks or content.

## Further sources that need additional validation

- NBA Stats tracking/matchup endpoints could add potential assists, rebound chances, touches and defensive assignments, but reliable historical bulk coverage, point-in-time semantics, ID crosswalks and redistribution rights need verification. The current ESPN actor roles are not a substitute.
- Historical NBA injury reports need the actual publication time and status changes before the bet cutoff. A present-day roster status or after-game DNP reason is insufficient.
- Reconstructing five-player rotations from substitution events requires event completeness checks, starting lineups and possession/minute reconciliation. This ingestion retains event evidence without claiming validated lineups.
- Stadium guests or celebrity attendance lack a verified, comprehensive, timestamped source here. No celebrity or anecdotal fields are invented.
- Verified odds/prop snapshots remain a separate dataset; this play archive provides no PrizePicks, Kalshi or Polymarket player-prop histories.
