# Historical court infrastructure pilot

Run `python court_context/osm_infrastructure.py` only in GitHub-hosted Actions. The guard executes before network access or dataset writes. Dependency: `requests`; remaining imports are Python standard library. Outputs default to `data/court_context` and packaged releases to `dist`. No model training occurs.

The source archive and its Wikidata places member are SHA-256 pinned to release `extras-36173901119-1`. The hosted collector selects at most three distinct coordinates whose **place label** contains tennis and a facility term, excluding event and city terms. This is conservative label screening, not a verified venue classification. No acceptable labels produces an explicit empty report rather than invented coordinates.

For each candidate, Overpass receives two historical queries, dated January 1, 2020 and 2024. Each covers an approximately 600 m square, requests individual tennis pitches, has a 30-second client deadline, and runs sequentially with a two-second interval. At most six Overpass requests are attempted, without redirects or retries, under a 15 MB source budget. The archive permits at most three redirects to HTTPS GitHub/release-asset hosts; all actual HTTP calls count against a separate ten-request limit. Individual Overpass responses are capped at 2 MB. Partial failures and invalid elements appear in aggregate reports.

Court CSV rows retain map version/edit time, snapshot time, centre and raw surface/indoor/covered/lighting tags. Future edits are rejected. Duplicate element identities within a response quarantine every conflicting record. Selection retains one coordinate per Wikidata place. Row counts measure element/place/snapshot observations, not distinct physical courts; separately mapped nodes and ways can represent the same court. Missing tags mean unknown. Map dates do not prove infrastructure effective dates; centres and nearby facilities do not identify a match court. Roof operation, night-session assignment and automatic training joins remain unverified. Mapper identities are omitted.

The bundle keeps OSM-derived observations under ODbL attribution/share-alike terms, and the selected Wikidata subset separately under CC0. Sources and hashes accompany compressed CSV, schema, summary and release manifest. Consult [the source audit](../docs/TENNIS_CONTEXT_SOURCE_AUDIT.md) for primary documentation and limitations.

Synthetic tests: `PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest court_context.test_osm_infrastructure`. Tests perform no real source requests or dataset writes.
