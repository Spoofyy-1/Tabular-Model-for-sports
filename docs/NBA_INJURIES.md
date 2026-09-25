# Timestamped NBA injury reports

`enrichment/nba_injuries.py` retrieves official NBA PDF injury reports on GitHub-hosted runners and publishes factual parsed **CSV.gz** tables, schemas, coverage, quality, and provenance. Raw PDFs exist only transiently under `RUNNER_TEMP` and are removed after parsing. No PDFs or real sports data should be read or downloaded on the owner's machine.

## Source and scope

The [NBA's reporting guidance](https://official.nba.com/nba-injury-report-2025-26-season/) describes team submissions and continual updates. The [nbainjuries producer documentation](https://github.com/mxufc29/nbainjuries) describes static report coverage from the 2021–22 season and known gaps. Older NBA season landing pages currently redirect to the current season, so the collector requests timestamped report files directly and records failures.

The default range is October 18, 2021 through December 31, 2024. Candidate dates come from the project's frozen ESPN game table; one **17:30 Eastern Time** snapshot is requested for each game date. Source gaps are counted explicitly. This is a bounded historical collection, not every intraday update. `--end` can be extended for separate holdout collection, subject to `--max-reports` (default 1000; maximum 2000).

```bash
# Hosted GitHub Actions runner only, with Java 17 and standard project dependencies:
pip install -r enrichment/requirements-nba-injuries.txt
python enrichment/nba_injuries.py --start 2021-10-18 --end 2024-12-31 --max-reports 1000
```

The parser is pinned to commit `57603738fb28185e3c1bf4d71d75af0f85514ac1`. Its dependencies include `tabula-py`, `JPype1`, `PyPDF2`, requests and pandas. Java parsing is sequential to avoid shared-JVM concurrency issues; network retries are bounded. No model or baseline workflow is changed.

## Timing and interpretation

Legacy filenames encode only the hour, although the report header represents the half-hour update. Since December 22, 2025, filenames encode minutes. The collector verifies the PDF's first-page header against the requested date and **17:30** before accepting parsed rows, converts Eastern Time with DST to UTC, and records the original source URL, download time, byte hash and parser version.

A verified report header is **not independent evidence of immutable historical web availability**; this distinction is explicit in each row. A report may contain today's and tomorrow's games. Same-day early games may already have started at 17:30; those rows are retained with `header_precedes_game_start=false` and cannot supply a pregame feature. Any later modeling needs a stricter bet-specific cutoff, an audited player mapping, and latest-available-snapshot selection.

## Tables

- `injury_report_entries.csv.gz`: accepted factual player status and team-not-submitted entries, source game/team/player/status/reason fields, UTC/ET report timestamps and exact game mapping where available.
- `quarantined_rows.csv.gz`: parsed records with invalid/missing game, matchup, team or participation-status fields; no statuses or players are invented.
- `report_inventory.csv.gz`: one requested snapshot per row, source URL/hash/retrieval time, header verification, parsed counts, missing-report and error statuses.
- `schema.json`: column types and CSV null conventions. IDs must be read as strings; `\N` represents null values; empty strings remain distinct.
- `enrichment_summary.json`, `source_manifest.json`: aggregate coverage, split counts, mapping/duplicate/parse quality, provenance, licensing and limitations.

Games match the project's frozen ESPN schedule by exact Eastern calendar date and home/away team codes. Ambiguous keys stay unmapped. Player names remain unlinked and `espn_player_id` stays null; there is no fuzzy identity matching. Game UTC starts determine the existing pre-2024 fit / calendar-2024 calibration / 2025+ holdout split. Unmatched game times remain unresolved.

`NOT YET SUBMITTED` is a separate team entry, not a healthy-player signal. Missing players, missing reports, quarantined records and incomplete intraday coverage must not be interpreted as availability. The parser can change line grouping, so aggregate error and duplicate audits are required before training.

## Attribution and rights

Injury reports are published by the NBA. The third-party parser's MIT license is copied with outputs, but it does not grant rights to NBA content; [NBA terms](https://www.nba.com/termsofuse) remain separate. This collector publishes structured factual entries and provenance, not the original PDF documents. No claim of a betting edge follows from collecting these reports.
