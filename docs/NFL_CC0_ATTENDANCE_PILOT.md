# Bounded NFL attendance pilot using Wikidata

Audit date: 2026-09-25. This document records the source contract and the first hosted attempt's aggregate result below. The collector is `nfl_attendance/collect.py`, invoked without arguments, and local validation uses fabricated in-memory tests. Research used primary documentation, rendered entity pages and local source code. No entity JSON, sports data archives or attendance rows were downloaded or saved locally. Collection runs only on a GitHub-hosted runner.

An initial pilot targets five Super Bowl event candidates in calendar years 2020–2024. This is a small championship-game sample, not NFL attendance coverage. At source-review time, primary pages exposed an attendance statement for three candidates. The first hosted attempt received no event entities, so this page inspection has not become a collected attendance dataset.

## First hosted attempt: no attendance collected

The [nfl-attendance-36188533446-1 release](https://github.com/kennynakao/Tabular-Model-for-sports/releases/tag/nfl-attendance-36188533446-1) reports `status=audit_only`, zero received source entities, zero exact schedule joins and zero attendance/game rows. The hosted process downloaded the pinned schedule, then received an API error instead of the first Wikidata entity and stopped without retry. Its aggregate report records three HTTP attempts and 8,654,196 decoded bytes, including a 476-byte Wikidata error response. Workflow completion means the diagnostic artifacts were published; it does not mean attendance collection succeeded.

The recorded exception is `RuntimeError: Entity API returned an error; collection stopped without retry`. The collector did not preserve the provider's error code or `Retry-After`, so the cause is unresolved. Do not infer a rate limit, invalid request or unavailable data from the generic exception alone. No additional requests or rerun were made for this documentation update.

The release's five `missing_property` classifications are **not evidence that five event entities lack P1110**. They were produced by parsing empty fallback objects after no entity was received. The first candidate encountered an API error; the others were not fetched after collection stopped. Treat all five attendance-availability states as unobserved in this release. The earlier rendered-page findings in the table below remain a separate source audit.

Before a later hosted attempt, make these diagnostic changes; they are recommendations only and are not implemented by this documentation update:

- Preserve a bounded, sanitized API `error.code`, HTTP status and validated `Retry-After` (seconds or HTTP-date), alongside the already recorded response hash and byte count. Avoid dumping the response body or free-text error details into public logs. Keep the existing stop-on-error behavior.
- Track transport and entity-validation status per candidate: `received`, `api_error`, `http_error`, `invalid_entity` or `not_fetched_after_stop`. Reserve `missing_property` for a successfully received, validated entity whose claims actually lack P1110. Preserve separate unknown-value, no-value, invalid and conflicting claim states.
- Fetch the five bounded event prerequisites first. Validate identity, date, participant crosswalk, chain and available attendance before downloading the 8.65 MB schedule archive. If no candidate can support a count/game join, publish an audit-only result with `schedule_fetch_status=not_attempted_no_eligible_event`. If eligible candidates exist, then fetch the pinned schedule and apply the existing exact joins. Do not expand the allowlist or automatically retry failed requests.

Add synthetic regressions for a provider error on the first entity, an unfetched candidate, a received entity without P1110, and the skipped schedule download when no eligible event remains. No collector mutation was made in this pass, avoiding an immediate workflow retrigger.

| Calendar year | Event | Verified entity identifier | Attendance statement in inspected page |
| --- | --- | --- | --- |
| 2020 | Super Bowl LIV | [Q20204363](https://www.wikidata.org/wiki/Q20204363) | Present |
| 2021 | Super Bowl LV | [Q24233824](https://www.wikidata.org/wiki/Q24233824) | Not exposed in inspected page |
| 2022 | Super Bowl LVI | [Q30114464](https://www.wikidata.org/wiki/Q30114464) | Not exposed in inspected page |
| 2023 | Super Bowl LVII | [Q54196526](https://www.wikidata.org/wiki/Q54196526) | Present |
| 2024 | Super Bowl LVIII | [Q54196525](https://www.wikidata.org/wiki/Q54196525) | Present |

Use these five identifiers as a fixed allowlist. Validate the backwards [P155/follows](https://www.wikidata.org/wiki/Property:P155) chain starting at LVIII with at most four hops and [P156/followed by](https://www.wikidata.org/wiki/Property:P156) as a reciprocal check. Missing or conflicting edges, repeated identifiers or nondecreasing years stop validation and produce an audit reason. Do not expand beyond the allowlist to compensate for missing attendance.

## Rights, transport and claim contract

Wikidata's [licensing policy](https://www.wikidata.org/wiki/Wikidata:Licensing) places its structured entity data under CC0. Attribute Wikidata and preserve statement provenance. This does not grant rights to reproduce linked gamebooks, photographs, Wikipedia prose or quoted reference text. Do not follow referenced PDFs, scrape Pro Football Reference, or copy reference quotations; existing [gamebook restrictions](WEATHER_CROWD_SOURCE_AUDIT.md) remain applicable.

The documented [individual-entity interface](https://www.wikidata.org/wiki/Wikidata:Data_access) supports fetching known identifiers. The implementation makes five fixed event requests with `en|mul` labels and uses reviewed participant/venue identity crosswalks without extra entity requests. Limits: 20 HTTP attempts including redirects, 20 MB combined decoded source bytes including the pinned schedule archive, 10 MB of Wikidata payload, 2 MB per entity response and a 300-second collection wall limit. An identifying User-Agent, explicit timeouts and trusted-host redirect checks apply. Rate limiting or API errors stop collection without retry. No search crawl, page history, media downloads or reference-URL retrieval is needed.

Whitelist direct event claims: attendance P1110, calendar date P585, start P580, venue P276, participants P1923, instance P31 and chain P155/P156. Preserve claim identifiers, ranks, snak types, quantity units/bounds, time precision/calendar model and relevant qualifiers. Record entity revision, retrieval UTC time, payload hash and reference URLs without copying quotation text. Exclude winner and score properties, including score qualifiers on participants.

[P1110](https://www.wikidata.org/wiki/Property:P1110) is a reported attendance quantity. Accept a canonical count only when nonnegative, integral and dimensionally appropriate; otherwise preserve the raw claim and exclusion reason. Missing, unknown and no-value claims stay distinct from zero. Retain conflicting nondeprecated quantities as candidates and leave the canonical value unresolved. Identical values may share one canonical count while retaining every statement's provenance. A source with no references remains explicitly unverified.

Do not equate attendance with people in seats, crowd noise or fan allegiance. Leave the counting method unverified unless structured evidence establishes it. Entity revision time is not the original publication time; all target-game attendance is retrospective and `verified_asof=false`.

## Exact schedule matching

Reuse the already published [NFL team archive](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/matchup-36179718171-1/csv-matchup-nfl-teams.tar.gz), avoiding the larger base archive:

- Archive bytes: `8653720`; SHA-256: `4ecdcf9533346d99c5c121ebd87085b41d041667adbac7074e08a714ea1eaeb8`.
- Selected member: `data/matchup/nfl_teams/schedule_context_audit.csv.gz`; SHA-256: `c91a2f24949085036e5fc2664ef0aa3172f70c6fff34d540152ea46b164c3cb8`.
- Verify both hashes on the runner, read only this member, bound decompression to 120 MB, preserve string IDs and the documented `\N` null sentinel. Retain the archive's upstream source/license notices separately from the CC0 claims.

The member supplies `game_id`, `team`, `opponent`, `game_date`, `season`, `is_home`, `source_game_type`, `source_stadium`, `source_stadium_id` and `source_game_date_time_known`. First restrict to `source_game_type == "SB"`. Require exactly two reciprocal team rows per game, one nominal home side, matching season/start/venue identities and a known original start time. Collapse the pair to one game before counting attendance observations.

Map participant entity IDs through an explicit reviewed QID-to-franchise-code crosswalk. Join the exact unordered pair, event calendar date and Super Bowl game type; require exactly one candidate. Validate home/away if [P3831 role qualifiers](https://www.wikidata.org/wiki/Property:P3831) identify [home Q24633211](https://www.wikidata.org/wiki/Q24633211) or [away Q24633216](https://www.wikidata.org/wiki/Q24633216). Missing roles remain flagged, not inferred from participant order or physical venue. Neutral-site designation is compatible with a nominal home team.

P585 with day precision denotes a calendar date, not midnight kickoff UTC. Compare its date components to the schedule's documented Eastern-calendar date derived from canonical UTC `game_date`; do not shift the Wikidata day as though it were a timestamp. Nonzero date uncertainty is unresolved. The source season must be the previous calendar year for these February championship events. Preserve the source start-time claim without reconstructing its refined clock: actual-start clocks can differ from the schedule's kickoff. Do not use exact clock equality as an identity requirement. The implementation checks venue using reviewed exact stadium-name aliases to Wikidata QIDs; provider stadium IDs are retained, but their equivalence to QIDs is **not** verified. An explicit mapped venue conflict excludes the join, while an unknown name or missing venue remains unresolved. No fuzzy matching, scores or winners may resolve ambiguities.

## Outputs and limits

Publish compressed CSV event claims, canonical attendance/game links and exclusions, plus schema, hashes, license notices and aggregate coverage. `cc0/` holds structured claims; `schedule_join/` retains separate upstream NFL attribution and is not wholly CC0. Include all five candidate IDs, missing/conflict counts, exact-join counts, role/venue verification flags and retrieval/publication uncertainty. Game links sort by game time then event ID; claim rows sort by event/property/statement ID. Publish only an explicit generated-file inventory. Status is `complete` only if all five events have eligible counts and joins without collection errors, `partial` for some eligible counts, and `audit_only` for none. Export no capacity ratio: current [P1083 capacity](https://www.wikidata.org/wiki/Property:P1083) does not establish the stadium's football configuration on the game date.

All five dates belong to pre-2025 development; 2024 retains its calibration label. No training, holdout tuning, causal estimates or betting conclusions follow from this sample. Synthetic tests should cover missing/conflicting attendance, fractional quantities, chain cycles, conflicting nominal roles, duplicate schedule pairs, ambiguous matches, date-only semantics, actual-versus-scheduled start differences and blocked future-year expansion.
