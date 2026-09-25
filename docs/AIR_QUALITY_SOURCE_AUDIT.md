# Historical air quality as sports context

Source audit: 2026-09-25. EPA's [AirData reuse policy](https://www.epa.gov/outdoor-air-quality-data/do-i-need-request-permission-use-monitoring-data-and-graphics-airdata) explicitly places its ambient AQS monitoring data in the public domain and permits reuse without a permission request. Follow its [citation guidance](https://www.epa.gov/outdoor-air-quality-data/how-do-i-cite-data-and-graphics-obtained-airdata-website): attribute the US Environmental Protection Agency, Air Quality System Data Mart, the access date and source URL. This source needs no paid subscription or API key for its pre-generated files.

## Bounded collection contract

The [official file catalog](https://aqs.epa.gov/aqsweb/airdata/download_files.html) lists annual ZIPs of daily county AQI, generally around 1.5 MB each in the reviewed years. Request only `https://aqs.epa.gov/aqsweb/airdata/daily_aqi_by_county_YEAR.zip` for 2000–2025. The catalog establishes file availability, not a successful local collection or guaranteed record count. Actual ingestion and CSV generation run only on GitHub-hosted Actions.

Keep the source representation and a normalized CSV.gz partition for each successfully validated year. Preserve AQS geographic codes as strings, source place names, calendar date, AQI, source category, defining pollutant/site and reporting-site count when available. Record exact source headers, null conventions, retrieval time, HTTP modification metadata, byte counts and hashes. Treat a source calendar date as a date; do not invent a UTC observation or publication time.

The collection is bounded at 100 MB total input, 60 HTTP attempts including redirects, 600 seconds, 5 MB per ZIP and 60 MB per decoded CSV. Stop without retry on a source/schema error and publish completed coverage plus failure status. Reject unsafe/ambiguous ZIP members and schemas. Retain raw evidence for invalid rows and duplicate county/date groups; conflicting observations cannot be resolved by selecting the first row. Only explicitly generated files enter the release.

## Meaning and limits

EPA's [AQI methodology page](https://aqs.epa.gov/aqsweb/documents/aqi/aqi_data.html) describes county AQI as the largest eligible monitor AQI for that county and day. It is a unitless regional index, not a direct pollutant concentration or a measurement at a court or stadium. It cannot identify wildfire smoke as the cause of poor air quality. Missing reporting days or unmonitored counties stay missing, not zero or clean-air observations.

The [file documentation](https://aqs.epa.gov/aqsweb/airdata/FileFormats.html) distinguishes monitors, pollutants, sampling durations and multiple summary rules. Monitor-level daily summaries and county-level AQI are different tables. Preserve source categories instead of recomputing concentrations from AQI. Values above 500 must not be clipped automatically.

EPA [updated particulate AQI breakpoints in 2024](https://www.epa.gov/system/files/documents/2024-02/pm-naaqs-air-quality-index-fact-sheet.pdf). A current download of historical observations does not prove which values or category definitions were published before a past game. Keep historical availability and per-record calculation-version status unknown; do not claim a reconstructed betting-time information set or uniform historical pollutant conversion.

These observations could support a later test of environmental context, particularly for outdoor events, after a historically valid venue-to-reporting-area mapping and date alignment are verified. Indoor ventilation, roof operation, time outdoors and athlete exposure remain unknown. No automatic game/player joins, causal claims, betting edge or model fitting are part of this collection. Pre-2024 rows, calendar 2024 and calendar 2025 are separate fit-candidate, calibration-candidate and holdout partitions; those labels do not make retrospective observations pregame-available.

## Publication status

Collector implementation and hosted validation are pending. Record actual completed years, unique county/day rows, invalid/conflicting groups, source transfer totals and asset hashes after the hosted run. Do not substitute catalog estimates for collected coverage.
