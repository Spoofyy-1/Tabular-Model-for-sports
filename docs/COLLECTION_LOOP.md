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
- Team-win matchup profiles, opponent interactions, coaching tendencies measured from earlier games, and team injury-report context. Public staff roles and preparation routines require explicit sources and dates; current roles do not establish historical employment or routine adherence.

Next useful expansions include source-licensed NBA/NFL market activity, officials and stadium/court metadata, historical announced schedules, travel/rest proxies and explicit coverage audits for unusual candidate features. Preserve source rights separately. Statistical significance, causal effects and betting profitability have not been established for these exploratory fields.

## Progress as of the first collection pass

- Published tennis point release: `tennis-points-36173714850-1`, 1,873,115 cleaned points; original source views and excluded-key counts retained.
- Published tennis quote/activity release: `tennis-markets-36173714956-1`, 432,733 observations, eight book snapshots, 240 minute rows. A subsequent run adds supplied transaction references; inspect its aggregate report before selecting the current release.
- Published NBA/NFL quote/activity release: `sports-markets-36174160216-1`, 25,905 observations, ten snapshots, 212 minute rows. NFL requested prop mapping was excluded, not invented.
- Partial context release `extras-36173901119-1` is published. Valid bundles contain 8,968 combine profiles, 12,927 draft records, ten documented tennis spectator associations across three events, 50 Wikidata place records and 29,953 daily ERA5 records across seven places. NBA spectator extraction found no qualifying evidence; NFL rows were audit-only. Missing coverage is not absence.
- The officials collector's legacy NFL mapping and transient HTTP failures were fixed. Successful release `extras-36175530329-1` publishes 68,723 NBA/NFL assignment rows (2015–2026 source seasons). Twenty-eight NFL assignment rows have unmatched game dates and remain flagged. This release also expands celebrity associations to 13 rows across four events; no NBA spectator row has qualified yet.
- Successful release `tennis-notes-36175530428-1` scans the existing MCP archive and adds 5,067 candidate-category mentions across 2,477 matches: medical/timeouts 1,235; equipment 1,178; interruptions 833; weather 712; clock/time violations 423; noise/crowd 351; roof 163; underarm serves 117; light/visibility 55. These are unverified source mentions, not event counts or automatically usable features; CC BY-NC-SA restrictions remain.
- Successful tennis market rerun `tennis-markets-36174160110-1` preserves supplied transaction references in addition to the original activity fields. Transaction references are not guaranteed unique fills.
- New `matchup.yml` jobs collect NBA/NFL team style profiles, injury context and public staff/routine evidence. Check the hosted summary before claiming actual coverage. Training remains paused.
- Published `matchup-36178068271-1` contains valid NBA and injury bundles: 64,334 NBA team/pregame rows, 32,167 NBA game/matchup rows, 17,119 NBA team/report snapshots, 24,253 overlapping NBA cutoff candidates and 9,329 NFL team/game final-report aggregates. NBA style history includes 64,136 quality-screened team games. The NFL style job parsed all seasons but needed duplicate metric reconciliation; rerun only that collector after fixing exact duplicates and quarantining whole conflicting games.
- Published `matchup-36178593411-1` expands staff/routine annotations to 12 records: NBA two, NFL three, tennis seven. Six are staff roles and six describe advice, plans or habits. Role validity intervals remain unknown. Two original NBA pages returned HTTP 403; separate public official conference profiles supplied the NBA records, without bypassing the blocked pages. The ten-row initial staff release is an overlapping earlier version and must not be added to this count.
- Weather was partial: one rate-limit response and four timeouts. Do not immediately replay all twelve places. Reuse successful outputs and honor provider cooldowns; later collection should target missing places or a new bounded source.
- `extras.yml` selects collectors by changed source/test files, so a fix to officials does not redownload all weather/profile sources. Shared packager/workflow changes intentionally revalidate all collectors; avoid unnecessary changes there.
- Source-only synthetic checks passed: 39 tennis, 25 context extras, seven sports-market and 36 Azure tests. These checks do not replace hosted source validation.
