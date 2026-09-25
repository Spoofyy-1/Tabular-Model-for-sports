# Wimbledon calendar-day weather pilot

Run `python tennis_weather/collect.py` with no arguments on a genuine GitHub-hosted runner. The collector uses Python 3.9 or later, the standard library and `requests`; the existing context requirements also suffice. It checks the cloud guard before network access or output. Never run real collection locally.

The fixed source contracts are documented in `docs/TENNIS_WEATHER_ALIGNMENT_RESEARCH.md`. Two pinned release archives total 46,709,420 bytes. The complete request budget is 60 MB, 24 HTTP requests including redirects and 480 seconds of network work. Only GitHub release hosts and Wikidata are permitted. No new weather, Overpass, odds or point-data calls are made. No source archives are extracted onto the filesystem; bounded selected members are verified in memory.

The fixed 2023 and 2024 Wimbledon final entity candidates must independently pass day-precision, edition, round, participant and court validation. Missing/conflicting/unknown claims are excluded. A unique exact MCP player-pair/date/tournament/round match is required, without using scores or outcomes. Weather selection uses the existing Wimbledon identity, a shared facility chain, a 2-metre archived-coordinate tolerance and a 500-metre court sanity bound. Distance never selects a substitute location. These thresholds are fixed before collection.

Wikidata requests explicitly include `en|mul`. The publisher documents that [default labels and aliases use the `mul` language code](https://www.wikidata.org/wiki/Help:Default_values_for_labels_and_aliases), while [the Action API language filter limits returned terms](https://www.mediawiki.org/wiki/API:Presenting_Wikidata_knowledge). English terms take precedence; otherwise source-provided default terms are used. Raw language fields and selected-language provenance are exported. Missing participant names and intersecting name sets have separate exclusion reasons. This repairs omitted source terms without supplying hardcoded names or relaxing the exact identity/court checks.

Candidate prerequisites are checked immediately after the entity requests. Each failure retains its exclusion reason. If neither candidate passes, the collector publishes the CC0/audit outputs without downloading either source archive; a rejected candidate never blocks another valid candidate.

The result is retrospective `calendar_day_overlap_context`. Europe/London and source-date semantics are explicit assumptions. The overlapping UTC daily buckets remain separate, with numeric weather columns and recorded units. Their boundaries are not match timestamps. Actual start/end, session and roof operation stay unknown. Null and empty numeric observations remain unknown, never zero. Canonical split labels are retained; training eligibility is always false.

The output tree is `data/tennis_weather/`. `wikidata_cc0/` contains normalized source claims and labels under CC0; `research_only/` contains the MCP-derived match/weather association and quarantine CSVs under the source's noncommercial share-alike restrictions, plus attributed weather terms. No article text is exported. Zero accepted matches is a valid audit-only result; the aggregate report records exclusion counts and input-stage errors without source rows or response bodies.

The same guarded command packages four files for publication:

- `dist/csv-tennis-weather-alignment-research-only.tar.gz`
- `dist/tennis_weather_summary.json`
- `dist/tennis_weather_schema.json`
- `dist/tennis_weather_asset_manifest.json`

The archive contains only the explicitly generated files, including license notices and a source manifest. Existing unrelated files are not added. The summary and stdout contain aggregate diagnostics. Source/schema failures are reported as audit-only or partial coverage rather than a claim of successful alignment.

Synthetic tests require no cloud access and write no dataset files: `PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tennis_weather -p test_collect.py -q`.
