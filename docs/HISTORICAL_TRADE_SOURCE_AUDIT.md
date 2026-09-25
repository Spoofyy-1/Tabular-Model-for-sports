# Historical trade sources: bounded next pilot

Audit date: 2026-09-25. Research only; no collector was implemented and no dataset files were downloaded locally. Inspection used primary documentation and repository/file metadata. **Sports coverage, exact event identities and fill counts remain unverified.**

## Strongest next source: a pinned on-chain archive

The [TimeSeventeen/Polymarket-v1 authors' source card](https://huggingface.co/datasets/TimeSeventeen/Polymarket-v1/blob/5aa1b9d52316a8b2e789e81c8ae42c7ed532e8aa/README.md) licenses the archive under **CC BY 4.0**, allowing reuse with attribution. Preserve the notice, source revision, authors and change description. The [authors' paper](https://arxiv.org/abs/2606.04217) describes settlement history from November 2022 through April 2026; this does not establish complete sports coverage.

The source has three relevant layers. `OrderFilled/` contains monthly nominal maker/taker fills with unique chain/block/log identifiers. `daily_aligned/` and `daily_aligned_multi/` contain cleaned binary and negative-risk daily records. The publisher removes relayer/router records in the cleaned layers. Daily filenames use **UTC+8**. All are settlement observations; none supplies historical order-book depth or quote availability.

Key raw fields: `id`, `block_timestamp`, `token_asset_id`, `token_amount`, `usdc_amount`, `price`, `fee_usdc`, maker/taker directions. Cleaned records add `condition_id`, `asset_id`, `market_slug`, category and outcome metadata, but their documented schema omits the raw fill ID. Block timestamps are epoch seconds. Final resolution labels and retrospectively enriched metadata must be excluded from decision-time features.

## Immutable file metadata verified without downloading files

The [Hugging Face repository metadata endpoint](https://huggingface.co/api/datasets/TimeSeventeen/Polymarket-v1) reported `private=false`, `gated=false`, license `cc-by-4.0`, revision:

`5aa1b9d52316a8b2e789e81c8ae42c7ed532e8aa`

File metadata was verified through `POST https://huggingface.co/api/datasets/TimeSeventeen/Polymarket-v1/paths-info/5aa1b9d52316a8b2e789e81c8ae42c7ed532e8aa` with a fixed path list. The hashes below are the returned LFS SHA-256 object hashes, not the Git pointer hashes. These are source-object contracts, not collected trade rows.

| Path | Bytes | SHA-256 |
| --- | ---: | --- |
| `daily_aligned/2024_07_14.parquet` | 183828 | `01b97d4f42ff22b8112813818aa4b015e53f63443568c40dbae2dffe5427e95e` |
| `daily_aligned/2024_07_15.parquet` | 195094 | `3864ef3bb324e2f32760e77a13c67036ef417e3882f86a746121a79bf15f2bcd` |
| `daily_aligned/2025_02_09.parquet` | 1469303 | `5d88556ea3dfa565adf2b461a82d1598dfc3d9dab6ee6f09a9a3cf33ccc91cce` |
| `daily_aligned/2025_02_10.parquet` | 1809211 | `23fc6ee2aa853758dfe107b4ffe1361f29ef866ace06044dfee3bcfd50e1c44a` |
| `daily_aligned/2025_06_22.parquet` | 2532483 | `6d8635382bb236511f35488f216819a29ac483ba41c611c3f04142a41868d56a` |
| `daily_aligned/2025_06_23.parquet` | 2668689 | `da04586309a96ee3ab9ae773c65ed690b44f3dbd378a2139ec359f329665507d` |
| `daily_aligned_multi/2024_07_14.parquet` | 424462 | `b8c3bfe862eca89734c3ed004af42582b05848d8f75a6bc631ed0209c9070224` |
| `daily_aligned_multi/2024_07_15.parquet` | 499984 | `34cb9e52525206a0e34152aae3d1cdbf148655b6fa3b888eebfb2a39ba9bb7e1` |
| `daily_aligned_multi/2025_02_09.parquet` | 2261375 | `9b94a59256470f30b1c9d0628a16fc0150ee8c26cc583c0c81539b89a63735d3` |
| `daily_aligned_multi/2025_02_10.parquet` | 2665060 | `164ae5772886a2ac88791dd161c420c01b8453693825aa22e72d119a75de90fa` |
| `daily_aligned_multi/2025_06_22.parquet` | 1380523 | `11ff5a116e32efe7f516960ad6e6ea5d82d9b29bb1236e38859ccf10cfb2bb64` |
| `daily_aligned_multi/2025_06_23.parquet` | 1744324 | `304cac05b081cbc18c5ba6a0551208d0970dc9592b6a8e174ceea7dcc9e76027` |
| `OrderFilled/2024_07.parquet` | 34056680 | `03dc8c08aa30edaa883d8d2ec3a5f39c25e9f8e5302825e0a1714e21c3d160da` |

The twelve daily objects total **17,834,336 bytes**. Adding the July raw object gives **51,891,016 bytes**, fitting a proposed **60 MB all-input / 50 HTTP-attempt / 300-second hosted-only pilot**, including small documentation metadata. Require content hashes before parsing, explicit trusted redirect hosts and bounded decompression/rows. Do not download whole repositories or let a dataset library implicitly fetch unlisted shards.

For later expansion, verified monthly objects are `OrderFilled/2025_02.parquet` (218,729,504 bytes, SHA-256 `7a8700f3e157fa6e832388c0e2ef45acf7b7b63c776b13397b063beb13756c5a`) and `OrderFilled/2025_06.parquet` (200,420,668 bytes, SHA-256 `cbb293e439dbc433fe7c95bf8da6950172a197745d69867df1113a49565d1617`). Neither belongs in the initial 60 MB pilot.

## Proposed audit and deduplication contract

These date pairs are fixed candidate windows for tennis, NFL and NBA research. They are not proof that the requested match markets exist. First report category/slug/condition/token coverage and schema compliance on the hosted runner. A verified sport category plus exact market identity can enter a source-coverage CSV; uncertain categories remain quarantined. Do not match a game using the resolved winner or price pattern.

Retain filename timezone and actual block timestamp independently. Use explicit UTC intervals, not filename dates as UTC. Preserve original source fees and raw token prices; do not substitute current fees or convert a No token into a Yes execution.

Raw fills can be deduplicated by validated chain/block/log ID. Conflicting duplicate IDs must be quarantined. A transaction hash alone is insufficient because one transaction may contain several fills. Cleaned records without fill IDs must not be collapsed by timestamp/price/size: distinct fills can share those values. Report duplicate-signature groups, and distinguish source-cleaned row totals from independently validated unique-fill volume. The July raw object can support a bounded identity audit; it does not automatically establish a one-to-one join to cleaned rows. Apply and report the publisher's relayer/router exclusions before any economic-volume claim.

Settlement time is not matching-engine arrival time or the time a strategy could observe the trade. Without archived observation latency, depth and game/point clocks, these records support retrospective trading-activity descriptions only. No training, executable strategy, complete venue-volume or profitability result follows from this pilot.

## Official API alternative: useful semantics, unresolved publication rights

The [official Data API v2 documentation](https://data-api.polymarket.com/v2/docs) embeds an OpenAPI specification. It documents public unauthenticated `/v2/trades`, cursor pagination (maximum 1,000 rows/page), settling transaction hashes and block-time seconds. `taker_only=true` serves a fill once; disabling it includes maker rows. Its minimum size filter defaults to 0.01, including when zero is supplied, so completeness below that threshold is not established.

Condition/event queries use a fixed three-year window and ignore `start`/`end`; only user queries honor those bounds. The public response lacks the cursor's internal sequence ID. Use bounded cursor walks with duplicate/overlap audits, and filter timestamps afterward; do not claim an exhausted sample is an exhaustive historical interval. A current Gamma cumulative-volume field cannot replace this history.

The specification's MIT label licenses the API description, not necessarily response redistribution. The [Polymarket Institute research guide](https://institute.polymarket.com/data) supports research access but does not supply an explicit raw-data redistribution grant. Prefer the separately licensed archival pilot for public release; no live API collector was implemented in this pass.

## Existing PMXT coverage

The existing market source is **PMXT CC BY 4.0**. The [archive catalog](https://archive.pmxt.dev/Polymarket/v2) and [v2 overview](https://archive.pmxt.dev/docs/v2-data-overview) timed out during this pass. No additional hour was verified. Preserve existing coverage as order-book/trade-message observations; a last-trade message or transaction reference alone is not a validated fill ledger. This audit does not add collected observations.
