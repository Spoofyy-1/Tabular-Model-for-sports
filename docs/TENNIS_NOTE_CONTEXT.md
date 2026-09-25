# Tennis note mention candidates

`python tennis/notes_context.py` reads the already published `tennis-points-36173714850-1` release on a GitHub-hosted runner. Dependencies are the existing pandas and requests packages. It does not fetch fresh MCP files, train models, place trades, or run data processing locally.

The input is `csv-tennis-mcp-research-only.tar.gz`, bounded to 250 MB. The collector checks the GitHub release tag, declared asset size, available GitHub digest, archive SHA-256 from `tennis_points_asset_manifest.json`, manifest inventory and each consumed member's SHA-256. Source archives stay under `RUNNER_TEMP` and are removed in a `finally` block. CSV members are streamed in 25,000-row chunks through gzip; archive members are never extracted as filesystem paths. Unexpected members, traversal, symlinks and duplicate member names fail closed.

The source `Notes` field is optional contributor free text. The collector selects keyword matches in nine categories: medical/timeouts; weather; roof; light/visibility; noise/crowd; time violations/serve clock; equipment; underarm serves; and interruptions. Each match is a **candidate mention**, not an established event. Simple negation and retrospective-reference flags help review but do not resolve the meaning, actor, or event time. An absent note or absent match never creates a negative example.

The output retains the licensed original note, matched phrases, note hash, source member/hash, original source row number, raw point number/server, and stable source-record identifier. Exact original rows remain distinguishable even where the source contains duplicate point records. A note attached to point N may describe an earlier occurrence, an opponent, a denied event, or a general observation. It is not a valid pre-point feature without separate timing review. No medical diagnosis, injury severity, chair-umpire identity, confirmed sanction, or elapsed clock duration is inferred.

Existing normalized match metadata supplies player names, date precision, tournament, round and surface. A separate exact join to the published canonical point table carries its `evaluation_split` and score-prefix validity forward unchanged. Invalid source point numbers or source points excluded by canonical validation remain unresolved. All rows keep `mention_independently_verified=false`, `actor_resolved=false`, `time_of_event_verified=false`, `absence_inference_allowed=false`, and `automatic_training_join_allowed=false`.

Outputs under `data/tennis/notes_research_only/`:

- `candidate_mentions.csv.gz`: one row per source note and matched category, including audit text and temporal/identity flags.
- `candidate_counts.csv.gz`: category counts by competition group and original evaluation split; these count source mentions, not independent incidents.
- `summary.json`: aggregate coverage, source provenance, checksums, missingness and limitations.
- `schema.json`: field types, null convention, exact keyword rules and derivation notes.
- Original `LICENSE.txt`, `SOURCE_README.md`, and `SOURCE_data_dictionary.txt` copied from the verified input archive.

CSV nulls use literal `\N`; empty strings stay distinct. Men’s and women’s source groups remain separate; these labels do not independently verify ATP/WTA tour sanction. All data remains **CC BY-NC-SA 4.0**, attributed to Jeff Sackmann, Tennis Abstract's Match Charting Project and contributors, with share-alike and noncommercial restrictions. This derivative makes no commercial betting eligibility claim.

Run `python -m unittest discover -s tennis -p test_notes_context.py` for synthetic in-memory tests of note selection, false/negative examples, source identities, null encoding, member hashing, archive safety and the local execution guard. Tests never read the real release or write a dataset.
