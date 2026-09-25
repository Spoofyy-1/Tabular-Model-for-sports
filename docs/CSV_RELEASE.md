# Cloud CSV releases

The [CSV export run](https://github.com/Spoofyy-1/Tabular-Model-for-sports/actions/runs/36170200182) completed successfully on September 25, 2026. All six bundles are in [csv-36170200182-1](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/csv-36170200182-1). The source Parquet assets were processed exclusively on GitHub-hosted runners; no datasets were downloaded to the owner's machine.

| Archive | Contents |
| --- | --- |
| `csv-base-nba.tar.gz` | NBA player-game, game, team and model feature tables |
| `csv-base-nfl.tar.gz` | NFL player-game, roster, injury, snap and model feature tables |
| `csv-context-nba.tar.gz` | NBA play events, event aggregates and schedule context |
| `csv-context-nfl.tar.gz` | Depth charts, tracking summaries and newer injuries |
| `csv-matchups.tar.gz` | NBA shots, defender matchups and game identity mappings |
| `csv-odds.tar.gz` | The 116-row historical quote linkage audit |

Each archive contains `.csv.gz` tables, adjacent `.schema.json` files and original source documentation/licenses. Gzip changes compression, not the CSV format. Preserve source paths, column order and ID strings. Parse UTF-8 CSV with quoted cells; `\N` is the only null sentinel, and empty strings remain distinct. For pandas, use `keep_default_na=False`, `na_values=[r"\N"]`, and explicit schema-guided ID dtypes. Nested cells contain JSON; binary cells use hexadecimal. Do not infer leading-zero IDs as integers. Schemas retain Arrow source types and timezone information.

Every source archive hash was checked on the runner. Table summaries report row counts and CSV SHA-256 checksums; release asset manifests report archive checksums. Counts overlap across source and derived views and must not be summed as independent training observations. The NBA feature splits remain 620,323 development and 46,636 evaluation rows; NFL remains 420,864 and 21,303. The format conversion did not retrain models.

## Newly collected CSV data

[Enrichment run 36170742592](https://github.com/Spoofyy-1/Tabular-Model-for-sports/actions/runs/36170742592) also completed successfully. Its [separate release](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/enrichment-36170742592-1) contains:

| Archive | Verified coverage |
| --- | --- |
| `csv-nba_injuries.tar.gz` | 703 official NBA PDF reports from 2021-10-19 through 2024-12-31; 66,259 parsed rows, including 62,068 player statuses and 4,191 team-not-submitted rows; one quarantined row |
| `csv-nfl_plays.tar.gz` | 1,285,290 plays and 1,368,573 player-role rows (1999–2026), 478,989 participation rows (2016–2025), 190,539 FTN charting rows (2022–2026) |
| `csv-nba_crosswalk.tar.gz` | 1,092 identity-candidate rows across 2026/2027 snapshots, 928 complete candidates and 164 incomplete/unmatched rows |

The NBA reports use one 17:30 Eastern snapshot per game date. Of mapped rows, 3,158 have report labels at or after game start and cannot support a pregame decision. Some December 31 local games occur in 2025 UTC and are therefore holdout rows. Report headers are checked, but immutable historical web publication times are not independently verified. Player IDs are unresolved; this source is not automatically joined to training. See [injury documentation](NBA_INJURIES.md).

NFL participation for 2026 was not available. Recent participation files can be published only after a season, so an earlier-game measurement is not automatically available for an historical wager. Play outcomes, pressure, routes and charting are postgame measurements; use availability-audited earlier-game aggregates only. NFL play-by-play uses CC BY 4.0, while FTN and participation outputs retain their separate CC BY-SA 4.0 attribution. See [NFL play context](NFL_PLAYS.md).

The NBA identity candidates preserve upstream matching evidence and are not independently verified identities. Even a one-to-one mapping can match the wrong people. No automatic training join is enabled. See [identity-candidate documentation](NBA_IDENTITY_CANDIDATES.md).

All new sources remain separate from the frozen baseline-feature A100 experiment. CSV availability does not establish as-of correctness, betting profitability or complete live-market coverage.
