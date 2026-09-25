# Tennis alignment: one bounded hosted diagnostic

Plan date: 2026-09-25. The bounded diagnostic is implemented in `tennis_alignment_diagnostic/collect.py`; hosted execution is pending. It does not change accepted matching. Local work inspected code, primary documentation and release manifests, and tested fabricated in-memory fixtures only. No source datasets were downloaded locally.

The verified outcome remains **zero accepted match joins and zero weather rows** in [tennis-weather-36187582212-1](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/tennis-weather-36187582212-1/tennis_weather_summary.json). One candidate passes entity prerequisites but fails the combined exact MCP lookup; the other lacks the required court claim. Resolving English/default labels corrected one source-contract omission, but did not establish coverage. The two pilot releases are overlapping snapshots, not additive independent facts.

## Frozen inputs; no live source refresh

Use the corrected pilot's [manifest](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/tennis-weather-36187582212-1/tennis_weather_asset_manifest.json) and [archive](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/tennis-weather-36187582212-1/csv-tennis-weather-alignment-research-only.tar.gz). The archive is **8,434 bytes**, SHA-256 `c9e27e95a667b9f2fe4b6bc12b0af6e091be7770dc30707cda96ded1d42fdfea`. Under `data/tennis_weather/`, the required members are:

- `wikidata_cc0/claims.csv.gz`: 2,181 bytes; SHA-256 `89e79378ded3289568d5198078b3fffd622ceff3fdf86d8c205fccd0d18f9d64`.
- `wikidata_cc0/labels.csv.gz`: 648 bytes; SHA-256 `4ea62d5bba15601c9dce6648426a2d6051355c38deb959236907c140df0703c6`.
- `schema.json`, `source_manifest.json`, and the original license notices, verified against that manifest.

Combine these with the already pinned [MCP archive](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/tennis-points-36173714850-1/csv-tennis-mcp-research-only.tar.gz) and [manifest](https://github.com/kennynakao/Tabular-Model-for-sports/releases/download/tennis-points-36173714850-1/tennis_points_asset_manifest.json). It is **45,950,657 bytes**, SHA-256 `dd23c16cff991bcf1805ad8f97288312dee3c21fc0e513da1f5c1c191ea38f62`. Under `data/tennis/mcp_research_only/`, read `mens_singles/matches.csv.gz`, `mens_singles/source_matches.csv.gz`, `schema.json`, `source_manifest.json` and `LICENSE.txt`. Resolve and verify member hashes from the pinned archive's manifest. Project match CSVs to identity columns; do not consume point tables, score fields, notes, winners, descriptive player rankings or market prices.

The two archives total **45,959,091 bytes**. The authorized implementation uses a 60 MB inbound cap, at most 12 HTTP attempts including redirects and 300 seconds of collection work. Require the genuine hosted-runner guard before every real input/output path. Apply fixed HTTPS host checks, source and member hashes, bounded decompression, safe member validation and explicit publication inventory. No fresh Wikidata, weather, Overpass or article requests are needed. There is no reason to transfer the 758,763-byte weather archive again for this diagnosis.

## Reproduce prerequisites before reading MCP

Replay the fixed entity prerequisites from the preserved structured claims, retaining source revision, claim ID, rank, snak type, qualifiers and reference count. Use the raw `label_en`, `label_mul`, `aliases_en_json` and `aliases_mul_json` fields; do not infer them from the resolved display label. Validate required columns and unique entity-label records first. Do not fabricate API reference objects from a numeric reference count; preserve reference provenance separately.

Report prerequisite pass/failure counts and reason categories for the two existing catalog IDs. Court absence remains a hard exclusion. If neither candidate passes, stop with an audit report before downloading MCP. A candidate that fails prerequisites is not eligible for subsequent matching, even if a name/date coincidence can be found.

## Count each exact matching gate

For each prerequisite-valid candidate, report both row counts and distinct nonempty match-ID counts after every stage below. Also report a mutually exclusive first-failure histogram. Keep this funnel separate from independently evaluated counts so an early failure does not conceal later field coverage.

1. All normalized metadata rows, then `competition_group == mens_singles`.
2. Supported tournament label after the existing NFC/case/whitespace normalization. Preserve the current allowlist; report unsupported/missing/empty counts without publishing raw labels.
3. Parseable naive calendar dates, then equality with the source candidate day. Categorize failures as null, empty, invalid syntax, offset-aware timestamp or non-midnight naive timestamp. This diagnoses representation without turning a date into a UTC kickoff.
4. Supported final-round label, independently and in combination with tournament/day. Preserve the current `f`/`final` allowlist.
5. Exact unordered full-name pair under the **current** term-selection policy, counting source-label-only matches separately from matches requiring a selected source alias. Count direct and swapped player order without treating them as different matches.
6. Existing quality gates: nonempty ID, `singles_metadata_eligible`, retirement/walkover flag, and calendar-date precision. Report each failure and whether any otherwise exact identity row fails a quality gate. The present collector aborts a candidate on such a failure; diagnostics must not silently select a different passing row.
7. Exact candidate cardinality: zero rows, exactly one row, or multiple rows. If one row survives, separately count occurrences of that ID in the **entire** normalized metadata table, as the current collector does. Distinguish multiple distinct IDs from duplicated rows with one ID.

Independently report counts for exact day alone; supported tournament plus day; final round plus day; and current full-name pair alone and together with tournament/day/round. These are diagnostics, not alternate match rules. A pair found on another day, round or tournament never becomes an accepted match.

Global input diagnostics should include total rows, distinct IDs, null versus empty IDs, duplicate-ID groups, exact duplicated metadata rows, and per-required-column null/empty counts. Do not merge nulls and empty strings. Record code revision and the immutable input hashes so the same report can be reproduced.

## Distinguish source absence from normalization loss

`tennis/point_data.py:metadata` resolves source headers, retains full player/tournament/round strings, parses dates using pandas, removes exact duplicate normalized rows, and excludes missing or conflicting match IDs. It does not construct player aliases. An absent normalized match can therefore mean missing source coverage or a normalization/key-quality exclusion; the current aggregate failure cannot distinguish them.

In the same hosted read, inspect only the raw source metadata identity columns. Report header-resolution success, date parse failures, missing IDs, exact duplicates, conflicting-ID exclusions and the number of raw identity candidates with no normalized counterpart. Count null, empty and date-syntax categories before parsing as well as afterward. Compare per-ID identity tuples using literal player/tournament/round text and canonicalized valid calendar dates, so case or whitespace changes remain visible in this integrity check. Compare with the published producer's key-quality counters where available. Preserve the producer's exclusion behavior; do not recover a conflicting raw row by choosing its score, winner, input order or convenient date. An unknown header or inconsistent schema should become a diagnostic failure, not a guessed mapping.

## Omitted default aliases: a counterfactual count only

[Wikidata documents `mul` default labels and aliases](https://www.wikidata.org/wiki/Help:Default_values_for_labels_and_aliases); aliases need not be repeated in every language. The current collector requests `en|mul`, but `resolved_aliases()` returns the first nonempty language list. Consequently, an English alias can suppress an additional default-language alias in the matching set. This behavior was reproduced with fabricated inputs. Its effect on the actual pilot remains unverified.

Define two diagnostic name policies before reading data:

- **Current:** English label if present, otherwise default label; English aliases if nonempty, otherwise default aliases. Retain the existing full-name token requirement and normalization.
- **Both alias languages:** the same selected label plus the exact source-provided English and default aliases together. Preserve term language and whether it came from a label or alias internally. Do not add alternative spellings or transliterations.

For each policy, count raw/nonempty/distinct eligible terms, terms removed by normalization deduplication, terms rejected by the existing token requirement, and the intersection between the two participants' name sets. An intersecting name set remains ambiguous. For the second policy report how many additional distinct aliases were actually omitted by the first, how many exact full-name pair matches they add within the already fixed tournament/day/round subset, and whether they change cardinality from zero to one, zero to multiple, one to multiple, or leave it unchanged. Count collision-blocked cases separately; never promote these counterfactual counts into accepted output.

The unselected `mul` label, when an English label exists, is another available source term but is **outside this alias-only comparison**. Count its presence without silently introducing a third matching policy. No accent stripping, punctuation removal, edit distance, surname-only matching, initial matching, manual player names or outcome-assisted resolution is proposed.

## Implemented report and decision boundary

Run `python tennis_alignment_diagnostic/collect.py` with no arguments on a genuine GitHub-hosted runner, using `context_requirements.txt`. It publishes aggregate CSVs plus schema, provenance and original license notices. The summary JSON mirrors bounded gate, alias, raw-versus-normalized and source-metadata counts, permitting hosted-result review without downloading CSV archives locally. Candidate catalog IDs identify fixed scope; player names, match IDs, literal source rows, exact event dates, alias strings and source-value hashes are excluded. MCP-derived counters remain attributed and separated from CC0-only source-contract counts; the diagnostic bundle is not wholly CC0. Code revision and Python/pandas versions are recorded.

Publication outputs are:

- `dist/csv-tennis-alignment-diagnostic.tar.gz`
- `dist/tennis_alignment_diagnostic_summary.json`
- `dist/tennis_alignment_diagnostic_schema.json`
- `dist/tennis_alignment_diagnostic_asset_manifest.json`

Possible conclusions are source coverage absent, producer-normalization exclusion, unsupported field representation, omitted exact source alias, ambiguous identity, or still unresolved. The report should support whichever occurs. Even a counterfactual single match would require a separately reviewed policy change and normal court/date/identity checks. This plan does not alter acceptance, create weather rows, enable training or claim execution value. Zero accepted joins remains the published result.
