# Tabular Model for Sports

NBA and NFL player-stat forecasting with public historical data, chronological evaluation, and separate model bundles. Dataset ingestion and CSV exports run on **GitHub-hosted Actions runners**. The owner additionally authorized a finite training job on the existing **Azure A100**, with outputs in private Blob Storage. Local machines contain code and documentation only.

**Status:** the [first cloud build](https://github.com/Spoofyy-1/Tabular-Model-for-sports/actions/runs/36055555015) succeeded. [Datasets, trained models, and evaluation reports are published](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/snapshot-36055555015-1). There is no verified profitable betting backtest.

**CSV datasets are published:** [download locations and schemas](docs/CSV_RELEASE.md). The [CSV release](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/csv-36170200182-1) preserves all baseline/context tables as compressed CSV, along with source metadata. A [new enrichment release](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/enrichment-36170742592-1) adds official NBA injury entries, NFL play context and identity candidates. Keep these assets in the cloud.

| Cleaned model table | Development, through 2024 | Holdout, 2025 onward | Total |
| --- | ---: | ---: | ---: |
| NBA | 620,323 | 46,636 | 666,959 |
| NFL | 420,864 | 21,303 | 442,167 |

The underlying normalized sources contain 848,095 NBA roster-listed player-game rows (including DNP/missing boxes) and 475,586 NFL player-game rows. Model tables apply the documented quality exclusions. The NFL table retains defensive and special-teams players; target heads restrict roles. Model evaluation also requires ten earlier appearances, so scored counts are smaller than table counts.

Initial held-out mean absolute error (MAE; lower is better):

| Target | Model MAE | Previous-ten-appearance mean MAE | Error reduction |
| --- | ---: | ---: | ---: |
| NBA points | 4.630 | 4.761 | 2.75% |
| NBA rebounds | 1.911 | 1.970 | 2.99% |
| NBA assists | 1.348 | 1.384 | 2.59% |
| NBA made threes | 0.897 | 0.909 | 1.29% |
| NFL passing yards | 64.278 | 71.045 | 9.53% |
| NFL passing touchdowns | 0.879 | 0.946 | 7.12% |
| NFL rushing yards | 8.783 | 9.090 | 3.38% |
| NFL receiving yards | 16.289 | 16.653 | 2.19% |
| NFL receptions | 1.243 | 1.276 | 2.56% |

These are point estimates, not significance tests. The evaluation includes players without confirmed offered props and conditions on recorded appearances; low-volume NFL players affect aggregate errors. Accuracy against this baseline does not establish an edge against a sportsbook or prediction-market price. No model settings were tuned using these holdout results.

## Run in GitHub

1. Open [Actions](https://github.com/Spoofyy-1/Tabular-Model-for-sports/actions) in this repository.
2. Select **Build sports datasets and models**, then **Run workflow**.
3. Review the NBA and NFL job logs and generated evaluation summaries separately.
4. Find the published assets in [Releases](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases). Keep datasets, model archives, and row-level prediction files in GitHub/cloud storage; do not download them onto the local workstation.

The original ingestion/training entry points enforce the hosted-runner restriction. The separate `azure/train_gpu.py` requires an actual Azure Job and verifies A100/CUDA before downloading data. Do not override either guard or use a self-hosted workstation runner. The source repository is for code, documentation, and workflow definitions; generated datasets are release assets.

| Release asset | Contents |
| --- | --- |
| `dataset-nba.tar.gz` | NBA normalized tables, chronological feature splits, provenance, and quality reports |
| `dataset-nfl.tar.gz` | NFL normalized tables, separate context tables, feature splits, provenance, and validation |
| `model-nba.tar.gz` | NBA model bundle and generated evaluation outputs |
| `model-nfl.tar.gz` | NFL model bundle and generated evaluation outputs |

## Additional context collection

The separate **Expand sports context datasets** workflow collects more information without refitting the original model bundles. Its outputs are published in separate `context-*` releases, sorted and partitioned with source manifests, schemas, identity checks and temporal flags.

**Published:** [expanded context release](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/context-36094215424-1), built successfully in [GitHub Actions](https://github.com/Spoofyy-1/Tabular-Model-for-sports/actions/runs/36094215424). It adds 14,205,388 NBA play events, 6,328,071 shot records, 2,143,437 defender-matchup records, 1,944,255 NFL depth-chart records, 24,833 weekly NFL tracking records and 6,760 newer injury reports. These tables overlap and are not a count of independent player-games. [Coverage and quality details](docs/CONTEXT_RELEASE.md).

| Archive | Additional information |
| --- | --- |
| `context-nba.tar.gz` | Play-by-play, shot/event proxies, schedules, venues and gaps between games |
| `context-matchups.tar.gz` | Shot coordinates and actual offensive-player/defender matchup statistics, with explicit NBA-to-ESPN game mappings |
| `context-nfl.tar.gz` | Next Gen Stats weekly tracking summaries, historical depth charts and updated injury snapshots |
| `context-odds.tar.gz` | Existing quote sample identity/timing audit and aggregate Kalshi coverage inventory |

Current-game shots, tracking results and attendance are postgame observations. They cannot enter a pregame model directly. The expansion preserves missing values and unmapped IDs, and does not make these new fields automatically model-ready. Development remains through calendar 2024; 2025 onward stays held out.

Code-triggered builds can reuse the first validated NBA/odds context snapshot when the collector and shared source files match exactly. Reused archives retain their original manifests and retrieval dates, and every archive hash is checked on the runner. To refresh upstream data, manually run **Expand sports context datasets** with `reuse_release` blank.

Collector details: [NBA event context](docs/NBA_CONTEXT.md), [NBA shots and defender matchups](docs/NBA_MATCHUPS.md), [NFL context](docs/NFL_CONTEXT.md), and [odds coverage](docs/ODDS_EXPANSION.md). Remaining priorities are recorded in [data gaps](docs/DATA_GAPS.md).

## Experiment

Development data include all available dates **before January 1, 2025 UTC**. Model parameters are fitted using dates before January 1, 2024; the complete 2024 calendar year calibrates residual intervals. Dates on or after January 1, 2025 form the held-out evaluation. These are calendar-date boundaries, not season boundaries.

Each sport has one bundle containing a separate `HistGradientBoostingRegressor` for each target:

| Sport | Targets |
| --- | --- |
| NBA | Points, rebounds, assists, made three-pointers |
| NFL | Passing yards, passing touchdowns, rushing yards, receiving yards, receptions |

Features describe earlier games: recent form over 3/10/20 appearances, previous workload or minutes, rest, and the opponent's prior ten-game allowed statistics. Evaluation compares predictions with a prior-ten-appearance mean and reports MAE, RMSE, bias, interval coverage, and yearly breakdowns. Cold starts and participation uncertainty limit the scored cohort.

## Data and limits

The default collection requests NBA season-ending years 2002–2026 from ESPN-derived hoopR/SportsDataverse boxscores, and NFL seasons 1999–2025 from nflverse. Actual coverage and exclusions come from each run's manifests. Null outcomes and absent appearances are not converted into fabricated zero performances.

Historical boxscores are not historical tradable quotes. Profitability requires timestamped lines, prices, fees, payouts, settlement rules, and realistic execution. The current pipeline does not establish those conditions or place bets.

- [Data design and source attribution](docs/DATA_DESIGN.md)
- [Model card and evaluation protocol](docs/MODEL_CARD.md)
- [Historical odds and betting evaluation requirements](docs/HISTORICAL_ODDS.md)

Data remain subject to their source licenses and applicable upstream terms; this repository does not claim ownership of ESPN, NBA, NFL, or third-party data.
