# Additional NFL context

`context/nfl_context.py` collects tracking summaries, depth-chart history and injury reports only
on GitHub-hosted Actions. Raw files live under `RUNNER_TEMP/nfl-context` and
organized outputs under `data/context/nfl`. The workstation receives code,
documentation and aggregate reports only. This update does not modify models.

| Source/table | Requested coverage | Added information | Historical use |
|---|---|---|---|
| NGS passing, receiving and rushing weekly | 2016–2026, where published | Throw timing, air yards, completion expectation, separation/cushion, stacked boxes, yards above expectation | Postgame measurements; earlier games only, after availability audit |
| NGS completed-season summaries | 2016–2026, where published | Source week-zero aggregates | Separate audit tables; never join as same-season pregame features |
| Historical weekly depth charts | 2001–2024 | Positions, formations and depth ordering | No verified publication time; excluded from model features |
| Timestamped depth charts | 2025–2026, where published | ESPN position slots, ranks and successive loaded timestamps | Source `dt` preserved; no automatic game assignment or independent timestamp verification |
| Additional injury reports | 2025–2026, where published | Injury descriptions, practice/game status, source modification time | Final report snapshots; modification time does not establish historical publication time |

NGS only reports players meeting its minimum-attempt thresholds. Missing rows
are not zero performance. Current 2026 files are partial. Source availability is
checked during each remote run; exact row counts appear in the release report.
The injury release inventory now contains both 2025 and 2026 files, superseding
earlier documentation that described the feed as unavailable after 2024.
The baseline already contains 2009–2024 injury reports, so this layer collects
only the additional seasons. Missing assets are reported explicitly; empty
published files and incompatible schemas fail validation. Coverage reports list
published weeks, missing modification times, status counts and unmatched games.
The new injury exporter omits per-row modification timestamps, documented in
[upstream issue 100](https://github.com/nflverse/nflverse-rosters/issues/100).
The normalized timestamp remains null, with `source_column_not_provided` status.
Release upload times are never substituted as report publication times.

All canonical tables use GSIS `player_id`, preserve the original player/team
identifiers, and normalize relocation/team aliases to the existing project's
codes. Missing/malformed GSIS records are quarantined, never matched by names.
Depth chart rows retain formation/position/slot/rank; multiple roles for the same
player are valid. Identical rows are deduplicated. NGS weekly identity conflicts
fail validation. Weekly tables join the schedule by season/type/week/team;
unmatched rows remain visible in aggregate quality reports.

Every table and every field in `DATA_DICTIONARY.json` explicitly records
`verified_asof: false`. `pregame_feature_enabled` is false in all rows. The source
loaded timestamp is useful evidence, but this collector does not pretend it is
an independently audited historical publication timestamp. Retrieval time and
HTTP modification time also do not establish past availability. NGS data may
be retrospectively revised; simply lagging a downloaded value does not resolve
that risk. The [depth-chart publisher](https://github.com/nflverse/nflverse-rosters/blob/main/exec/update-depth-charts.R)
also reapplies its current ESPN-to-GSIS crosswalk to old snapshots. An old `dt`
does not prove that the current player-ID mapping existed at that time.

The existing experiment is unchanged: development game dates before January 1,
2025 UTC, with 2024 calibration and 2025+ holdout. Where schedule dates are known,
the collector labels this split by actual game date. NFL 2024-season playoff
games played in January 2025 belong to the holdout. No split or pregame
eligibility is inferred for season summaries or unjoined depth snapshots.

Remote outputs contain sorted compressed Parquet tables, separate excluded-ID
tables where needed, the upstream license text, `manifest.json` with source URLs,
retrieval dates and SHA256 hashes, a field dictionary with null counts, and
`context_summary.json` with counts by season, schedule matches and source gaps.
Downloads are streamed with a 30 MiB/file, 256 MiB/download and 500 MiB total raw/output
budget. No play-by-play download is needed.

Sources and source terms:

- [nflreadr NGS loader](https://nflreadr.nflverse.com/reference/load_nextgen_stats.html)
  and [NGS dictionary](https://nflreadr.nflverse.com/articles/dictionary_nextgen_stats.html).
- [nflreadr depth-chart loader](https://nflreadr.nflverse.com/reference/load_depth_charts.html)
  and [current depth-chart dictionary](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html).
- [nflverse availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html)
  documents the depth-chart source transition and update cadence.
- [Injury loader and schema](https://nflreadr.nflverse.com/reference/load_injuries.html),
  [injury dictionary](https://nflreadr.nflverse.com/articles/dictionary_injuries.html)
  and [current injury release inventory](https://api.github.com/repos/nflverse/nflverse-data/releases/tags/injuries).
- [nflverse-data CC-BY-4.0 license](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md)
  and [nflverse terms notice](https://nflverse.nflverse.com/#terms-of-use).
  NFL/source-provider ownership and terms continue to apply; the repository
  license does not assert rights beyond its licensors' authority. Attribution:
  NFL Next Gen Stats, NFL Data Exchange, ESPN, nflverse contributors and Lee
  Sharpe's schedule collection. Data are supplied without warranty.

PFR advanced metrics, combine and draft tables are documented candidates for a
later update after specific third-party terms review. FTN participation is a
different, season-end data product with CC-BY-SA-4.0 obligations and is not
included here. Injury `date_modified`, when supplied, is retained unchanged and parsed into
`source_snapshot_at_utc`; otherwise both are null with the missing source column
explicitly flagged. Even `modified_before_kickoff=true` does not establish
historical publication time. No report gaps are imputed as healthy status.
