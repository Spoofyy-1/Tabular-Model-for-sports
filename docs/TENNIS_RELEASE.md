# Published tennis and market research

All row-level data were processed in GitHub cloud. The local workstation contains only code, documentation and synthetic tests.

## Tennis point CSVs

[Release tennis-points-36173714850-1](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/tennis-points-36173714850-1) was built successfully from the pinned Match Charting Project source. It contains **1,873,115 cleaned point rows across 11,775 retained match histories**, alongside the original source views and quarantine counts. The inventory counted 11,785 source match identities before ten ambiguous histories were excluded. Of cleaned rows, 1,869,461 passed score-prefix validation.

| Group | Cleaned point rows | Retained matches |
| --- | ---: | ---: |
| Men's charted singles | 1,280,408 | 7,524 |
| Women's charted singles | 592,707 | 4,251 |

The archive contains source serve strings, scores, server identity, point winners, reconstructed pressure context, early-deficit labels, and descriptive summaries for 1,561 players. A total of 398 players met the declared break-point comparison screen. These summaries use pre-2025 charted matches, with conservative exclusion of date-only boundary observations. Newer points remain in separately marked evaluation periods.

**This source is CC BY-NC-SA 4.0 and remains noncommercial research only.** Volunteer charting is selective; player comparisons are not representative rankings of current ability or proven causal clutch effects. The source raw files omit spreadsheet-derived first-serve-in/ace/double-fault convenience columns, so those labels remain null. Original serve strings are preserved for later verified parsing. There are no reliable wall-clock point timestamps. See [source/schema details](TENNIS_DATA.md).

## Historical tennis market pilot

[Release tennis-markets-36173714956-1](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/tennis-markets-36173714956-1) contains **432,733 normalized PMXT observations** for two verified April 17, 2026 match-winner markets: Fils–Musetti and Swiatek–Andreeva. One complete archive hour, 14:00–15:00 UTC, was processed. The second requested hour was excluded by the 500 MB transfer budget.

The observations comprise 431,966 price changes, 759 last-trade-price messages and eight full book snapshots. All eight snapshots have two-sided uncrossed books. The archive also provides 240 per-token minute summaries of observed activity, available no earlier than each minute's end. A later export preserves transaction references when supplied; a transaction hash is not assumed to identify a unique trade message.

Most observations are **updates, not independent price offers or unique trades**. Trade-message sizes are not a complete venue trading-volume measure. No historical market suspension state, complete fee/delay history or point-clock linkage is established. This is useful market microstructure data, not an executed after-serve strategy. PMXT derivatives retain their CC BY 4.0 attribution and are separate from the noncommercial point dataset. [Market rules and timing audit](TENNIS_MARKETS.md).

## NBA/NFL historical market pilot

[Release sports-markets-36174160216-1](https://github.com/Spoofyy-1/Tabular-Model-for-sports/releases/tag/sports-markets-36174160216-1) adds **25,905 observations**, ten full snapshots and 212 minute summaries before reviewed scheduled starts:

- NBA Charlotte–Orlando, April 17, 2026: 25,457 observations across one full-game moneyline and two exact points contracts (LaMelo Ball 22.5 and Paolo Banchero 22.5). All eight NBA book snapshots in the source hour were at or after the cutoff and were excluded.
- NFL New England–Seattle, September 9 local / September 10 UTC, 2026: 448 observations and ten snapshots for the verified moneyline. The requested Drake Maye prop did not pass the strict contract mapping and is not in the collected prop dataset.

Both source and collector-receipt timestamps must precede the reviewed schedule cutoff. The schedule is not proof of the actual historical start time, and today's rules metadata is not a verified historical rule version. No automatic player-stat joins, betting returns or executable fills are claimed.

The fixed pilots establish usable CSV ingestion and identity/coverage audits. They are small event samples, not complete 2023-onward line coverage. Direct Kalshi raw-data collection/model use remains outside this pipeline because its reviewed current data terms restrict redistribution and machine-learning uses.
