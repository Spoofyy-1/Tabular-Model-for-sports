# Tennis date and source coverage audit

Audit date: 2026-09-25. This is a read-only code, source-documentation and aggregate-report audit. No source CSV, dataset archive, match rows or weather rows were downloaded or stored locally. Eight fabricated in-memory parser cases were tested; no collector or matching rule was changed.

## What the published diagnostic establishes

The [completed frozen-input diagnostic](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/tennis-alignment-diagnostic-36192040887-1/tennis_alignment_diagnostic_summary.json) contains 7,564 normalized men's match records and 7,566 source-export records. For the one candidate that passes the frozen entity prerequisites, neither table contains a parseable calendar date equal to the candidate's recorded day. All 492 supported Wimbledon records pass calendar-date parsing. Seven exact participant-pair matches occur elsewhere in the metadata, but none satisfies the required day. Both name policies produce zero candidates, with no omitted eligible aliases or participant-name collisions.

All 7,564 shared IDs preserve the literal player, tournament and round fields and equivalent calendar dates. The producer reports two conflicting-key rows excluded under one source ID. These exclusions do not explain a lost exact-day candidate. There is one invalid source date globally, outside the supported Wimbledon subset; do not describe every source date as valid. The second fixed event candidate remains excluded for a missing or unknown required court claim. Accepted match joins and accepted weather rows remain zero.

These findings establish missing exact-day coverage in the pinned metadata under the existing interpretation. They do not establish absence from all editions of the publisher's database, prove that every source date is semantically correct, or justify selecting a different event date or participant spelling.

## Producer and candidate date handling

`tennis/point_data.py` imports metadata with `dtype="string"`, retains the resolved `Date`/`matchdate` field, and calls `pd.to_datetime(..., format="mixed", errors="coerce")`. It does not reinterpret the year as a season, apply an Excel serial-date origin, localize to UTC or shift a timezone. The diagnostic replays this parser family and then rejects offset-aware and non-midnight dates for the exact calendar-day comparison.

The [pandas date-conversion documentation](https://pandas.pydata.org/docs/reference/api/pandas.to_datetime.html) distinguishes string parsing from numeric epoch interpretation; `utc=False` preserves naive inputs and `errors="coerce"` makes invalid dates missing. Its warning about inferred mixed formats still matters: successful parsing alone does not prove the publisher's intended event date.

Eight synthetic compact `YYYYMMDD` cases passed through the actual producer `metadata()` function using pandas 2.3.3. They covered leap dates, year boundaries and the independently corroborated final day. Every result equaled the manually constructed calendar date and remained timezone-naive. This checks the exercised implementation paths, not every record in the real source.

`tennis_weather/collect.py` reads the Wikidata day-precision Gregorian `P585` claim as a calendar date, without converting the serialized midnight suffix into a kickoff timestamp. It checks the candidate year and tournament edition. **The published aggregate diagnostic does not expose the frozen candidate's exact day.** The current public entity and primary event reports can corroborate an expected day, but the aggregate alone cannot prove the archived claim equals it.

The current `unique_date()` validator also does not inspect the time value's `before`/`after` uncertainty members. Their frozen values are not exposed by the aggregate report. This is a validation limitation to resolve before any future accepted join, not evidence that uncertainty caused the present zero-match result.

## Primary publisher corroboration and remaining limits

The current [2024 men's final entity Q128304298](https://www.wikidata.org/wiki/Q128304298) records July 14, 2024. The inspected page identifies revision 2392472881 and an August 15, 2025 modification date; its relevant event statements display zero references. This is a current publisher page, not a direct reading of the frozen claim archive.

The [ITF's official 2024 Wimbledon men's final notes](https://www.itftennis.com/media/12777/2024-wimbledon-mens-singles-final-match-notes.pdf) identify the final and its participants on Sunday, July 14. The [ATP's dated final report](https://www.atptour.com/en/news/alcaraz-djokovic-wimbledon-2024-final) independently corroborates the same day and Centre Court. Direct ATP page retrieval returned HTTP 403 during this audit; the search index exposed the primary article's dated text, and the ITF document was directly readable through web research. No access restriction was bypassed. These sources support the calendar day, not an exact match start/end interval or roof operation.

The MCP source is pinned to `1813a1309b7ed7ebf1c7e884b32bf675d00e4edf`. [Git commit metadata](https://api.github.com/repos/JeffSackmann/tennis_MatchChartingProject/git/commits/1813a1309b7ed7ebf1c7e884b32bf675d00e4edf) dates that repository snapshot to September 18, 2026 at 08:35:54 UTC. It therefore is not a repository snapshot from before the 2024 final. A recent repository commit does not establish completeness or the last update of every individual match.

The [pinned MCP README](https://github.com/JeffSackmann/tennis_MatchChartingProject/blob/1813a1309b7ed7ebf1c7e884b32bf675d00e4edf/README.md) describes volunteer-contributed match records and match metadata including dates. The publisher's [contribution guide](https://www.tennisabstract.com/blog/2015/09/23/the-match-charting-project-quick-start-guide/) describes selecting and charting individual matches. Neither is a guarantee of complete tournament-day coverage. The inspected [data dictionary](https://github.com/JeffSackmann/tennis_MatchChartingProject/blob/1813a1309b7ed7ebf1c7e884b32bf675d00e4edf/data_dictionary.txt) primarily documents point fields; it does not establish match wall-clock times or a timezone for metadata dates.

## Optional bounded diagnostic, not a collection request

If another diagnostic is needed, it can use the original pinned metadata directly instead of transferring the 45,950,657-byte point archive again. [Git tree metadata](https://api.github.com/repos/JeffSackmann/tennis_MatchChartingProject/git/trees/fb9985cc2b38c7ecffdc6d0fbdab22af4e5457ea) lists `charting-m-matches.csv` as 1,141,817 bytes with Git blob SHA-1 `7bf0d44611caf3c2becfb77d47db70da57fab4cd`. The fixed source URL is `https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/1813a1309b7ed7ebf1c7e884b32bf675d00e4edf/charting-m-matches.csv`.

A separately reviewed hosted-only job could validate size and Git blob identity, compute SHA-256 provenance, and emit only year-coverage counts and strict `%Y%m%d` versus existing mixed-parser disagreement counts. Git blob verification hashes `blob <byte length>\0` followed by the exact source bytes; ordinary SHA-1 over the bytes is not equivalent. Unknown date formats remain explicit rather than being guessed.

The 8,434-byte frozen entity archive from `tennis-weather-36187582212-1` could supply a count or boolean stating whether its stored candidate day equals the primary-source day, without publishing source rows. Its existing archive SHA-256 is `c9e27e95a667b9f2fe4b6bc12b0af6e091be7770dc30707cda96ded1d42fdfea`. Including its small manifest, the planned transfer can remain under a strict 2,000,000-byte aggregate input cap. Verify all existing pins and schemas, bound requests including redirects, and fail explicitly on mismatch or access denial.

The output would remain an aggregate research diagnostic with zero accepted joins. Preserve MCP attribution and CC BY-NC-SA restrictions; do not copy primary article bodies into the dataset or treat their public accessibility as a redistribution license. This audit did not collect datasets, train models, refresh source data or relax date/name checks.
