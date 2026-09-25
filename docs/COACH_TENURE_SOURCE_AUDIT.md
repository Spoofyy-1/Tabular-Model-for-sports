# Coach hiring source audit

Research date: 2026-09-25. Repository: [kennynakao/Tabular-Model-for-sports](https://github.com/kennynakao/Tabular-Model-for-sports).

This document records source choices and parsing decisions for future hosted collection. It is not a downloaded coaching dataset or a completed tenure table. No source CSVs, article corpora, PDFs or real dataset rows were created locally. Eight candidates have clear dated team announcements; a ninth illustrates conflicting date semantics. The inspection used primary publisher pages through web research, not a hosted ingestion run.

## Candidate announcement sources

1. **Brooklyn Nets / Jordi Fernández.** [Team announcement](https://www.nba.com/nets/news/brooklyn-nets-name-jordi-fernandez-head-coach): April 22, 2024; publication 9:01 AM EDT. Completed appointment announcement; separate duty-start time unstated.

2. **Toronto Raptors / Darko Rajaković.** [Team announcement](https://www.nba.com/raptors/news/raptors-name-darko-rajakovic-as-head-coach): June 13, 2023; publication 12:17 PM EDT. Appointment announcement; separate effective timestamp unstated.

3. **Philadelphia 76ers / Nick Nurse.** [Team biography accompanying the appointment](https://www.nba.com/sixers/news/nick-nurse-bio): June 1, 2023, 12:45 PM EDT; named head coach. Distinct from earlier reporting.

   Collection decision for these three NBA pages: record an appointment-announcement event, not a complete employment interval. The first two contain the appointment assertion in the lead; the third is a same-day team announcement biography and should retain that source kind. Preserve accented names and source spelling. Never derive a predecessor's departure date from a successor's announcement.

4. **Seattle Seahawks / Mike Macdonald.** The [team hiring article](https://www.seahawks.com/news/mike-macdonald-named-head-coach-of-the-seattle-seahawks) is dated January 31, 2024 at 4:30 PM, without a displayed timezone. A [separate team introduction notice](https://www.seahawks.com/news/mike-macdonald-to-be-introduced-as-head-coach-of-the-seattle-seahawks-on-thursday-february-1-2024-at-11-a-m-pst) explicitly distinguishes the January 31 hiring announcement from the February 1 introductory press conference. Treat these as different event types. Do not assign the press-conference time to employment commencement.

5. **Atlanta Falcons / Raheem Morris.** The [team hiring announcement](https://www.atlantafalcons.com/news/raheem-morris-hired-head-coach-announcement-arthur-blank) is dated January 25, 2024 at 8:48 PM, with no visible timezone. The lead describes a completed hire announced that day. The same article discusses an earlier interim appointment in 2020: a parser must isolate the current announcement and avoid conflating the two spells. The official URL without the spurious encoded suffix that appeared in one search result was separately opened and verified.

6. **Los Angeles Chargers / Jim Harbaugh.** The [team communications release](https://www.chargers.com/news/chargers-name-jim-harbaugh-michigan-head-coach) is dated January 24, 2024 at 5:11 PM, without a visible timezone. Its lead explicitly describes agreement to terms that day. Emit `agreement_to_terms_announced`; do not silently upgrade it to a signed-contract effective timestamp or first coaching day.

7. **Carolina Panthers / Dave Canales.** The [team announcement](https://www.panthers.com/news/panthers-agree-to-terms-with-dave-canales-to-become-head-coach) is dated January 25, 2024 at 8:30 PM, without a visible timezone, and describes agreement to terms on Thursday. Preserve the agreement event distinction used for Harbaugh. A subsequent introductory event would be additional evidence, not a replacement date.

8. **Houston Texans / DeMeco Ryans.** The [team public-relations release](https://www.houstontexans.com/news/houston-texans-hire-demeco-ryans-as-head-coach) announces a completed hire on January 31, 2023, with a displayed publication time of 6:00 PM and no timezone. The [current team coaching biography](https://www.houstontexans.com/team/coaches-roster/demeco-ryans) explicitly identifies January 31, 2023 as the appointment date. This is useful corroboration of the historical event, but that mutable biography's later outcomes and current tenure description are not historical pregame features.

## Conflict candidate: do not resolve automatically

**New England Patriots / Jerod Mayo.** The [January 12, 2024 team notice](https://www.patriots.com/news/patriots-to-host-an-introductory-press-conference-to-announce-the-promotion-of-jerod-mayo-as-the-15th-head-coach-in-franchise-history) announces a forthcoming January 17 introduction. The [official historical timeline](https://www.patriots.com/press-room/history) separately records the January 12 announcement and January 17 introduction. Yet [official preseason game notes](https://www.patriots.com/news/game-notes-jerod-mayo-makes-his-head-coaching-debut) describe January 17 as the hire date. The [formal introduction release](https://www.patriots.com/news/patriots-formally-introduce-jerod-mayo-as-the-15th-head-coach-in-team-history) currently displays January 18 despite referring to the introduction as occurring that day; other team coverage places the ceremony on January 17.

Store distinct announcement and introduction assertions and a `conflicting_event_date_semantics` audit flag. Leave a single effective tenure start unresolved. This also demonstrates why a parser cannot reliably turn every occurrence of “today” into the currently displayed publication date without corroboration. Do not treat date-modified metadata as original publication.

## League trackers as discovery indexes

The [NBA 2023 coaching tracker](https://www.nba.com/news/2023-coaching-tracker) covers six teams and links appointment and departure reports. Its June 14 update is not every coach's hire date.

The [NFL 2024 coaching/GM tracker](https://www.nfl.com/_amp/nfl-coaching-gm-tracker-latest-news-interviews-developments-in-2024-hiring-cycle) combines official announcements and sourced reports. Keep section, role, and evidence type distinct.

The [NFL 2023 coaching/GM tracker](https://www.nfl.com/news/nfl-coaching-gm-tracker-latest-news-interviews-developments-in-2023-hiring-cycle) is another multi-team discovery index. Do not promote interview or candidate-list entries into appointments.

These are revised pages. Their current contents may help find an original announcement, but their latest update time does not establish when each claim first became public. Prefer the linked team release when it is accessible. General managers, coordinators, assistants, interim coaches and permanent head coaches need separate role codes.

## Actionable hosted collector design

Start with the eight main team pages and the Seattle introduction / Texans biography corroborations: ten documents, at most 20 requests and 20 MB total, with a 4 MB page limit. All actual retrieval and output creation must run on genuine GitHub-hosted Actions. If necessary, allow at most two ordinary HTTPS redirects on the original hostname, count every request against the cap, and record the redirect chain. Access challenges and 403 responses stay explicit failures. Prior staff work encountered NBA-host access failures; do not switch to undocumented alternate NBA hosts to circumvent them.

For NFL club pages, inspect `article`, `.nfl-c-body-part`, and `Article`/`NewsArticle` JSON-LD, matching the canonical page URL. Capture the lead's named person, team and appointment verb, then stop before related content. Separate article publication metadata, modification metadata and explicit event dates. Validate weekday references against an independently supported event date rather than treating them as complete dates.

For NBA team pages, inspect a scoped article body or `__NEXT_DATA__` story/content field tied to the exact article slug or canonical URL. Do not scan unrelated current news, recommended articles or current team navigation. Distinguish publication dates containing EDT/EST offsets from timezone-free display times. A parser should emit no candidate when name/team/role evidence conflicts or when only a headline is accessible.

Suggested schema fields are `sport`, `team_name_as_reported`, `coach_name_as_reported`, `canonical_team_id`, `canonical_coach_id`, `role`, `is_interim`, `event_type`, `event_date`, `event_date_precision`, `event_date_basis`, `effective_start_date`, `effective_end_date`, `first_game_coached_id`, `source_url`, `source_kind`, `source_published_at_utc`, `source_published_date`, `source_modified_at_utc`, `retrieved_at_utc`, `source_sha256`, `evidence_sha256`, `date_conflict_status`, `historical_availability_verified`, `automatic_training_join_allowed`, and `rights_status`.

For this initial collection, effective start/end dates and first-game assignments remain null unless separately supported. Historical availability remains unverified, and automatic training joins remain disabled. An appointment-announcement date is useful factual context, but does not by itself prove when a coach first directed a game, whether a predecessor remained for an interim period, or when the appointment ended. Even a complete tenure does not establish a coaching-style effect or cause of wins.

No open article-content license was established. Publish only a small set of normalized factual event annotations, citations and hashes; do not redistribute article bodies, interview transcripts, images, or bulk biographies. The project's code license does not grant rights to source content. Commercial reuse and bulk-content permissions remain unresolved. Preserve per-source HTTP status, extraction counts and exclusion reasons in an aggregate report so access/layout gaps can be audited without opening datasets locally.
