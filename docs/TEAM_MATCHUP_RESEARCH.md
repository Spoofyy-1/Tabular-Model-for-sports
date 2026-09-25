# Team wins, styles, availability and public routines

The collection scope includes team wins alongside player props. Actual data stays on GitHub-hosted runners and in CSV release assets. Training remains paused. Development ends December 31, 2024; 2025 onward remains separate.

## Measurable matchup profiles

An archetype should be a time-varying description of how a team plays, with uncertainty when its roster or coach changes. NBA shooting mix, possession estimates, turnover and rebound shares, and NFL run/pass, shotgun, no-huddle and fourth-down tendencies provide testable dimensions. Pair each team's prior-game offense with its opponent's prior-game defense. Numeric profiles retain more information than an unverified label such as “clutch team.” Future archetype clustering must fit only within the training period, then freeze before validation.

Team box scores do not identify NBA pick-and-roll coverage, switching, zone use, coaching intent or actual defender assignments. Those require separately licensed event/tracking data or carefully dated film annotations. Likewise, offensive results are affected by personnel and opponents and cannot establish a coach's independent effect. The initial collectors distinguish measured team tendencies from staff facts.

[Yamada and Fujii (2024)](https://arxiv.org/abs/2403.13821) study lineup compatibility using shooting-style and offensive-role clusters. This is a useful research precedent for lineup interactions, not evidence that our team profiles forecast betting returns. For NFL, [nflfastR's expected-pass definitions](https://nflfastr.com/reference/add_xpass.html) separate situational expected dropbacks from dropbacks over expectation and flag pre-2006 coverage limits. Raw pass rate should not be interpreted as a pure coaching preference without controlling for game situation.

Relevant definitions and source availability: [NBA stats glossary](https://www.nba.com/stats/help/glossary), [nflverse data update schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html). NFL participation files after 2023 are delivered after the postseason; they must not silently become same-season historical predictors. Revised model-derived measures such as EPA also need model-vintage and historical-availability qualification.

## Injury report context

`matchup/injury_context.py` reuses already-published injury tables: NBA official PDF report entries, historical NFL final reports, and recent NFL reports. No PDF recollection or local data processing is needed. It publishes:

- NBA report/team/game snapshots with source report timestamps, exact team-name mapping, reported status counts, unresolved/conflicting identity flags and explicit not-yet-submitted records.
- NBA candidate views 30, 60 and 120 minutes before listed game start. These are overlapping research views based on report headers, not independently verified historical public availability. No candidate is fabricated for a missing report.
- NFL team/game aggregates of final report snapshots, practice-status distributions and missing modification timestamps. These cannot reconstruct earlier versions of a report.

No omitted player is assumed healthy. Counts do not yet weight players by projected minutes, usage, position or replacement strength. NBA player names are not automatically mapped to canonical IDs. Conflicting player identities invalidate aggregate count completeness. Both sports retain `automatic_training_join_allowed=false` until the appropriate identity and availability evidence is obtained.

The eventual availability model should resolve stable player IDs and use pregame expected minutes/snaps, expected rotation or depth role, absence of multiple players at one position, and interactions with the opposing team's strengths. Actual target-game minutes, starters, snaps, final injury revisions and game results cannot be predictors. [NFL injury field definitions](https://nflreadr.nflverse.com/articles/dictionary_injuries.html) and [NBA report guidance](https://official.nba.com/nba-injury-report-2025-26-season/) document the underlying reports.

## Staff and routines

Documented coach, nutritionist, dietitian, physiotherapist and performance staff roles belong in an evidence table with source URL, publication precision, retrieval time, named person, team/player association and explicit tenure evidence when supplied. A current staff page does not establish that person worked there in 2023. An interview describing a routine is evidence of a statement, not confirmation it occurred at every match. Unknown dates and missing routines remain null. Article bodies, images and private personal information are excluded.

Later testing should compare incremental changes in time-separated log loss, calibration and net expected value with fees and execution assumptions. Staff and celebrity variables can proxy for team identity, star players, playoffs, media coverage or venue; selected anecdotes cannot establish causation. Multiple comparisons, rare events and small samples need explicit controls. Collection does not establish any betting edge.

## CSV layout and collection

The **Collect team matchup research CSVs** workflow publishes separate archives for `nba_teams`, `nfl_teams`, `injury_context` and `staff_routines`. It selects only changed collectors on source pushes. All archives carry a summary, schema, source provenance and SHA-256 inventory. Compressed CSV uses UTF-8 and `\N` for null; preserve identifiers as strings.

| Collector | Table grain | Included measurements |
|---|---|---|
| NBA team styles | Team/game plus one home-away comparison per game | Rolling 5/10/20-game shooting mix, free-throw rate, effective FG%, rebound shares, turnover proxy, assist rate, possession proxy and opponent allowances; rest intervals and offense-versus-defense interaction candidates. |
| NFL team styles | Team/game | Rolling 5/10-game pooled-count pass/run, neutral early-down pass, shotgun, no-huddle, deep target, explosive play, red-zone and fourth-down tendencies; yards-per-play/dropback/run and opponent comparisons. |
| Injury context | NBA report/team/game; NFL final report/team/game | Listed status counts, report timing, explicit not-submitted/unknown values, conflicting identity exclusions and available practice-status counts. |
| Staff/routines | Source/person/role or preparation mention | Dated source metadata and separately coded role, advice, plan or reported habit; no assumed match-level adherence. |

`team_game_profiles`/`observed_team_game_metrics` and `team_game_labels`/`win_labels` describe current-game outcomes and belong outside prediction inputs. Only the `pre_*` histories and their opponent comparisons are candidate predictors, subject to historical-availability review. The NBA history resets at each source season. The NFL window can cross seasons, so earlier-coach/earlier-roster influence remains a modeling concern. Both require a prior UTC date and at least a twelve-hour start-time gap; actual completion/publication time remains unverified. NFL ties have an explicit tie result and a null binary win label.

The injury collector also checks upstream NBA quarantined rows. A report affected by an excluded source row cannot supply a complete-count candidate; newer incomplete reports block fallback to older complete reports. Thirty-, sixty- and 120-minute cutoff views overlap and must not be counted as independent games. No rows from these collectors are automatically added to an existing model.
