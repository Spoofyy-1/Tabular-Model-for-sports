# Historical odds expansion and identity audit

The new collector checks whether the existing licensed prop quotes refer to real NBA/NFL games and identifiable players. It also inventories a bounded portion of Kalshi's historical player-prop markets. It does not train models or establish betting profitability. Real data is downloaded and processed only on GitHub-hosted Actions.

## Hosted invocation and outputs

```sh
python context/odds_context.py \
  --release-tag snapshot-36055555015-1 \
  --output-dir data/context/odds \
  --kalshi-pages 1
```

Dependencies: pandas, pyarrow, requests. Both `GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted` must be genuine runner settings. The collector reads the published NBA, NFL and odds archives on the runner; it does not load model artifacts. The existing three archives total roughly 88 MB. The total network budget is capped at 500 MB and 1,000 requests. Kalshi pagination is bounded to 0–8 pages per each of six series. No paid account or API key is required.

The output directory contains:

- `quote_link_audit.parquet`: licensed source quotes with deterministic identity/time matches, explicit exclusions, and pricing-audit status.
- `PARLAY_LICENSE.txt`: upstream license retained with attribution to [ParlayAPI](https://parlay-api.com).
- `context_summary.json`: aggregate coverage, exclusions, source URLs/hashes, license information, and remaining information-availability gaps. Logs contain aggregate counts only.

Publish these through the remote release workflow. Do not download the dataset artifacts to the owner's machine.

## What a successful match establishes

Team names use an explicit alias dictionary. Both opposing teams and a UTC start time must identify exactly one schedule game. The maximum accepted source/schedule difference is 15 minutes, recorded for review rather than silently overwritten. Unzoned timestamps are rejected. The quote must precede both reported start times by at least one minute. Every rejected case retains its reason.

Player names are compared after deterministic case, accent and punctuation normalization against the player identities for the matched game. Multiple IDs, unsupported aliases, and absent identities remain unresolved; there is no fuzzy match or guessed player. This is retrospective identity validation. It must not be interpreted as evidence that the player's participation or lineup was known when the quote was posted. No player outcomes are used or exported, and no outcome-dependent strategy is selected.

Canonical stats include `threes`, full-game basic statistics and explicitly mapped combinations. The earlier collector rejects detected period markets; the source stat mapping still requires validation against each book's historical market/settlement rules. A successful link is labeled `eligible_for_further_pricing_audit`. **Every row remains ineligible for a betting-ROI claim.** DNP, pushes, overtime, cancellations, actual fees, limits, quote freshness and available size still need verification.

The frozen chronology remains: fit before 2024, calibrate during 2024, and hold out game dates from January 1, 2025 UTC onward. This audit does not fit or select a model. It reports coverage by year and temporal partition, so the 2026-only sample cannot masquerade as development-period history.

## Source expansion findings

The [ParlayAPI public sample](https://github.com/JacobiusMakes/sports-odds-datasets) is explicitly CC BY 4.0. Its provider describes a broader archive beginning in 2022, but the open 50,000-row sample covers only part of 2026. The previous audit found mislabeled non-player/virtual markets, incomplete timestamps, observations after scheduled starts, and pick'em pseudo-odds. Existing source availability does not establish a complete 2023–2025 panel. This stage improves the quality of the released sample instead of inventing missing prices.

[The Odds API](https://the-odds-api.com/liveapi/guides/v4/#get-historical-event-odds) documents player-prop history after May 3, 2023, with historical access on paid plans. This collector does not purchase or access a paid plan. The inspected public NFL repository `theedgepredictor/odds-data-pump` lacked a clear dataset license, and its inspected 2023-season records carried 2025 update timestamps; it is not incorporated as contemporaneous pregame history. Newly located [SharpAPI samples](https://github.com/Sharp-API/SharpAPI-Sample-Data) and [SmartStake's licensed prop dataset](https://huggingface.co/datasets/SmartStake/mlb-player-props/blob/main/README.md) cover other sports, so they are not substituted for NBA/NFL data.

[Kalshi's historical market endpoint](https://docs.kalshi.com/api-reference/historical/get-historical-markets) supports paginated series inventories. The collector checks NBA points/rebounds/assists and NFL passing/rushing/receiving yards. It publishes only series counts, opened-month coverage and pagination completeness; raw contracts, rule text and quotes are not exported because a public redistribution license has not been established. The minimum date in a bounded sample is explicitly not called the platform's first listing date. Price history would additionally require the [historical candle endpoint](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks), fees and depth validation.

Polymarket has genuine older bespoke athlete markets, including a 2023 LeBron scoring-record event. That does not establish a systematic NBA/NFL conventional prop archive. The earlier CLOB history request returned HTTP 403, and no bypass is attempted. [Price-history documentation](https://docs.polymarket.com/api-reference/markets/get-prices-history) provides a future authorized access route, but no Polymarket quotes are fabricated or published here.

No documented PrizePicks public historical data API was verified. Its [terms](https://www.prizepicks.com/help-center/terms-of-service) restrict unauthorized automated means and commercial reuse. No private endpoint, bot evasion or account access is used. A research license or authorized timestamped export with the actual payout/rules version would be needed for a defensible historical PrizePicks return study.
