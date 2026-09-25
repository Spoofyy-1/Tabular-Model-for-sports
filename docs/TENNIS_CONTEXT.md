# Tennis place and historical weather research

`extras/tennis_context.py` collects additional context only on a GitHub-hosted Actions runner. It does not train a model or download real source rows to the owner's machine.

```bash
python extras/tennis_context.py
# Optional smaller cloud run:
python extras/tennis_context.py --start-year 2020 --weather-places 4
```

Outputs are in `data/extras/tennis_context`; `summary.json` contains aggregate coverage and source failures. The main tables are gzip-compressed CSV with `\N` nulls, accompanying schema JSON, source URLs, retrieval times and response SHA256 hashes. Wikidata entity identifiers remain strings; weather dates are explicitly UTC.

| Folder | Context | License and restrictions |
|---|---|---|
| `wikidata_cc0` | Tennis-associated entity/venue labels and aliases, coordinates, elevation plus original unit, country, capacity when provided; query and quality flags | Wikidata structured data CC0 |
| `weather_noncommercial_research/{year}` | ERA5 daily minimum/maximum/mean temperature, humidity, precipitation, wind speed/direction, radiation and surface pressure; requested and grid coordinates/elevation | Open-Meteo weather data CC BY 4.0; free API service is limited to noncommercial use |

The place query requests at most 3,000 relation rows, keeps invalid geometry for audit, and uses only valid Earth coordinates for weather. If Wikidata's public SPARQL endpoint fails, a bounded MediaWiki search collects up to 50 tennis-tagged entities with direct coordinates; this fallback does not fabricate tournament-to-venue links. Both methods can include tennis facilities and other tennis-associated entities, rather than a certified stadium-only registry. Missing elevation, capacity and other properties remain missing. Wikidata's current snapshot does not establish an entity's historical location, venue capacity or tournament assignment.

Default weather coverage starts in 2015 and ends seven days before execution, allowing for ERA5's publication delay. Collection selects at most twelve distinct place/coordinate pairs using Wikidata sitelink counts as a collection priority, not an outcome/performance criterion. Dates before 2025 are marked development; dates from 2025 onward are separately marked holdout. Files are partitioned by calendar year. The scope is bounded by a 500 MB total response budget and a conservative 4,500 weighted weather API-call estimate per execution. Weather requests are not automatically retried; individual failures are recorded while successfully collected tables are retained.

ERA5 is retrospective reanalysis at approximately 25 km grid spacing. Its daily UTC aggregates can contain weather occurring after a match, and they do not measure indoor court conditions, roof status or a player's exposure. They are not forecasts that were available before an event. Every weather record marks `verified_asof=false`, `is_historical_forecast=false`, and `automatic_training_join_allowed=false`. No match-to-venue or player-travel joins are performed. Such joins need verified historical venues and match start times first.

Primary references: [Wikidata access documentation](https://www.wikidata.org/wiki/Wikidata:Data_access), [Wikidata licensing](https://www.wikidata.org/wiki/Wikidata:Licensing), [Open-Meteo historical weather documentation and ERA5 attribution](https://open-meteo.com/en/docs/historical-weather-api), and [Open-Meteo API service terms](https://open-meteo.com/en/terms).

Synthetic verification: `python -m unittest discover -s extras -p 'test_tennis_context.py'`. Tests cover identifiers, elevation units, invalid coordinates, duplicate geometry, UTC dates and development/holdout separation, missing values, incomplete weather, and rejection of local collection before network or file writes.
