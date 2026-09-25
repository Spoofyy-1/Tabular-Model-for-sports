# NFL play context in CSV

The enrichment collector adds play-level information beyond the existing player
box scores, NGS weekly summaries, depth charts and injury reports. It runs only
on GitHub-hosted Actions. Source datasets, processing and outputs never run on
the owner's workstation; code, documentation and aggregate reports may be local.

```sh
python enrichment/nfl_plays.py --start-season 1999 --end-season 2026
```

The output is `data/enrichment/nfl_plays`. Each table is partitioned by season
with `data.csv.gz` and `schema.json`; this is ordinary UTF-8 CSV compressed with
gzip. The aggregate `enrichment_summary.json` and detailed `manifest.json`
record coverage, source downloads, checksums, exclusions, licenses and changes.
All raw source files are removed from the runner after their season is processed.

| Table | Requested coverage | Added context | License |
|---|---|---|---|
| `pbp` | 1999–2026 | Down, distance, field position, game clock, score margin, pass/rush/scramble/sack context, air yards, explicit player IDs | CC-BY-4.0 |
| `player_play_roles` | 1999–2026 | Explicit passer, rusher and targeted receiver by game/play/GSIS ID | CC-BY-4.0 |
| `participation` | 2016 onward, where released | Formation, personnel, defenders in box, on-field players, primary receiver route, pressure, coverage and throw timing where present | CC-BY-SA-4.0 |
| `ftn_charting` | 2022 onward, where released | Motion, play action, screens, RPO, blitz/pass-rusher counts, catchability, contested catches, drops, QB location and progression read | CC-BY-SA-4.0 |

Only selected PBP columns are read into memory; all published participation and
FTN charting fields are retained. Source-era gaps remain null. Participation
before 2023 is NFL NextGenStats; from 2023 it is FTN Data. The latter is only
released after the complete season and postseason have finished, so even lagging
it one game does not make it historically available during that season.

FTN charting is described as charted within 48 hours after games. This is a
general source schedule, not evidence of a specific historical publication time.
Its `date_pulled` records upstream retrieval and is preserved separately from our
download time. Current revised snapshots cannot prove earlier availability.
Every field schema and table has `verified_asof=false`, and every normalized
row has `pregame_feature_enabled=false`. No training changes are made.

Calendar splits use actual UTC game dates: development before January 1, 2025;
holdout from that date onward. January 2025 playoff games in the 2024 NFL season
remain holdout. Date-only kickoff placeholders are flagged. Source game IDs
are retained without replacing embedded historical team codes. Franchise aliases
are normalized only in separate team columns with source values retained.

Missing or malformed game/play identities and conflicting duplicate keys are
quarantined in separate CSV partitions. Exact duplicates are removed. Event actor
GSIS identifiers are validated; malformed source actor IDs remain in the PBP
table and are counted, but do not enter the player-role table. Participation
lists are preserved; they do not identify a receiver's assigned defender. The
route field describes the primary receiver, not every receiver on the field.

CSV consumers must read identifier columns as strings and use the schema:

- Nulls use the literal sentinel `\N`; empty strings and the text `NA` are preserved.
- Set `keep_default_na=False, na_values=[r"\N"]` in pandas and set ID column dtypes to `string`.
- UTC timestamps include numeric offsets. Booleans are `True` or `False`.
- Nested lists/dictionaries are JSON strings; source delimited player lists remain unchanged.
- The CSV has headers and standard quoting. Decompress with ordinary gzip tools.

PBP includes timeouts, penalties and other administrative/no-play events. A count
of all rows is not an offensive snap denominator. Use the retained event-type,
no-play and attempt fields. FTN `read_thrown` primary-read coding changes in
2023; do not convert missing 2022 values into a different read category.
Participation's legacy `ngs_air_yards` is null from 2024 onward; the PBP `air_yards`
column is a separate documented measurement.

The collector processes one source-season file at a time. Download limits are
64 MiB per asset and 1 GiB total; outputs are capped at 512 MiB. The current
release inventory suggests roughly 0.5 GiB of compressed upstream downloads for
the complete requested PBP history, plus about 35 MiB of participation and a few
MiB of charting. Missing current-season participation is expected and reported.

Sources and redistribution requirements:

- [PBP loader](https://nflreadr.nflverse.com/reference/load_pbp.html),
  [PBP dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html),
  [nflverse-data license](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md).
- [Participation loader and explicit licensing](https://nflreadr.nflverse.com/reference/load_participation.html),
  [participation dictionary](https://nflreadr.nflverse.com/articles/dictionary_participation.html).
- [FTN charting loader and explicit licensing](https://nflreadr.nflverse.com/reference/load_ftn_charting.html),
  [FTN dictionary](https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html).
- [CC-BY-SA-4.0](https://creativecommons.org/licenses/by-sa/4.0/legalcode.en):
  participation and FTN charting adaptations retain this license. Credit **FTN
  Data via nflverse**, and **NFL NextGenStats via nflverse** for participation
  through 2022. Source/adapted tables remain separated from the PBP tables.
  Redistribution must retain attribution, license and changes notices; do not
  relabel these derivatives as proprietary or CC-BY-only data.
- [nflverse terms](https://nflverse.nflverse.com/#terms-of-use): underlying
  source-owner terms continue to apply, and data are supplied without warranty.

The output includes both license texts and an attribution notice. It does not
claim betting profitability or contain forecasts, model weights or trade orders.

Local verification, restricted to tiny synthetic in-memory examples:

```sh
python enrichment/nfl_plays.py --self-test
```
