# Model card

**Status:** baseline implementation awaiting a successful remote training/evaluation run. No performance, edge, or profitability claim is made. Published run reports, once generated, supply the measured results and exact cohort counts.

## Intended use

Research into pregame NBA and NFL player-stat forecasting, with reproducible temporal evaluation. Outputs estimate individual game statistics for a defined cohort. They do not establish player participation, a sportsbook's eligibility rules, or a profitable bet. No orders are submitted.

The project produces two sport-specific model bundles. Each target has its own scikit-learn `HistGradientBoostingRegressor`, using squared-error loss and fixed initial settings: 90 boosting iterations, learning rate 0.07, 15 maximum leaf nodes, minimum 50 samples per leaf, L2 regularization 5.0, maximum 127 bins, and random seed 41. Early stopping is disabled. The workflow's configured iteration count and source revision determine the actual run.

| Bundle | Regression targets | Scoring roles |
| --- | --- | --- |
| NBA | Points, rebounds, assists, made three-pointers | Observed player appearances |
| NFL | Passing yards, passing touchdowns | QB |
| NFL | Rushing yards | QB, RB, FB, WR |
| NFL | Receiving yards, receptions | RB, FB, WR, TE |

The scored cohort requires at least ten prior observed appearances, an observed target, and the applicable NFL role. Cold starts remain in the underlying dataset but are outside this baseline's scored cohort. The release report records any additional baseline-availability and quality exclusions.

## Features and availability

Player history includes previous-game values, rolling means over 3/10/20 prior appearances, ten-appearance standard deviations, prior workload, and NBA minutes. Schedule features describe home designation, calendar timing, competition type, NFL week, and rest since earlier appearances. Opponent context summarizes statistics allowed across the opponent's previous ten recorded games.

Outcome-derived values are shifted before rolling or merging. Current-game statistics, minutes, snaps, final scores, actual starter flags, closing prices after prediction time, and final-season aggregates are excluded. Unversioned NBA position metadata are not model inputs. NFL features and role selection use the prior recorded appearance's position; these retrospective source labels are not verified timestamped depth charts.

Injury, snap, roster-status, and player-master tables are not default model inputs. Their historical publication or availability cannot be established merely from their presence in a downloaded file. Historical boxscores themselves can contain later corrections, so this is a chronological forecast evaluation on the available archive, not a complete reconstruction of information available at each historical instant.

## Evaluation protocol

Parameters are fitted only on game dates before January 1, 2024 UTC. The complete 2024 calendar year calibrates empirical standardized residuals. Development ends at January 1, 2025 UTC; every later date is held out. Calendar dates govern this split even when they cross NBA/NFL season labels.

Model parameters and calibration residuals remain frozen during held-out evaluation. As time advances, rolling features may use outcomes from earlier held-out games that have already occurred. This simulates sequential pregame forecasting; it is not a single forecast made on January 1, 2025 for every later game. No held-out outcomes may influence model fitting, calibration, parameter selection, or decision thresholds.

The comparison baseline is the player's prior-ten-appearance mean for the target. Reports include MAE, RMSE, signed error, baseline comparison, yearly results, and empirical 80% interval coverage and width. Missing baseline values require an explicit policy and coverage reporting; they must not silently disappear from a model-versus-baseline comparison.

Intervals use the calibration year's empirical standardized residual quantiles, scaled by historical variability. They are an approximation, not a guarantee of conditional coverage and not verified probabilities of exceeding any betting line. Count distributions, correlations between props, and changes in playing time can make this approximation poor.

## Limits and next validation

- Participation and historical market availability are not modeled. Conditioning evaluation on observed appearances understates the uncertainty of deciding which players will play.
- Era changes, trades, injuries, role changes, sparse histories, and unusual games can reduce accuracy. Earlier data quality and retrospective quality exclusions affect coverage.
- A lower statistical error than a rolling baseline does not establish an advantage over market prices. Joint probabilities are needed for multi-pick payouts; independent marginal forecasts are insufficient.
- Profitability needs a separate evaluation with timestamped executable prices/lines, venue fees and payouts, settlement rules, voids, limits, and realistic fills. See [historical odds requirements](HISTORICAL_ODDS.md).

All data processing and training occur on GitHub-hosted Actions. Dataset and model archives remain in GitHub/cloud storage. See [data design](DATA_DESIGN.md) for source attribution, licensing, and provenance.
