# NFL weather units, observation timing and attendance sources

Audit date: 2026-09-25. This is a source-contract review, not a dataset validation. Inspection was limited to primary documentation, source code, repository/release metadata and existing local project code. No sports dataset assets, API game responses or Parquet footers were downloaded or processed locally. No collector, workflow, model or existing release was changed.

**Decision:** keep the exact `nfldata` schedule temperature unit and weather observation time unverified. Wind is documented in miles per hour. No complete, clearly licensed game-level NFL attendance/capacity history was established. Wikidata provides an explicitly CC0 route for a bounded candidate collection, subject to a separate coverage and identity audit.

## Exact weather contract

The [nflreadr schedule dictionary source](https://raw.githubusercontent.com/nflverse/nflreadr/main/data-raw/dictionary_schedules.csv) defines `temp` as stadium temperature applicable to outdoor/open-roof games. It explicitly defines `wind` in miles per hour, with the same roof applicability. It does **not** specify Fahrenheit/Celsius, weather observation timestamps, measuring instruments, weather-station identity, averaging periods or historical publication times. `gametime` is kickoff time in Eastern time; it is not a weather-observation timestamp. The dictionary does not establish a game-level attendance or capacity field.

The originating [nfldata Games documentation](https://github.com/nflverse/nfldata/blob/master/DATASETS.md#games) provides the same temperature/wind descriptions. Its roof definitions distinguish an outdoor stadium, an open retractable roof, a closed retractable roof and a dome. Those descriptions support separate exposure categories, but do not turn a missing indoor weather value into zero. The documentation also separates the NFL season label from the game's actual calendar year and identifies nominal home teams at neutral sites. This file still contains older availability/schema descriptions; it is useful for stated semantics, not proof of current completeness.

The [nflreadr loader](https://github.com/nflverse/nflreadr/blob/main/R/load_schedules.R) reads the nfldata games object and checks accepted roof values. It does not document a temperature unit conversion or attach a weather-observation clock. The [nfldata publication workflow](https://github.com/nflverse/nfldata/blob/master/.github/workflows/release_games.yml) reads the repository's `games.rds`, upserts the existing release by `game_id`, and republishes formats when content differs. A repository update, release timestamp or retrieval timestamp therefore records a data operation, not the time a stadium thermometer was read. No transformation establishing the missing temperature-unit or observation-time contract was found in this inspected path.

Consequences for the existing weather release:

- Preserve the original temperature field. Keep `temperature_unit_independently_verified=False` and the explicit assumption wording in any Fahrenheit conversion or bucket. The hypothetical arithmetic `(F - 32) * 5 / 9` is valid; its applicability to this exact source field remains unverified.
- Wind conversion `mph * 1.609344` has a documented source unit. Preserve missing/invalid values and distinguish open-air games from closed roofs, domes and unknown retractable-roof states.
- Describe weather as retrospective source-reported game context. Do not call it kickoff weather, an in-game average, a pregame forecast or a historical feature known before betting.
- Keep fit/development boundaries based on game dates, not NFL season labels. January postseason games are an obvious reason those differ.

The NFL Big Data Bowl temperature schema and other NFL products do not establish the contract for this `nfldata` field. Similarly, nflscraPy's separately documented Fahrenheit field does not establish that its extraction and nfldata's schedule are identical. Value ranges or a few plausible games would not be sufficient unit provenance.

## Attendance and capacity candidates

| Candidate | What the inspected source establishes | Reuse/coverage decision |
|---|---|---|
| Existing nfldata/nflreadr schedule | Game IDs, teams, venue, roof, temperature and wind; no documented attendance/capacity contract | Reuse for the weather context already collected; do not invent crowd counts |
| nflscraPy Metadata releases | Producer documentation describes event attendance, weather and boxscore-link keys, with historical downloads advertised from 2000 | Technically relevant; underlying attendance data rights remain uncleared |
| SportsDataverse NFL schedule loader | Loads the combined nflverse schedule release, then filters seasons | No independently documented crowd field or new attendance source |
| Wikidata structured statements | Explicit CC0 data policy; separate attendance and capacity properties | Suitable for a bounded candidate audit; complete NFL coverage and historically valid denominators are not established |
| Official NFL gamebooks | Exact-game reports may contain attendance | Excluded from automatic collection under the restriction already recorded in WEATHER_CROWD_SOURCE_AUDIT.md |

The [nflscraPy producer README](https://github.com/blnkpagelabs/nflscraPy#metadata) describes `attendance` as a recorded event total, identifies `boxscore_stats_link` as the key, and points to its Metadata release. It identifies Pro Football Reference as an underlying source and advertises a 2000-to-present history. This is a producer claim, not a measured completeness audit. Neither attendance's admission-count methodology nor a historically valid stadium-capacity series is established there. Do not treat a ticket/recorded total as measured spectators occupying seats.

The repository's [MIT notice](https://github.com/blnkpagelabs/nflscraPy/blob/master/LICENSE) licenses its software and associated documentation. That alone does not establish the uploader's permission to sublicense all underlying PFR data or authorize a new automated PFR collection. No gamebook/PFR ingestion was implemented, and no Metadata data asset was downloaded to work around this gap.

The [SportsDataverse NFL loader source](https://github.com/sportsdataverse/sportsdataverse-py/blob/main/sportsdataverse/nfl/nfl_loaders.py) confirms that `load_nfl_schedule` reads the combined nflverse schedule. Metadata checks found no `espn_nfl_schedules` release under `sportsdataverse/sportsdataverse-data` at audit time; the [separate sportsdataverse/nfl-data repository metadata](https://api.github.com/repos/sportsdataverse/nfl-data) reported no GitHub-detected license. A null detected license is not proof that no permission can exist, but does not provide the explicit data-reuse grant needed here. The NBA attendance route cannot simply be assumed to exist for NFL.

## A defensible sparse-data route

[Wikidata's licensing policy](https://www.wikidata.org/wiki/Wikidata:Licensing) explicitly releases structured data under CC0. This supports reusing its structured statements with provenance, without scraping restricted gamebooks. It does not establish statement accuracy, completeness, or the semantics of each underlying reference. The present audit inspected property documentation, not NFL event statements; no NFL game coverage count is claimed.

[Attendance P1110](https://www.wikidata.org/wiki/Property:P1110) covers event spectators or ticket holders. The property can also describe an entire tournament or exhibition, so an extraction must establish a single NFL game before treating its value as game attendance. Event date, participants and venue need an exact, independently checked bridge to the project's schedule ID. A season total, championship-series total or ambiguous event must remain unjoined.

[Maximum capacity P1083](https://www.wikidata.org/wiki/Property:P1083) can distinguish statements by sport, point in time, start/end time, part of a venue and other qualifiers. Its semantics also allow different units, including seats and standing room. Preserve statement IDs, quantity units, qualifiers, rank and references. A current preferred capacity must not be projected backward through stadium renovations, changed layouts or temporary restrictions. Even a valid facility capacity is not automatically the capacity applicable to a particular NFL game.

A later hosted pilot should first publish raw structured attendance/capacity candidates and a coverage audit. Preserve conflicting statements rather than choosing the maximum or latest value. Join only a single game with validated participants/date/venue; retain unresolved rows and reasons. Compute an attendance/capacity ratio only when both the count's meaning and the event-specific denominator are supported. Values above one should be flagged, not clipped. Actual crowd noise, allegiance, local density and physiological effects remain unobserved.

## Remaining evidence needed

Temperature certainty requires a maintained nfldata field specification or an identifiable upstream producer transformation that explicitly supplies units for the same schedule column and relevant historical versions. Weather timing requires observation/source timestamps or a documented sampling convention tied to those values; commit times cannot substitute. A broad attendance panel requires a licensed game-level source with identity, counting methodology and coverage evidence, plus a separate historically qualified capacity source if occupancy is desired.

Until then, retain the unverified flags and descriptive-only status. This audit does not establish causal weather/crowd effects, statistically adjusted player sensitivity, or betting profitability.
