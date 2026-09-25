# Unusual tennis context: bounded source audit

Audit date: 2026-09-25. This is a source and implementation-priority report, not a newly collected dataset. No new collector was authorized during this audit. Actual source extraction must run on a GitHub-hosted runner, with CSV outputs, source hashes, licenses and uncertainty preserved. New model training remains paused.

The most practical additions are venue elevation and a clearly labeled audit of existing point-note mentions. Tournament ball, roof and clock policies are collectable as small dated source catalogs. A complete historical record of roof position, medical timeouts, chair assignments or elapsed serve-clock time was not established.

| Rank | Candidate | What can actually be collected | Readiness and historical limit |
|---|---|---|---|
| 1 | Venue altitude from SRTM | Elevation at already verified venue coordinates through a small JSON batch API | Strong technical route; preserve historical venue identity and actual raster provenance |
| 2 | Point-note interruption mentions | Positive references to medical treatment, delays, warnings or challenges from already collected MCP `Notes` | Cheap research-only audit; missing mentions are not proof that an interruption did not occur |
| 3 | Tournament ball specification and court product metadata | Dated official supplier announcements and ITF approval/classification documents | Small factual source catalog; no established open, complete tournament-year CSV |
| 4 | Roof capability and scheduling/clock policy | Dated official tournament/federation announcements describing infrastructure or event rules | Useful static policy history; does not prove roof state or actual local start at any point |
| 5 | Historical serve-environment proxy | Annual Tennis Abstract ace-adjusted tournament index, or a new strictly lagged research statistic from licensed raw matches | Existing annual HTML lead, but publication/reuse rights and outcome leakage require resolution before ingestion |

## 1. Venue altitude: bounded, documented API

[Open Topo Data's API documentation](https://www.opentopodata.org/api/) specifies a point/batch query. The candidate endpoint is `https://api.opentopodata.org/v1/srtm30m`, with `locations` and optional `interpolation` parameters. Documented response keys are `status`, `results[].elevation`, `results[].location.lat`, `results[].location.lng`, and `results[].dataset`. Its [public API limits](https://www.opentopodata.org/#public-api) are 100 locations per request, one call per second, and 1,000 calls per day. Hundreds of distinct tournament venues would require only a handful of calls and far less than 1 MB of results.

The [provider's SRTM documentation](https://www.opentopodata.org/datasets/srtm/) identifies the public service as SRTM version 3, documents approximately 30 m sampling, and returns null outside coverage. Preserve the dataset identifier, coordinate source, interpolation choice, source query and retrieval date. SRTM terrain measurement predates modern matches, but present-day venue names/coordinates must not be attached to an earlier tournament that used a different facility. Terrain elevation is also not a direct measurement of the playing surface of a roof or elevated stadium.

The [USGS SRTM catalog](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm) identifies original mission data and access routes. [USGS copyright guidance](https://pubs.usgs.gov/documentation/faq) permits use of USGS-authored/produced public-domain data with attribution, while noting third-party exceptions. The Open Topo Data **software** is MIT licensed; that is not the elevation dataset license. Confirm and record the chosen raster lineage rather than applying the software license to all hosted products. Avoid silently replacing SRTM with a differently licensed elevation product when coverage is missing.

Proposed derived CSV fields: `venue_id`, `latitude`, `longitude`, `elevation_m`, `elevation_dataset`, `interpolation`, `coordinate_source_url`, `coordinate_valid_from`, `coordinate_valid_to`, `retrieved_at_utc`, `source_url`, `source_sha256`, `historical_venue_join_verified`. These are proposed export fields, not an asserted upstream CSV header.

## 2. Existing MCP Notes: interruption candidates, not event ground truth

The primary [MCP data dictionary](https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/data_dictionary.txt) documents `Notes` as a contributor's free-text observations, sometimes describing challenges. The existing point export also supplies `match_id`, `Pt`, `Set1`, `Set2`, `Gm1`, `Gm2`, `Gm#`, `Svr`, and `PtWinner`; these can locate a note in match/point sequence. No documented structured medical-timeout, umpire-identity, time-violation, clock-duration or roof-position columns were established.

The [primary repository README](https://github.com/JeffSackmann/tennis_MatchChartingProject#readme) applies **CC BY-NC-SA 4.0**. Keep any derivative in the existing noncommercial research bundle with attribution and share-alike terms. The cloud release already contains the raw source, so a bounded note audit needs no new external data transfer.

A first pass could produce positive candidate mention flags and counts by category, accompanied by explicit `mention_extraction_method`, `evidence_note_sha256`, `source_point_number`, `actor_resolved=false`, and `event_independently_verified=false`. Each category needs negation and retrospective-reference handling. A note attached to point N might explain an earlier event; its sequence position is not proof of the moment an interruption began or ended. No note means unknown. Do not infer a player's diagnosis, injury severity, or a definitive timeout count from these notes. No one should label an unnamed official using a guess from the tournament.

## 3. Balls and court products: official documents, missing event crosswalk

Primary starting points are the ITF's [approved balls](https://www.itftennis.com/en/about-us/tennis-tech/approved-balls/), [classified surfaces](https://www.itftennis.com/en/about-us/tennis-tech/classified-surfaces/), and [technical publications](https://www.itftennis.com/en/about-us/organisation/publications-and-resources/tennis-tech/) pages. The [2024 ball approval procedure](https://www.itftennis.com/media/12222/2024-itf-ball-approval-procedures.pdf) documents approval-year treatment, packaging names and laboratory criteria. It is a procedure document, not proof that a listed product was used at a given tournament.

The ITF's [official tournament supplier announcement](https://www.itftennis.com/en/news-and-media/articles/dunlop-selected-as-tennis-ball-sole-supplier-for-paris-2024/) is a concrete dated lead for event-to-supplier context. More such first-party tournament announcements can support a small catalog. Do not copy article bodies or advertising language. A supplier brand does not establish a particular ball model, felt specification, pressure, batch or ball-change condition unless that detail is explicit.

No openly licensed historical tournament-year CSV, stable public API schema, or complete event-to-product crosswalk was verified. Therefore candidate export fields such as `tournament_id`, `season`, `ball_manufacturer`, `ball_model`, `approval_year`, `surface_product`, `court_pace_category`, `valid_from`, `source_published_at`, `rights_status` are **proposed**, not verified upstream column names. Publisher copyright applies to documents; an unrestricted commercial dataset license was not identified. ITF product classification is not a measured court-pace index for every actual match.

## 4. Roof, day/night and clock: distinguish policy from observation

Official [Roland-Garros infrastructure plans](https://www.rolandgarros.com/fr-fr/article/roland-garros-2024-toit-retractable-court-suzanne-lenglen), the tournament's [edition changes and scheduling announcement](https://www.rolandgarros.com/fr-fr/article/roland-garros-programmation-nouveautes-edition-2024), and the federation's [roof implementation report](https://www.fft.fr/actualites/toit-suzanne-lenglen-inauguration-roland-garros) provide dated sources for a venue-policy catalog. Maintain separate fields for an announced future change, verified availability, and actual match-specific operation. Roof-capable is not roof-closed; rain is not proof of closure. Tournament-wide scheduling announcements are not actual match start timestamps.

For clock rules, an [official ATP Next Gen event release](https://www.atptour.com/-/media/sites/atp-tour/press/press-releases/19-october-2018-tag-heuer-next-gen-atp-finals.pdf) documents event-specific clock technology and trials. Treat trial rules as scoped to that competition and period. Do not apply them retroactively to the whole ATP/WTA tour. No public historical point-by-point countdown series or complete umpire/time-violation feed was verified.

Useful proposed catalog fields are `venue_or_tournament_id`, `policy_kind`, `policy_value`, `announced_at`, `effective_from`, `effective_to`, `implementation_verified`, `source_url` and `rights_status`. Official articles have no established open bulk-content license; only a carefully cited minimal factual catalog is a candidate. Exact day/night or solar elevation remains unresolved where the existing data has only a match date. Never substitute noon, a session's advertised start, or the current timestamp.

## 5. Pace proxy: an outcome-derived index is not a sensor measurement

Tennis Abstract publishes annual pages at `https://www.tennisabstract.com/cgi-bin/surface-speed.cgi?year=YYYY`. Its [author's methodology discussion](https://www.tennisabstract.com/blog/category/surface-speed/) describes an ace-rate measure adjusted for participants. The visible annual table labels are `Date`, `Tournament`, `Surface`, `Ace%`, and `Surface Speed`; annual pages extend back to 1991. This is an HTML-table lead, not a verified bulk CSV API. No explicit reuse grant for these particular HTML tables was established, and the MCP repository license must not be assumed to cover unrelated site products.

A completed-event index uses match outcomes from the event, and possibly a retrospective annual normalization. It cannot be attached as a pre-event feature to those same matches. It also blends balls, weather, participants and surface, and does not isolate physical court speed. The safer research alternative is to calculate a differently named, strictly lagged venue serve-environment statistic from already licensed data, with sample size, uncertainty and player/opponent adjustment. That is a future modeling task, not authorized collection in this audit.

## Rejected lead and unresolved fields

[Tennis-Data's primary download page](https://www.tennis-data.co.uk/data.php) offers historical annual files but explicitly restricts intended use to private individuals and excludes commercial or data-training products using automated bots/scrapers/AI. **Do not ingest these files for this automated project without permission.** Public availability is not a reuse license; third-party descriptions calling the files open do not override the source terms.

No complete, historically timestamped, openly licensed bulk source was verified for actual roof state, elapsed serve-clock time, chair-umpire assignment, or medical-timeout chronology. These remain missing fields, not zeros. All candidate collections must preserve 2025 onward as holdout context, avoid publication-time leakage, and remain separate from baseline models until explicitly reviewed.
