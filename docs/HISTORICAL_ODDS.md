# Historical player-prop lines

Game statistics and betting quotes are different datasets. The development cutoff is January 1, 2025 UTC; only quotes from 2025 onward can evaluate this frozen experiment. Earlier quotes may support future training experiments but cannot be added to this holdout protocol after inspecting results.

No complete, verified, free 2025-onward NBA/NFL player-prop quote history has been established for PrizePicks, Kalshi, and Polymarket together. The default odds collector produces a **quarantined feasibility sample**, with every candidate marked `usable_for_roi=false`. It does not train the statistical models or calculate returns.

| Source | What is available | Gap |
|---|---|---|
| [The Odds API](https://the-odds-api.com/historical-odds-data/) | Documented player-prop history beginning May 3, 2023, at five-minute snapshots | Paid access; bookmaker/market coverage must be checked; no purchase has been made |
| [Kalshi historical API](https://docs.kalshi.com/getting_started/historical_data) | Historical markets, trades, and candles, with live/archive separation | Player props were introduced later than 2023; candles do not establish queue position or executable size; historic fees/rules need versions |
| [Polymarket US historical prices](https://docs.polymarket.us/api-reference/price-history/get-price-history) | Separate U.S. API with quote history | U.S. and global products are distinct; a chart is not a fill simulator or a complete 2023 prop archive |
| [PrizePicks payouts](https://www.prizepicks.com/help-center/payouts) | Current product rules and displayed lineups | No verified official bulk historical public player-prop API found; do not invent prices or treat a multi-pick selection as a standalone even-money wager |
| [Sports odds datasets sample](https://github.com/JacobiusMakes/sports-odds-datasets) | CC BY 4.0 sample attributed to ParlayAPI; source commit and license captured remotely | Mixed products and questionable timing; only a small, unverified 2026 subset is relevant |

The sample audit checks sport, timestamps, player-like selection names, supported statistics, period labels, thresholds, and conventional sportsbook identity. Rows at or after event start, unzoned timestamps, unsupported products, and obvious invalid selections are rejected. Remaining candidates still require an official schedule/player join and confirmation of price semantics. Virtual events, preseason/summer games, and PrizePicks pseudo-odds must not slip into an NBA/NFL regular betting evaluation.

The default public build collects only the explicitly licensed sample. The optional Kalshi example collector is disabled in the public workflow pending a review of redistribution terms and rule-version handling. No comprehensive Polymarket or PrizePicks quote dataset is claimed. One historical special proposition does not establish a systematic archive of conventional player props.

## Required normalized quote schema

Preserve `observation_time`, `event_time`, provider event ID, canonical game ID, player ID, statistic, threshold, side, price format, product, bookmaker/exchange, period, overtime convention, settlement rules/version, payout, fees, quoted size, source URL, and quality status. Player names alone are not join keys. A binary contract paying $1, sportsbook decimal odds, and a PrizePicks lineup multiplier require different return calculations.

Probability estimation should produce a distribution for each statistic, then evaluate the actual threshold. Same-game legs need a joint distribution. Before computing any return, use the quote available at the decision time, include realistic fills and fees, and reproduce DNP/push/cancellation rules. Data collected at close cannot justify an earlier decision.

## Context worth adding

Prior workload, opponent defense, rest, rotation changes, and dated injury news are plausible inputs. The initial models already use shifted performance, workload, rest, and opponent aggregates. NFL injury and snap tables are stored separately; they are not automatically valid pregame features. Exact defender assignments and expected lineups need timestamped sources. Stadium guests and similar anecdotes need a predeclared hypothesis and reliable pregame records; adding many retrospective explanations invites false discoveries.

The next investment in this project should be audited historical odds and injury publication histories, followed by prospective paper trading. More weakly sourced columns cannot substitute for a valid evaluation.
