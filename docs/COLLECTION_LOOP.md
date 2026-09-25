# Two-hour contextual data collection

The owner requested data collection only on September 25, 2026 and deferred Azure login/training. No GPU job has been started. The prepared Azure source is retained for later use.

The task heartbeat `two-hour-sports-data-collection` runs every 15 minutes, eight times, with an explicit work deadline of **2026-09-25 20:13 UTC / 13:13 America/Los_Angeles**. At or after the deadline, start no new collectors; summarize published coverage and pause the heartbeat. Check active GitHub runs before launching more work. Do not repeat successful large downloads simply to refresh counts.

All actual source processing stays on GitHub-hosted runners; datasets are CSV or CSV.gz release assets. The local checkout contains source, documentation and synthetic tests only. Inspect only remote aggregate summaries/log diagnostics locally. No local CSV download, model fitting, trade placement, paid subscription or Azure work is authorized in this collection window.

Current tracks:

- Tennis point sequences, serve outcomes, pre-point score states, break points, tiebreaks and early-deficit descriptive summaries. MCP data are CC BY-NC-SA 4.0 and remain noncommercial research only.
- PMXT historical tennis quote snapshots, spread/depth and observed activity, kept separate from point data. An actual point clock is still required for execution research; scheduled start times cannot substitute for it.
- Public celebrity-presence reports tied to a documented game/match or explicitly less precise event. Keep article URLs, publication time/precision and evidence quality. Missing coverage never means a celebrity was absent. Do not publish article bodies or images.
- Tennis venue/court geography and unusual context such as altitude and observed weather. Revised geography, postgame weather and retrospective attendance are not automatically pregame observations.
- NFL combine physical measurements, college and draft history. Career totals from current source snapshots stay isolated from candidate pre-career features.

Next useful expansions include source-licensed NBA/NFL market activity, officials and stadium/court metadata, historical announced schedules, travel/rest proxies and explicit coverage audits for unusual candidate features. Preserve source rights separately. Statistical significance, causal effects and betting profitability have not been established for these exploratory fields.
