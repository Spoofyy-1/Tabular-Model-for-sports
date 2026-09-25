# Cited public spectator context

`python extras/celebrity_context.py` collects a deliberately small research sample on a GitHub-hosted runner. It requires `beautifulsoup4>=4.12`, `spacy>=3.8,<3.9`, and the pinned `en_core_web_sm` 3.8.0 English named-entity model. Local execution fails before network requests, model loading, or dataset writes. Only source URLs and general parsing rules are stored with the code; no attendance facts are manually seeded.

The source audit found official NBA game recaps containing dedicated spectator sections and official USTA articles containing match-specific photo captions. It did not establish an openly licensed, complete bulk celebrity attendance database. NFL articles often mix the current game, earlier games, future invitations, and unrelated appearances. Those sources are inventoried without creating attendance rows. Announced entertainment is not treated as observed attendance.

The bounded catalog has seven article requests and at most two official NBA boxscore requests. Each article is limited to 8 MB and each boxscore to 2 MB, so the absolute transfer cap is 60 MB. Article responses remain in runner memory; no raw body or image is saved or included in a release. Requests are sequential with a short interval. HTTP failures and parser failures remain in the source catalog instead of producing fabricated facts.

## Extraction and identity

- NBA extraction only considers named people in a `VIP WATCH`, `CELEBRITY WATCH`, or `CELEBRITY SIGHTINGS` section with an explicit presence predicate. It stops at the next heading. The official game ID comes from the page URL; game date comes from the official page title and UTC start, when available, from the matching official boxscore. Current boxscore player names are excluded. There is no guessed ESPN mapping.
- USTA extraction requires a match-specific singles-final caption. Names are extracted only before the match description, excluding opponents and photographer credits. An explicit date in the caption takes priority over article publication date. A dedicated final-day gallery can resolve a date only when its explicit weekday matches the publication date. Other rounds are excluded because a date/gender/round alone may describe multiple matches.
- `en_core_web_sm` identifies candidate person-name spans. Spelling is preserved; there is no guessed cross-article identity merging. Single-token names are conservatively omitted. Machine extraction can still miss or misidentify names, so `identity_independently_verified` stays false.
- A tennis event key combines tournament, event calendar date, gender, and singles-final round. It is not a canonical match/player identifier. ATP and WTA labels describe the men's/women's event groups.

All rows are positive documented-presence associations. Missing records mean **unknown**, never absent. These selected high-profile articles cannot support a complete attendance feature, an unbiased celebrity-effect estimate, or a claim that attendance causes player performance changes.

## Time and data use

The export separates event date, optional exact event start, article publication timestamp, publication date, precision, retrieval time, and derivation. A date-only label is never converted into a made-up midnight timestamp. A currently retrieved article's publication metadata does not prove the text was available at that time in the past. Article updates and immutable historical captures have not been reconciled. `historical_publication_time_verified`, `eligible_for_pregame_feature`, and `automatic_training_join_allowed` remain false on every row. Event dates through 2024 and 2025 onward are partitioned, but this does not grant historical training eligibility.

`data/extras/celebrity_context/` contains:

- `documented_presence.csv.gz`: cited factual associations, temporal precision and usage flags.
- `source_catalog.csv.gz`: fetch/parser outcomes, publication metadata, source hashes and row counts, including rejected source types.
- `summary.json`: aggregate coverage, failures, source provenance, auxiliary NBA metadata and limitations.
- `schema.json`: types, missing-value counts, null encoding and identity semantics.
- `SOURCE_RIGHTS.md`: source attribution and rights limitations.

CSV nulls use literal `\N`; empty text remains distinct. Articles and photographs are copyrighted by their publishers and licensors. Only minimal factual associations and citations are exported; no article prose or images are redistributed. No open content license or unrestricted commercial reuse permission is claimed. This research bundle is separate from trained models.

Synthetic tests (`python -m unittest discover -s extras -p test_celebrity_context.py`) cover section boundaries, mixed-match dates, opponent exclusion, date precision, future announcements, ambiguous event identity, and the local-execution guard. No real articles or attendance datasets are used by tests.
