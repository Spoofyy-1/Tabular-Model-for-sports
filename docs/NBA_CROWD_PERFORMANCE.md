# NBA reported attendance and player performance

This collector prepares retrospective CSV comparisons using the existing hash-pinned NBA player/game archive and 25 pinned ESPN-derived schedule assets. Collection and transformation run only on GitHub-hosted runners. The source transfer is capped at 120 MB; no source dataset is stored or processed on the owner's Mac.

Published and verified: [crowd-performance-36186734904-1](https://github.com/kennynakao/Tabular-Model-for-sports/releases/tag/crowd-performance-36186734904-1). The hosted workflow and all declared coverage gates passed. The compressed CSV archive is 32,037,792 bytes. Thirty-one synthetic checks passed before collection; the hosted run verified the actual source hashes, schemas and coverage.

## Verified coverage

| Measure | Count |
| --- | ---: |
| Canonical schedule/base game links | 32,198 |
| Linked roster-listed player/game rows | 847,926 |
| Distinct games represented by linked player rows | 32,179 |
| Distinct linked players | 2,577 |
| Excluded player rows without a validated schedule link | 169 |
| Eligible player outcomes | 666,823 |
| Eligible rows with positive integral reported attendance | 643,269 |
| Distinct games represented by those positive-attendance rows | 30,974 |
| Player/attendance descriptive summary rows | 77,057 |
| Separate attendance-band denominator rows | 191 |

Eligible outcomes comprise 591,641 pre-2024 rows, 28,546 calendar-2024 rows and 46,636 held-out rows from 2025 onward. The linked roster panel includes 730,094 reported appearances and 117,832 explicit DNP records. Eligibility additionally requires an observed boxscore and the documented quality checks; it is not identical to the roster panel or the original modeling table.

Of 32,290 source schedule rows, 92 remain unmatched or quarantined. There are 1,207 reported zeros in attendance, 9,262 missing capacities and 6,437 ratios above one. Those schedule-level counts include audit rows and must not be presented as counts of eligible player games. No fractional count outcomes or fractional attendance/capacity values were found. Sources transferred 107,170,501 bytes on the hosted runner, below the 120 MB cap.

## Tables and interpretation

- `game_context_audit.csv.gz` preserves reported attendance, source-listed capacity, venue identity, neutral-site/completion flags and exact game-join checks.
- `base_game_join_audit.csv.gz` records the source game identities and whether a valid schedule link exists.
- `player_game_context.csv.gz` links roster-listed player outcomes to accepted game context while preserving participation, missing values and exclusion reasons.
- `excluded_player_game_audit.csv.gz` retains rejected records for reconciliation.
- `player_attendance_descriptive_summary.csv.gz` gives fixed attendance-band comparisons by player, season, season type, nominal home status, neutral-site status and evaluation period, with explicit sample counts.
- `attendance_band_denominators.csv.gz` keeps distinct games and roster-listed player rows separate for each crowd group.

The archive includes schemas, hashes, source provenance, source license notices and an aggregate coverage report. CSV uses literal `\N` for null, distinct from zero and empty text. Game, team and player identifiers remain strings.

ESPN game IDs, both opponents, timestamps, season and season type must agree. Duplicated/conflicting identities are quarantined at game level. The source's boxscore reconciliation flags exclude incomplete or conflicting games from primary outcome comparisons. Attendance analysis requires a completed game. NBA season labels refer to the season-ending year; calendar evaluation periods are tracked separately.

An explicit DNP or unknown participation is not a zero performance. A reported appearance with observed points can contribute to per-game outcomes even if minutes are rounded to zero or missing. Metric-specific missingness and invalid values remain visible. Per-minute comparisons require a separate positive-minutes denominator. Game counts must remain distinct from player-row counts because all players share the same game's reported crowd exposure.

## Limits

Reported attendance does not establish how many people occupied seats, supported each team or made noise. Zero reported attendance remains an unverified source value, including during periods when venue restrictions varied. Current source-listed capacity has no verified historical validity. Ratios require finite nonnegative attendance and positive capacity; ratios above one are flagged and preserved rather than clipped.

The summaries are unadjusted descriptions of the observed sample. Player selection, minutes, opponents, home advantage, venue size, postseason demand and pandemic restrictions can explain apparent differences. These outputs do not estimate a causal crowd effect or demonstrate a betting advantage.

Actual attendance and same-game outcomes are retrospective. They are not historical pregame forecasts and are not automatically admitted into model features. Data before 2024, calendar 2024, and 2025 onward remain separate. No new model is trained by this collector.

Source contracts and reuse limitations are documented in [the weather/crowd audit](WEATHER_CROWD_SOURCE_AUDIT.md). The producer's software/distribution licenses do not establish ownership of all upstream NBA/ESPN rights.
