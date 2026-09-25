# Expanded context release

[Release context-36094215424-1](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/context-36094215424-1) completed successfully on September 25, 2026 UTC. All collection and processing ran on GitHub-hosted Actions. The four archives total 396,642,304 bytes. Archive sizes and SHA-256 hashes were checked against GitHub's release-asset metadata without downloading data to the owner's workstation.

| Table | Records | Observed coverage / meaning |
| --- | ---: | --- |
| NBA play events | 14,205,388 | Requested season-ending years 2002–2026; per-season coverage in summary |
| NBA player-game event summaries | 649,469 | Earlier event archive aggregated by ESPN game/player; partial observations, not reconciled boxscores |
| NBA schedules | 32,290 | October 2001–June 2026; 64,580 team-game schedule rows |
| NBA shots | 6,328,071 | November 1996–June 2026; regular season and postseason |
| NBA offensive-player/defender matchups | 2,143,437 | October 2017–June 2026 |
| NFL weekly tracking | 24,833 | 5,592 passing, 13,623 receiving, 5,618 rushing; 2016–2026 |
| NFL tracking season summaries | 2,329 | Stored separately; never target-game pregame features |
| NFL historical weekly depth charts | 865,329 | Seasons 2001–2024 |
| NFL timestamped depth-chart snapshots | 1,078,926 | Seasons 2025–2026; multiple dates and roles per player |
| NFL newer injury reports | 6,760 | 6,068 in 2025 and 692 through week 3 of 2026; complements baseline 2009–2024 injury archive |

The table counts overlap: shot events, matchup rows and summaries are different views of games, not independent observations to sum into a training sample size. Current-season and early historical coverage may be partial.

## Quality and identity

- No duplicate nonnull NBA event, shot or offensive-player/defender keys were found. Shot/matchup identifier columns had no nulls.
- NBA game crosswalk matched 31,126 of 37,986 official NBA games to the baseline ESPN archive. Older and other unmatched games remain explicit; no player-level NBA-to-ESPN crosswalk is fabricated.
- NBA schedules have no missing game/team/start identifiers; 263 lack a venue ID. Event dates are present, but actor/coordinate coverage varies and is recorded per season.
- NFL depth processing removed 3,856 exact duplicates and quarantined 33,859 records with missing/malformed GSIS identities. Different roles and timestamped snapshots remain distinct.
- 98 weekly NFL tracking records and 102,299 historical depth-chart records lack a schedule match. They are preserved with unresolved game dates, not assigned a guessed game or split. Depth charts may contain bye/offseason entries; causes have not been individually reconciled.
- Every newer NFL injury report joins a schedule game, but all 6,760 lack a source modification timestamp. The [upstream issue](https://github.com/nflverse/nflverse-rosters/issues/100) documents that omitted field. File upload times are not substituted.

NBA/odds assets reuse the successful portions of the first context run, with identical collector/shared sources and verified hashes. Their original manifests and retrieval dates remain intact. NFL was rebuilt after adapting to the changed injury schema. Three `*_snapshot_reuse.json` assets identify the reused snapshot.

## Odds and model status

The existing licensed quote sample contains 116 candidates. Deterministic player/game/time checks passed 90; 14 lacked a unique schedule match, 10 lacked a unique player match and 2 failed the pregame timing test. All remain **ineligible for betting-return evaluation** pending rules, prices/payouts, fees and execution checks. Kalshi coverage inventory inspected 6,000 market metadata records across six series; it did not collect historical quotes or enumerate each series completely.

No models were refitted or selected using these context datasets. Development remains through December 31, 2024 UTC and 2025 onward remains held out. Actual defender assignments, shots, tracking results and attendance must never be used to predict the same game. Source timing, revised records, player crosswalks, participation and missingness require review before new features are enabled. Thirty-six synthetic tests passed, and every hosted collection job succeeded.
