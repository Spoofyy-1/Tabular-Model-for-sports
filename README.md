# Tabular Model for Sports

NBA and NFL player-stat forecasting with public historical data, chronological evaluation, and separate model bundles. Data collection, feature construction, training, and dataset storage run **only on GitHub-hosted Actions runners**. Local machines contain code and documentation only.

**Status:** the pipeline is being established; no successful training run or measured model performance is asserted here. A completed workflow produces the evaluation reports. There is no verified profitable betting backtest.

## Run in GitHub

1. Open [Actions](https://github.com/Spoofyy-1/Tabular-Model-for-sports/actions) in this repository.
2. Select **Build sports datasets and models**, then **Run workflow**.
3. Review the NBA and NFL job logs and generated evaluation summaries separately.
4. Find the published assets in [Releases](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases). Keep datasets, model archives, and row-level prediction files in GitHub/cloud storage; do not download them onto the local workstation.

The ingestion and training entry points enforce the hosted-runner restriction. Do not override it or use a self-hosted runner. The source repository is for code, documentation, and workflow definitions; generated datasets are release assets.

| Release asset | Contents |
| --- | --- |
| `dataset-nba.tar.gz` | NBA normalized tables, chronological feature splits, provenance, and quality reports |
| `dataset-nfl.tar.gz` | NFL normalized tables, separate context tables, feature splits, provenance, and validation |
| `model-nba.tar.gz` | NBA model bundle and generated evaluation outputs |
| `model-nfl.tar.gz` | NFL model bundle and generated evaluation outputs |

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
