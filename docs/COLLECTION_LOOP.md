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

## Progress as of the first collection pass

- Published tennis point release: `tennis-points-36173714850-1`, 1,873,115 cleaned points; original source views and excluded-key counts retained.
- Published tennis quote/activity release: `tennis-markets-36173714956-1`, 432,733 observations, eight book snapshots, 240 minute rows. A subsequent run adds supplied transaction references; inspect its aggregate report before selecting the current release.
- Published NBA/NFL quote/activity release: `sports-markets-36174160216-1`, 25,905 observations, ten snapshots, 212 minute rows. NFL requested prop mapping was excluded, not invented.
- Partial context release `extras-36173901119-1` is published. Valid bundles contain 8,968 combine profiles, 12,927 draft records, ten documented tennis spectator associations across three events, 50 Wikidata place records and 29,953 daily ERA5 records across seven places. NBA spectator extraction found no qualifying evidence; NFL rows were audit-only. Missing coverage is not absence.
- The officials collector finished NBA 2015–2026 but failed on duplicate NFL legacy schedule keys before packaging. Fix only ambiguous/irrelevant legacy mappings; never arbitrarily select a schedule row. A source-only fix triggers only that collector.
- Weather was partial: one rate-limit response and four timeouts. Do not immediately replay all twelve places. Reuse successful outputs and honor provider cooldowns; later collection should target missing places or a new bounded source.
- `extras.yml` selects collectors by changed source/test files, so a fix to officials does not redownload all weather/profile sources. Shared packager/workflow changes intentionally revalidate all collectors; avoid unnecessary changes there.
- Source-only synthetic checks passed: 39 tennis, 25 context extras, seven sports-market and 36 Azure tests. These checks do not replace hosted source validation.
