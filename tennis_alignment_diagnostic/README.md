# Frozen tennis alignment diagnostic

Run `python tennis_alignment_diagnostic/collect.py` with no arguments on a genuine GitHub-hosted runner. Install `context_requirements.txt`; Python 3.12 is suitable. Local execution of real inputs is prohibited. The only inputs are the two pinned release archives and their manifests documented in `docs/TENNIS_ALIGNMENT_DIAGNOSTIC_PLAN.md`. No live Wikidata, weather, Overpass, point-data or odds requests are made.

The command caps inbound decoded bytes at 60 MB, HTTP attempts including redirects at 12, and collection work at 300 seconds. It verifies archive/member hashes, schema null conventions, required columns, row counts and MCP license notices. It projects match CSVs to identity columns. Source-export dates are counted before parsing as well as afterward; those rows already reflect the original producer's import-time null conversion.

Aggregate CSVs separate independent and cumulative matching gates, first failures, quality flags, row cardinality, duplicated IDs, and source-export versus normalized identity tuples. The alias comparison retains the same selected label and compares the current aliases with the union of exact source-provided English/default aliases. Added collisions remain blocked. Direct and swapped order counts are observations, not additive accepted matches. No raw names, aliases, match IDs, source dates, outcomes or row samples are published.

The summary JSON includes the same bounded gate, alias and metadata counts for review without downloading CSV archives locally. Source-export versus normalized integrity comparisons preserve literal text changes and canonicalize only valid date representations.

The report always sets accepted match joins and accepted weather rows to zero. A diagnostic classification of `one` is evidence for a later review, not permission to promote a match. The two earlier zero-join pilot releases remain unchanged. Source metadata conflicts and retrospective weather limitations still apply.

The generated `data/tennis_alignment_diagnostic/` tree keeps CC0-only contract counts apart from MCP-derived noncommercial diagnostics, preserves original license notices and records input hashes, code revision and Python/pandas versions. Only explicitly generated files are packaged. Publication files are:

- `dist/csv-tennis-alignment-diagnostic.tar.gz`
- `dist/tennis_alignment_diagnostic_summary.json`
- `dist/tennis_alignment_diagnostic_schema.json`
- `dist/tennis_alignment_diagnostic_asset_manifest.json`

Synthetic tests perform no source requests or dataset-file writes: `PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tennis_alignment_diagnostic -p test_collect.py -q`.
