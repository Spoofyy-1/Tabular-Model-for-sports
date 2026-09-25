# Coaching appointment announcements

Run `python coach_events/collect.py` on a genuine GitHub-hosted Actions runner. Defaults are `data/coach_events/` for normalized outputs and `dist/` for publication assets. Optional flags are `--output-dir` and `--package-dir`. Dependencies: Python 3.11+, requests, beautifulsoup4. Local synthetic tests use `python -m unittest discover -s coach_events -p test_collect.py -v`.

The fixed catalog contains eight official NBA/NFL team pages identified in `docs/COACH_TENURE_SOURCE_AUDIT.md`. Each appointment must match the fetched article's lead, source-specific team/role wording and publication metadata. Names and dates are extracted rather than seeded. Agreement-to-terms announcements remain a separate event type. Missing publication dates remain unknown; contradictory labels or weekday mismatches preserve a conflict flag and prevent a dated event.

Limits: 20 requests, 20 MB total, 4 MB per page, no retries. At most two HTTPS redirects are permitted, on the same host and same article path/query; only a trailing-slash path variation is accepted. HTTP 403 and unrelated-story redirects are recorded as failures. No access challenges are bypassed.

`data/coach_events/` contains `appointments.csv.gz`, `summary.json`, `schema.json`, `source_audit.json`, and `RIGHTS.md`. CSV unknowns use `\N`. Effective employment start/end, canonical identities and first-game assignments are null. Event dates describe dated public announcements, not verified employment commencement. Historical availability and automatic training eligibility remain false. No article bodies, headlines or quotes are exported; source hashes cover decoded UTF-8 text, and evidence hashes cover normalized article-block UTF-8 text.

`dist/` receives `csv-coach-appointments.tar.gz`, `coach_appointments_asset_manifest.json`, and `coach_appointments_summary.json`. Archive members are explicitly limited to the five output files under `data/coach_events/`, even when an alternate output directory is supplied. The manifest records file/archive sizes and SHA256 checksums. The separate summary and stdout contain aggregate counts and per-source operational outcomes, not appointment rows.

The collector completes with audit-only outputs when sources are inaccessible. Source licenses and content restrictions remain in force; no open article-content license or blanket commercial permission is asserted. The associated workflow is managed separately.
