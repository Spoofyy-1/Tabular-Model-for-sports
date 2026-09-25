# NBA and NFL officiating context

`extras/officials_context.py` collects published bulk officiating assignments on GitHub-hosted Actions. The default requested range is 2015–2026; actual season and match coverage are measured remotely. Source requests are capped at 100 and total response bytes at 100 MB. No source datasets are downloaded or processed locally, and no model is trained.

```bash
python extras/officials_context.py
# Optional smaller hosted run:
python extras/officials_context.py --start-season 2024 --end-season 2025
```

Outputs under `data/extras/officials_context` are year-partitioned `nba/{season}/officials.csv.gz` and `nfl/{season}/officials.csv.gz`, source audit CSVs, `summary.json`, column schemas and source manifests. Nulls are `\N`; game and official identifiers remain strings. Actual game dates determine development through 2024 versus holdout from 2025. NBA ending-season years and NFL seasons are not used as substitutes for game dates.

NBA uses [SportsDataverse's NBA officials release](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_officials), compiled from ESPN game-summary officiating lists by the [producer code](https://github.com/sportsdataverse/hoopR-nba-data/blob/4a640c7f04f139c33198b8c22e0f2a5b48fc1d28/R/espn_nba_10_officials_creation.R). Its [producer license](https://github.com/sportsdataverse/hoopR-nba-data/blob/main/LICENSE.md) explicitly covers repository data under CC BY 4.0; the distribution repository's license is preserved too. Source columns include name, position and order, **not a stable official person ID**. Names are retained without creating a supposedly verified person crosswalk. Exact ESPN game IDs join published schedules; existing attendance and venue-capacity fields are repeated only to make the officiating records interpretable.

NFL uses the [nflverse officials release](https://github.com/nflverse/nflverse-data/releases/tag/officials), documented by [`load_officials`](https://nflreadr.nflverse.com/reference/load_officials.html), and published under the [nflverse-data CC BY 4.0 license](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). This adds full crews and roles beyond the head-referee field already present in schedules. The source's numeric `game_id` is its legacy game identifier: the collector maps it only against schedule `old_game_id`, retaining both IDs. When both exist, the separate source `game_key` must agree with schedule `gsis`; contradictions are quarantined. Unmatched games retain unknown canonical IDs and dates.

The NFL schedule lookup remains separately attributed to [Lee Sharpe and nflverse/nfldata contributors](https://github.com/nflverse/nfldata); no blanket third-party license is asserted for it. Source license texts, download URLs, response hashes, retrieval timestamps and release asset update times are retained. Release timestamps are not substituted for the time a referee assignment became known.

Only legacy game IDs actually present in the requested officiating panel enter the NFL schedule lookup. Exact duplicate lookup rows are collapsed; conflicting candidates for any requested legacy ID are all excluded from the join and preserved in `nfl/ambiguous_schedule_mapping_audit.csv.gz`. Affected official records retain unknown canonical game/date and an explicit ambiguity exclusion. Historical placeholder IDs outside the requested panel cannot block collection.

Both datasets are revised postgame snapshots. Every row marks historical publication and pregame assignment timing unverified, with automatic training joins disabled. Attendance and observed weather are retrospective; capacity and venue fields can be revised. Missing identities, conflicting duplicate game/official rows, unmatched schedules and missing source seasons are reported explicitly. No referee effect, causal relationship or profitable betting strategy is claimed.

Synthetic tests: `python -m unittest discover -s extras -p 'test_officials_context.py'`. They cover legacy/canonical NFL mapping, independent-key conflicts, NBA name-only identity, duplicate quarantine, date-based splits, unmatched games, and the local-execution guard.
