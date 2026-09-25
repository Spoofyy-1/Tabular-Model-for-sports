# Verified tennis identity diagnostic

The hosted [diagnostic release](https://github.com/kennynakao/Tabular-Model-for-sports/releases/tag/tennis-alignment-diagnostic-36192040887-1) completed on 2026-09-25 with no errors. Its [aggregate JSON report](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/tennis-alignment-diagnostic-36192040887-1/tennis_alignment_diagnostic_summary.json) diagnoses the earlier zero-join weather pilot without accepting any new match or weather rows. Local inspection used this aggregate report and release metadata only.

## What the frozen inputs establish

Of two fixed event candidates, one still lacks the required court claim. The other passes the archived entity prerequisites but has **zero exact candidate-day rows in both the source-export and normalized men's match metadata**. The diagnostic establishes a gap in this pinned snapshot's exact-day coverage; it does not establish that the upstream publisher has no such match, that the match never happened, or which external date claim is authoritative.

| Aggregate check | Verified result |
| --- | ---: |
| Normalized men's metadata rows / distinct IDs | 7,564 / 7,564 |
| Source-export metadata rows / distinct IDs | 7,566 / 7,565 |
| Normalized rows with valid calendar dates | 7,564 |
| Rows with a supported Wimbledon label, in each representation | 492 |
| Rows on the prerequisite-valid candidate's exact day, in each representation | 0 |
| Exact participant-pair rows without the day/tournament/round restriction | 7 |
| Additional eligible aliases from combining English and default-language lists | 0 |
| Shared IDs whose literal identity text and canonical dates agree | 7,564 |
| Shared IDs with identity conflicts | 0 |
| Accepted match joins / weather rows | 0 / 0 |

All 492 supported tournament rows fail the exact-day stage. The seven participant-pair observations elsewhere cannot substitute for the missing date. Both alias policies classify the candidate as zero matches; no omitted alias or intersecting participant-name set explains this result. A general code behavior that can omit default-language aliases remains documented, but it did not affect these frozen candidates.

The raw export has one conflicting ID represented by two rows, including one unparsable source date. The producer's counters report two conflicting-key rows excluded. Every retained normalized ID has the same literal participant/tournament/round fields and canonical date in the source export. No raw exact-day candidate was lost through normalization in this diagnostic. Source-export strings already reflect the producer's original import-time null conversion; original pre-import empty strings cannot be reconstructed.

## Publication and limits

The runner verified both pinned archives, selected member hashes, schema/null conventions, row counts and license notices. It used **45,966,624 decoded bytes over eight HTTP attempts**, below the 60 MB / 12-attempt / 300-second limits. No live Wikidata, weather, Overpass, point-data or odds refresh occurred. Thirty synthetic tests and independent identity/transport reviews passed before the hosted run; the hosted run also passed.

The CSV archive is 7,519 bytes and contains aggregate tables, schemas, provenance and notices. The release remains a prerelease diagnostic. CC0-only source-contract counts are separate from MCP-derived **CC BY-NC-SA 4.0** counters. These are not additional match observations and must not be concatenated into the model tables. No source rows, player names, literal aliases, match IDs, source dates, outcomes or debugging row samples were published by this diagnostic.

The earlier weather pilots remain at zero accepted joins. A [follow-up date/source audit](TENNIS_DATE_SOURCE_AUDIT.md) checks parser behavior, official event-date evidence and snapshot timing; it finds no systematic year/timezone bug and preserves the remaining frozen-date uncertainty. Before another alignment collection, verify actual date semantics and source coverage. Keep the court, edition, participant, round, date and uniqueness checks. Do not repeatedly fetch these same archives, expand matching to another date, or use scores/winners to force a join. Match start/end times and roof operation also remain unknown; no playing-hour weather exposure, causal effect, trading edge or training result has been established.
