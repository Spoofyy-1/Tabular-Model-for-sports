# New tennis context source: historical court infrastructure

Audit date: 2026-09-25. This is a documentation-only source audit. No map responses, sports records or other real dataset rows were downloaded or saved locally. No collector was added, and training remains paused.

The strongest new bounded collection candidate is **OpenStreetMap court infrastructure at historical map snapshots**, accessed through Overpass. This adds mapped indoor status, covering, lighting and surface detail to the existing Wikidata venue coordinates and weather. OpenStreetMap contributors are the primary publishers of these map observations; this is not an official tournament operations feed. Existing MCP point and note collections do not supply this dated infrastructure history.

## Measurable fields and their meaning

The primary [tennis tagging documentation](https://wiki.openstreetmap.org/wiki/Tag:sport%3Dtennis) describes individual court areas using `sport=tennis` with `leisure=pitch`, and associated `surface`, `indoor` and `lit` tags. It distinguishes individual courts from stadiums, sports centres, shops and other tennis-related objects. Querying only the sport tag would mix these units and requires an explicit object-type filter.

[Covering](https://wiki.openstreetmap.org/wiki/Key:covered) and [lighting](https://wiki.openstreetmap.org/wiki/Key:lit) have separate tag definitions. Preserve upstream strings, including conditional or unfamiliar values. These describe mapped infrastructure: a covering is not an observation that a retractable roof was closed during a match, and a lighting tag does not establish that the lights were on. An absent tag remains unknown. Surface material is not measured court pace, ball rebound or friction. ATP and WTA matches at the same complex cannot be assigned a particular court without a separate verified match-to-court source.

Court polygons also offer a possible later orientation measurement. Treat this as a geometry-derived candidate only after checking that the mapped outline represents one court. The tennis documentation notes that mapping the clearance outside the lines is inconsistent; do not infer regulation dimensions from polygon area. Polygon orientation alone cannot establish sun glare or wind exposure at a point with no wall-clock timestamp.

## Historical access and timestamp contract

The documented query endpoint is `https://overpass-api.de/api/interpreter`. The [Overpass language reference](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL#Date) supports a global `date` setting in UTC, selecting the database state at that instant. Available history begins at `2012-09-12T06:55:00Z`; requests for earlier dates return that earliest state, so the collector must reject earlier cutoffs. Individual court coverage and tag completeness are unmeasured and may begin much later. No claim of full tour coverage is justified before a hosted pilot.

Use JSON responses and documented metadata output to retain element `type`, `id`, `version`, `timestamp`, geometry or centre, and selected tags. An element timestamp records its latest map edit, not a roof opening, resurfacing date or tournament publication time. Retain the requested snapshot separately from the element timestamp and retrieval time. [Attic-data documentation](https://wiki.openstreetmap.org/wiki/Attic_Data) explains that historical versions include changed and deleted objects; they represent map history rather than a comprehensive history of the real world.

For a pilot, propose January 1 snapshots for 2013 through 2024 at a small number of independently verified tournament facilities. Select only a snapshot strictly before a match. Do not use today's tags to fill historical gaps, or use year-end tags for earlier matches that year. Venue identity must be valid for the match year; a tournament may relocate or use temporary courts. This creates a dated map-observation candidate, not independently verified contemporaneous tournament infrastructure.

## Hosted collection proposal

Run only on a GitHub-hosted runner. Begin with at most ten verified facility bounding boxes, twelve annual snapshots, sequential requests, a 25 MB total response cap and an explicit timeout. Reuse the existing remote venue release after hash verification; do not embed venue rows in local code. A bounding-box hit is a candidate association, not an automatic verified venue join. Keep unrelated facilities, training courts and ambiguous overlapping objects unjoined.

The provider's [resource policy](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html) gives broad daily guidance of about 10,000 requests and 1 GB, with load-dependent slots and cooldowns. It identifies HTTP 429 and 504 rejection modes and discourages whole-world scraping by tiled queries. The proposed pilot is deliberately much smaller. Back off on refusals, stop on persistent failures, and report incomplete snapshots rather than silently treating an empty or errored response as no courts. This audit verified documentation, not current endpoint availability.

Publish a separate compressed CSV bundle containing source element identity, requested snapshot, edit and retrieval timestamps, raw selected tags, candidate facility ID, association status, response hash and source query. Publish schema and aggregate missingness by tag and year. Retain `\N` for missing values. Exclude mapper usernames and identifiers from exports because they are unnecessary. These are proposed output fields, not an existing source CSV header. No historical match should gain automatic training eligibility from this pilot.

## Reuse rights and priority

The [OpenStreetMap Foundation copyright notice](https://www.openstreetmap.org/copyright) licenses map data under the Open Database License, with attribution and share-alike obligations. It separately licenses documentation under CC BY-SA 2.0. A derived map database should retain ODbL attribution and its license; do not relabel it CC0 because the existing venue lookup uses Wikidata. Keep it separate from the MCP noncommercial research bundle until any combination's applicable terms are assessed.

Recommended next step is a hosted coverage pilot, followed by review of tag completeness and venue-association quality. It can establish whether infrastructure observations improve historical context coverage. It cannot yet provide match-specific roof operation, night-session participation, interruptions, travel or a betting advantage. Those remain unmeasured.
