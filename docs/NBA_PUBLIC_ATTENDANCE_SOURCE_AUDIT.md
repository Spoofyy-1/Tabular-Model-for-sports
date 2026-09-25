# Public NBA attendance: source audit

Audit date: 2026-09-25. Three new source candidates were identified after checking the existing collector's URL catalog. This document contains minimal source-review notes, not a seeded attendance dataset. No article bodies, images or datasets were downloaded to the workstation. Collector code was not changed.

These are NBA-hosted, AP-attributed game reports. Their reporting is more specific than a general celebrity-fan article, but the NBA host should not be mistaken for independent NBA confirmation of an AP report. The numbered URLs supply exact official game identifiers, avoiding ambiguous date-and-team searches.

[Game 0042200234](https://www.nba.com/game/gsw-vs-lal-0042200234): Warriors–Lakers, Los Angeles, 2023-05-08. Jack Nicholson and Chris Pratt attended; recap dated 2023-05-09, 11:21 AM. Evidence: TIP-INS.

[Game 0042200236](https://www.nba.com/game/gsw-vs-lal-0042200236): Warriors–Lakers, Los Angeles, 2023-05-12. Kim Kardashian attended; recap dated 2023-05-13, 2:16 AM. Evidence: TIP-INS.

[Game 0042200313](https://www.nba.com/game/den-vs-lal-0042200313): Nuggets–Lakers, Los Angeles, 2023-05-20. Eddie Murphy attended; recap dated 2023-05-21, 11:20 AM. Evidence: TIP-INS.

The evidence above was visible in search-indexed versions of those primary-host pages. Direct page extraction during this audit returned page shells rather than the recap text. Therefore these are **documented source leads pending hosted retrieval**, not a claim that the collector can currently fetch or parse the evidence. A search index excerpt is a discovery aid, not a substitute ingestion source or a verified first-publication timestamp.

The displayed recap clocks did not establish a timezone in the inspected material, nor whether the date denotes initial publication or a later update. Preserve a date-only publication value unless a hosted fetch exposes a matching article's timezone-qualified `datePublished`. Do not invent midnight, UTC or Pacific offsets. All three are retrospective reports; they do not establish that attendance was knowable before tip-off. Keep `eligible_for_pregame_feature=false` and `historical_publication_time_verified=false` unless separate evidence changes that assessment.

## Concrete collector gap and matching contract

The current `nba_watch_blocks` implementation accepts only VIP/CELEBRITY WATCH/SIGHTINGS sections and treats TIP-INS as a section boundary. These candidates therefore need a deliberately narrow alternative extraction rule, not just additional URLs. A future rule should operate only inside a recap positively scoped to its official game ID, accept explicit attendance predicates in a sentence, and resolve people from that sentence rather than every proper name in the surrounding paragraph. TIP-INS often mixes roster, injury and spectator information; treating the whole section as an attendance list would introduce false positives.

Before producing any rows, the hosted collector must obtain the actual recap or matching structured article, confirm the game ID and historical event date, and record the article/evidence hashes. Do not widen extraction to navigation, recommended stories, unrelated image captions or all page text merely to make the row count positive. A shell response should be reported as unavailable evidence, not as no celebrity attendance.

Use the official game ID embedded in each source URL as the first matching key. On the runner, cross-check opponent pair, Lakers home status, season and source-local event date against the existing NBA schedule/crosswalk before assigning a canonical ESPN ID. Publication date must never replace game date. Calendar dates around UTC midnight require the schedule's actual start timestamp; retain source-local date separately. An unresolved official-to-ESPN match remains unjoined, rather than being guessed from a nearby date.

The next useful experiment is a hosted retrieval check on this three-URL allowlist, followed by synthetic parser tests and an aggregate extraction report. Do not create local manual attendance rows or hard-code the names above into parser fixtures. Use invented names for tests. No absent mention means absence, and repeated press coverage is not independent confirmation. No open content license was established; preserve source attribution and rights uncertainty, and do not redistribute article bodies, images or complete attendance passages. Finding these sources does not establish commercial reuse permission or a predictive effect.
