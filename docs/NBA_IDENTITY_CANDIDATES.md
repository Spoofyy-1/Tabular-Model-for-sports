# NBA/ESPN player identity candidates

`enrichment/nba_crosswalk.py` fetches the small 2026 and 2027 player snapshots from the [SportsDataverse NBA crosswalk release](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/nba_crosswalk), only on GitHub-hosted runners. It writes `data/enrichment/nba_crosswalk/player_identity_candidates.csv.gz`, `schema.json`, `enrichment_summary.json`, source provenance, and license notices.

All original fields remain present, including source names, IDs, matching methods, confidence, keys and team/season labels. Normalized identifier columns and consistency flags are added separately. CSV nulls use literal `\N`; import ID columns as strings and preserve empty text separately.

The [producer's builder](https://github.com/sportsdataverse/hoopR/blob/main/R/nba_crosswalk.R) uses matching based on names within team groups, with jersey and birth-date evidence. These are **candidate mappings**, not an official identity registry. Source confidence scores are not independent verification. The snapshots concern current rosters and do not establish historical team membership or cover all retired players.

`candidate_pair_bijective_within_snapshot` and `candidate_pair_bijective_across_loaded_snapshots` require each complete NBA/ESPN ID to have exactly one counterpart in the indicated scope. Duplicate rows are separately flagged; missing/invalid IDs remain incomplete. Even a one-to-one match can identify the wrong person. Therefore `identity_independently_verified` and `automatic_training_join_allowed` are always false, and this collector changes no training data or models.

The producer publishes CC BY 4.0 and the distribution repository MIT; copies and attribution are included. Retrieval times and byte hashes document the exact loaded snapshots. No source player rows are accessed or stored on the owner's machine.

```bash
# GitHub-hosted runner only; uses existing pandas/pyarrow/requests dependencies.
python enrichment/nba_crosswalk.py
```
