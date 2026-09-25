# Tennis point research and CSV data

The collector in `tennis/point_data.py` uses the primary [Tennis Abstract Match Charting Project](https://github.com/JeffSackmann/tennis_MatchChartingProject), pinned to commit `1813a1309b7ed7ebf1c7e884b32bf675d00e4edf`. Source metadata shows six point files, split by men's/women's matches and era, totaling roughly 191 MB, plus match metadata. Actual row counts, date coverage and missingness are measured on the GitHub-hosted runner. Source point rows are never downloaded or read on the owner's machine.

**The source data license is CC BY-NC-SA 4.0: attribution, noncommercial use, and share-alike are required.** This produces a separate research-only bundle; it does not establish permission for a commercial betting system. Original source documentation, attribution, modification notes and license notice are included. The original Sackmann Grand Slam and sequential point repositories currently return 404; this collector uses the still-accessible primary MCP repository instead of silently substituting an unverified mirror.

## Cloud execution and outputs

```bash
# GitHub-hosted runner only, using existing pandas/numpy/pyarrow/requests dependencies.
python tennis/point_data.py
# Optional bounded subset for initial validation:
python tennis/point_data.py --groups m
```

Outputs are under `data/tennis/mcp_research_only`, ready for a separate release archive:

| Table | Contents |
|---|---|
| `mens_singles/` and `womens_singles/` | Distinct source match and point CSVs; no pooled rankings across the two groups |
| `{group}/matches.csv.gz` | Normalized player names, date, surface, tournament, round and best-of metadata where present |
| `{group}/{era}/source_points.csv.gz` | All original point fields preserved, sorted by match and numeric point sequence |
| `{group}/{era}/point_states.csv.gz` | Reconstructed pre-point scores/server, break-point and tiebreak context, separately named point/serve outcome labels |
| `match_early_deficit_labels.csv.gz` | Retrospective deficit after four completed first-set games, with conservative completion eligibility |
| `player_summary.csv.gz` | Full descriptive pre-2025 player comparisons and uncertainty intervals |
| `data_summary.json` | Aggregate coverage, quality, source limitations and small candidate screens |
| `schema.json`, `source_manifest.json` | Column types/null counts, temporal roles, source URLs, source commit, retrieval times, hashes and license |

CSV nulls are literal `\N`; identifiers should be imported as strings. The dataset separates men's/women's charted singles, which broadly cover ATP/WTA players, but a gender file alone does not certify ATP/WTA sanctioned event membership. Other professional singles circuits can be present. Doubles-like participant names and missing identities are excluded from descriptive player comparisons. Player identity is the source name within its competition group, not a newly verified stable player ID.

## Serve timing and pre-point information

The [producer dictionary](https://github.com/JeffSackmann/tennis_MatchChartingProject/blob/1813a1309b7ed7ebf1c7e884b32bf675d00e4edf/data_dictionary.txt) documents point order, server, pre-point games/sets, tiebreak flags, first/second serve outcomes, aces, double faults, point winner and rally count. Player 1 is the player who served first. `Gm#` numbers games across the entire match; its optional parenthesized point counter is validated when present. Our pre-point score counts accumulate only earlier point winners within each source game. Break point means the returner would win a standard advantage-scored game by winning the next point; tiebreak points are handled separately. Gaps in point order, unknown servers, missing prior winners, game transitions before a completed game, inconsistent games/sets, and changed servers within a standard game invalidate subsequent reconstructed score prefixes. Nonstandard scoring is conservatively excluded when it fails these checks. Boundary checks use preceding outcomes and never the current or a future point winner.

`pre_*` fields represent candidate pre-point context. `label_*` fields describe current/future outcomes. First-serve-in, second-serve-in, ace, double-fault, point winner and rally length are **not pre-point features**. The source shot strings and convenience columns are retained for audit, but must not be passed wholesale into a model. Conditional analysis after a first-serve fault is possible only with the appropriate observation boundary; these charts contain no verified live update times, bet prices, execution delays or fill data.

Consequently the data cannot identify an executable instant to enter a trade after a serve. That needs a timestamped live feed and historical bid/ask, liquidity, spread, fees and actual latency. No tennis model is trained and no trades are placed.

## Descriptive clutch and early-deficit comparisons

Player summaries use only matches dated before January 1, 2025. Because source calendar dates lack a timezone, December 31, 2024 is conservatively excluded from these summaries, and date-only records immediately adjacent to the fit/calibration/holdout boundaries remain unresolved. Later source matches are retained as holdout data but never used to rank players in this release.

For serving under pressure, the summary compares break points saved against the same player's non-break-point service win rate, excluding tiebreaks from that baseline. The break-save rate is shrunk toward the player's baseline with 20 pseudo-observations; this is a declared descriptive stabilizer, not a fitted claim of skill. Candidate screens require at least 30 break points faced, 200 service points, 100 non-break-point service points and 10 charted matches. Raw counts, raw rates and Wilson 95% intervals remain available. Tiebreak service and return rates are separate columns with their own counts and intervals.

An early deficit means trailing in games after **four completed games of the first set**. Player 1/first server and Player 2/first returner are summarized separately. Candidate screens need at least ten eligible matches in that serve-order group; both raw and Beta(1,1)-shrunk trailing rates are supplied. Ties at 2–2 are not deficits. Labels are retained separately from pre-point features.

Early-deficit comparisons conservatively require evidence that the last recorded point completed a standard game, set and best-of-three/five match. Retirement/walkover flags, incomplete point histories and terminal tiebreaks lacking verified tiebreak-target rules are excluded. This intentionally sacrifices coverage rather than treating a partial match as completed. It does not certify the full underlying video or the absence of every source error.

These are descriptions of a volunteer-selected charted sample. Opponent strength, surface, era, fatigue, match selection and repeated-player dependence can explain differences. Binomial intervals and shrinkage do not remove those effects, and rankings are not causal measurements of “clutch ability,” profitable trade recommendations, or a validated forecast of who will start behind.
