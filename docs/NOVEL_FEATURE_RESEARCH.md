# Sixty unusual sports-feature hypotheses

Research date: 2026-09-25. Status: **ideas and source leads, not newly collected observations or tested effects**. These extend the existing weather, injury, crowd, coaching and player-stat work. NBA and NFL research was independently delegated; tennis and the combined testing design were reviewed by the main agent.

The question for every row is whether adding the specified input improves a defined prediction beyond a strong baseline. Direction is deliberately unspecified. A plausible story does not establish an effect, causality, or profitable execution. These are 60 sport-specific candidates; a few transfer shared mechanisms such as glare, officiating and schedule changes across sports.

## Reading the catalog

- **D — derive:** candidate calculation from structured records, subject to coverage and historical publication checks. This does not mean the feature is already built.
- **A — annotate:** needs dated public announcements, event calendars, venue documentation or verified factual annotations.
- **V — video/feed:** needs permitted video annotation, tracking, wall-clock timestamps or a suitable licensed/live feed. Historical coverage is unverified.
- **P — pregame:** may be used only if published before the prediction cutoff. **L — live:** only after the event/input has been observed. Historical publication unknown means retrospective research only, even for a P candidate.

Sources document possible inputs or rules, not the proposed predictive effect. Current rules and venue pages cannot establish historical conditions without dated versions. Raw source redistribution rights must be reviewed separately. Match Charting Project data remain in the noncommercial research partition.

## Tennis: 01–20

| ID | Candidate and exact measurement | First outcome to test | Path / timing | Source lead and limitation |
| --- | --- | --- | --- | --- |
| 01 | Ball-model change: current event's verified brand/model differs from the player's preceding event | First-set serve-point success | A / P | [ITF balls][t-balls] plus dated tournament equipment announcements. Approval is not proof of actual match use. |
| 02 | Ball age: completed games and points since the last confirmed change | Next service-point success | D+A / L | [2024 ATP rules][t-rules] plus point order. Validate event-specific changes, interruptions and replacements; do not assume one schedule universally. |
| 03 | Surface revision: product identifier and days since verified resurfacing | Ace and rally-length distribution | A / P | [ITF surface classification][t-surfaces] plus installation records. Product pace is not measured match-court pace. |
| 04 | Court wear: earlier play minutes on that court, with a separately reviewed bare-area measure where available | Slip/error frequency and point success | D+A or V / P,L | [Wimbledon grounds methods][t-grass]. Scheduled use is a wear proxy; actual maintenance and measured court condition need separate evidence. |
| 05 | Run-off restriction: measured distance behind baseline and beside sideline | Deep-return and defensive-point success | A+V / P | [ATP facilities rules][t-rules] establish geometry categories; obtain actual court plans and player positioning. Minimum specifications are not measurements. |
| 06 | Serve-toss glare: solar angle relative to the server's end and observed toss direction | First-serve faults | A+V / L | [NOAA solar equations][solar] plus reviewed court orientation and timestamps. Include obstructions; angle alone does not prove glare. |
| 07 | Shadow transition: fraction of service box or return path crossing a moving shadow | Return errors | V / L | Venue geometry and permitted video required; [NOAA][solar] supplies solar geometry only. No verified historical shadow table found. |
| 08 | Roof transition: elapsed points/minutes after confirmed closure or opening | Serve/return success after resumption | A+V / L | [Wimbledon roof documentation][t-roof]. A retractable roof's existence does not establish its match-specific operation. |
| 09 | Officiating-system change: live electronic calling, review-only calling or line judges, and prior player exposure | Stoppage rate and next-point success | A / P,L | [ATP officiating rules][t-rules] plus dated event implementation notices. Rules differ by tour/year; rollout is not random. |
| 10 | Public equipment change: first matches after a confirmed racket/string-model change | Serve errors and shot dispersion | A / P | Dated player or manufacturer statements needed. No complete verified registry found; sponsorship announcements do not prove the equipment actually used. |
| 11 | Preceding-match overrun: elapsed delay relative to the latest announced order of play | Opening-set performance | A+V / P | [ATP scheduling framework][t-rules] plus timestamped order-of-play revisions and court completion. A listed “not before” time is not a promised start. |
| 12 | Singles/doubles double duty: completed doubles points before a singles match, with elapsed recovery | First-set movement/serve outcomes | D+A / P | Official draws and orders of play; [ATP scheduling][t-rules]. Separate selection into doubles from extra workload. |
| 13 | Late opponent replacement: hours from public replacement notice to start, with change in opponent style | Opening return-game performance | A / P | [Dated ATP withdrawal example][t-replacement]. A publication date without time gives an interval, not exact notice hours. |
| 14 | Overnight resumption: break duration and frozen score when a suspended match resumes | First completed game after restart | A+V / L | Official suspension/resumption notices plus point feed. Control score and conditions; source match dates alone cannot identify this. |
| 15 | Serve-pattern predictability: smoothed entropy of prior serve directions by side and score | Next return-point success | D / L | [MCP][t-mcp]. Only earlier points; full-match summaries leak future choices. Sparse volunteer coverage and noncommercial terms apply. |
| 16 | Rally bursts: number of long rallies and cumulative shots in the preceding five points | Next-point success | D / L | [Rally definitions][t-rally]. Keep definition fixed; rallies are a workload proxy, not measured exertion. |
| 17 | Backhand targeting: share of previous opponent shots directed to the player's backhand | Next rally outcome | D / L | [MCP][t-mcp]. Requires auditable shot parsing and handedness; do not infer intent or injury. |
| 18 | Net-pattern adaptation: opponent's recent approach frequency versus the player's historical passing-shot profile | Next approach-point outcome | D / L | [MCP][t-mcp]. Use completed points only; do not condition a pre-point prediction on a future approach. |
| 19 | Failed serve-out recovery: earlier failure to close a set on serve, conditional on exact current score | Next service-game outcome | D / L | Point-score records, including [MCP][t-mcp]. Compare equivalent score/strength states; this is not a personality label. |
| 20 | Aborted tosses: caught/restarted tosses over earlier service points | Next serve fault probability | V / L | Permitted video or detailed live annotations required. Standard point tables do not guarantee this field; no complete archive verified. |

## NBA: 21–40

| ID | Candidate and exact measurement | First outcome to test | Path / timing | Source lead and limitation |
| --- | --- | --- | --- | --- |
| 21 | Arena conversion pressure: preceding hockey/concert event type and scheduled turnaround hours | First-quarter shooting and turnovers | A / P | [Arena calendar][n-calendar]. Event spacing does not measure floor temperature, traction or actual conversion duration. |
| 22 | Alternate floor: court-design identifier | Shooting errors | A / P | [2024 Cup courts][n-cup-floor]. Control tournament stakes; no traction inference. |
| 23 | Uniform contrast: announced outfit colors' measured similarity to opponent and floor | Live-ball turnovers | A / P | [LockerVision][n-uniform]. Verify historical outfit revisions and image rights; postgame images are not a pregame archive. |
| 24 | Ball supplier transition: games since implementation, interacted with prior shot mix | Shooting residuals | D+A / P | [Wilson introduction][n-ball]. Announcement describes unchanged specifications; physical changes are unproven and season confounding is strong. |
| 25 | Basket-background asymmetry: attacking end relative to fixed visual features | Free throws and jump shots | A+V / L | [NBA arena example][n-background] plus historical plans and direction logs. Distinguish backdrop from crowd count and player selection. |
| 26 | Lighting renovation: first games after documented commissioning | Shooting residuals | A / P | [NBA arena records][n-light]. Year-only renovation dates cannot identify a precise first game; other renovations may coincide. |
| 27 | Broadcast break regime: broadcast designation and historically applicable stoppage allowances | Starter minutes and post-break efficiency | D+A / P,L | [NBA timing rules][n-time]. Verify season edition; nationally televised games are selected nonrandomly. |
| 28 | Referee/player interaction: assigned crew's lagged foul profile crossed with player's lagged contact profile | Free throws and foul trouble | D+A / P | [Official assignments][n-refs]. Timestamp assignments and shrink small samples; crew selection can confound observed rates. |
| 29 | Recording-pattern residual: venue's historical assist-credit frequency after controlling passes, shots and players | Recorded assists | D+V / P | [Primary scorekeeper research][n-scoring]. Precise pass controls may require optical tracking. A measurement process hypothesis, not an accusation or direct measure of player ability. |
| 30 | Bench cooling interval: real elapsed time between exit and re-entry, beyond game-clock rest | First two shots after return | V / L | Substitutions plus timestamped play feed; [timing rules][n-time] explain clock differences. Do not fabricate wall time from game-clock gaps. |
| 31 | Airport disruption: relevant public ground-stop/delay-program minutes before tip-off | Opening-quarter performance | A / P | [FAA advisories][n-faa]. An airport notice does not establish actual team delay; no private-flight tracking. |
| 32 | Schedule revision shock: change in date/time and advance notice | Opening-half efficiency | A / P | [NBA dated revision][n-schedule]. Preserve original schedule and cause; cancellation reasons are major confounders. |
| 33 | Ceremony timing: announced pregame versus halftime ceremony; actual duration separately | Following-quarter efficiency | A / P,L | [Advance retirement announcement][n-ceremony]. An announcement establishes a plan, not extra delay or emotional state. |
| 34 | All-Star side-event workload: completed contest attempts before next league game | Early-game shot accuracy | A+V / P | [Official contest coverage][n-contest]. Attempts may need permitted video; participant selection and unusual break length matter. |
| 35 | Awards qualification proximity: qualifying appearances still needed versus games remaining | Participation and minutes | D+A / P | [NBA CBA summary][n-cba]. Use contemporaneous rules, exceptions and prior qualifying games. A threshold does not dictate behavior. |
| 36 | Short-contract window: days since public 10-day signing, first/second deal | Minutes volatility | A / P | [Official signing example][n-contract]. Announcement and effective dates can differ; no private motivation inference. |
| 37 | Discipline threshold: counted technical fouls remaining before automatic suspension | Foul/technical rate and availability | D+A / P | [Historical NBA rules][n-old-rules]. Apply season-specific thresholds and rescissions known at cutoff; final revised totals leak. |
| 38 | Cup scoring incentive: advancement margin scenarios known before play | Late-game pace and starter retention | D+A / P,L | [Historical tournament rules in team guide][n-cup-rules]. Reconstruct rules per edition and completed games only; do not use final group standings. |
| 39 | Second-chance clock regime: reset rule crossed with lagged offensive-rebound style | Second-chance possession length | D+A / P,L | [2018 rule change][n-shotclock]. Treat as a structural regime interaction; simultaneous league trends limit causal interpretation. |
| 40 | G League free-throw transition: recent exposure to a different free-throw format before NBA return | Initial NBA free-throw trips | D+A / P | [G League experiment][n-gleague]. Validate year-specific rules and cross-league IDs; promotion and role changes confound comparisons. |

## NFL: 41–60

| ID | Candidate and exact measurement | First outcome to test | Path / timing | Source lead and limitation |
| --- | --- | --- | --- | --- |
| 41 | Turf system generation: specific product and effective installation date | Rushing/cutting efficiency | A / P | [Packers surface documentation][f-turf]. Broad grass/artificial labels do not identify the installed system or its condition. |
| 42 | Sod age: days since confirmed replacement, including replaced zone | Rushing and kicking outcomes | A / P | [Chiefs resodding][f-sod]. Article date can bound installation; do not invent completion dates. |
| 43 | Field-use load: confirmed on-field concerts/soccer since maintenance | Footing-related errors | A / P | [Stadium operations account][f-operations]. Count field exposure, not every event at the venue; distinct from indoor basketball conversion. |
| 44 | End-zone sun alignment: sun angle relative to receiving/kicking direction | Deep-pass, punt and kick outcomes | A+V / L | [NOAA equations][solar] plus field plans and direction. Obstructions and actual exposure require review. |
| 45 | Playcaller location: booth/sideline and games since confirmed move | Play execution and delay penalties | A / P | [Bills discussion of booth experiment][f-booth]. Considering a move is not adopting it; changes often follow poor performance. |
| 46 | Exact-stadium familiarity: prior appearances at the physical venue, recency weighted | Specialist and opening-drive performance | D / P | [Schedule schema][f-schedule] plus player participation. Renaming cannot reset venue history; acknowledge incomplete earlier careers. |
| 47 | Coordinator opponent familiarity: prior employment, years since departure and current personnel overlap | Opening-drive and third-down outcomes | A / P | [Official coaching biography example][f-coach]. Reconstruct dated roles; a current biography alone is not proof of historical publication. |
| 48 | Offensive sequence predictability: lagged run/pass sequence probabilities conditional on down, distance and score | Next-play efficiency | D / P,L | [FTN charting schema][f-chart]. Ordering is distinct from overall pass rate; shrink sparse sequences and verify publication timing. |
| 49 | Flex notice: hours between public schedule change and kickoff, and size of change | Opening-half execution | A / P | [2024 NFL schedule procedures][f-facts]. Preserve initial schedule and selection context; notice is not measured preparation disruption. |
| 50 | Hash-mark asymmetry: prior offensive/defensive performance by starting hash and play direction | Pass efficiency by field side | D / P,L | [FTN starting-hash field][f-chart]. Pregame uses earlier tendencies; current-play hash only after observed. Coverage is era-dependent. |
| 51 | Number/position unfamiliarity: announced defender number-position combinations relative to player's earlier exposure | Identification errors and protection failures | A+V / P | [NFL number guidance][f-numbers] and rosters. Use historical rules; observed errors require separate annotation. |
| 52 | Crew-specific penalty mix: lagged holding/contact/pre-snap rates per opportunity | Penalties versus matchup style | D+A / P | [Officials contract][f-officials]. Historical assignment availability and team-selection confounding must be controlled. |
| 53 | Eligible-lineman novelty: previously observed declared-eligible packages and personnel turnover | Coverage assignment errors | A+V / P,L | [Historical eligibility rules][f-old-rules]. Extra linemen do not prove eligibility declarations; reliable annotations are required. |
| 54 | Replay regime: effective replay-assist scope and implementation dates | Reversals and recorded play outcomes | A / P | [NFL technology history][f-replay]. Primarily a recording/rule regime control, not a standalone team advantage. |
| 55 | Helmet-model transition: games since publicly confirmed equipment change | Execution/error rates | A / P | [2024 eligibility/testing poster][f-helmet] plus explicit player/team confirmation. An approved model is not proof it was worn; no private medical inference. |
| 56 | Punter-foot novelty: documented kicking foot crossed with returner's earlier exposure | Muff and fair-catch rates | D+A / P | [Official punter biography example][f-punter]. Unknown foot stays missing; separate punter quality from rotation familiarity. |
| 57 | Unit coordination: prior shared snaps for center–quarterback and offensive-line combinations | Exchanges, pressures and penalties | D / P | [Participation documentation][f-participation]. FTN participation from 2023 is released after postseason; lagging games does not itself solve availability leakage. |
| 58 | Short-notice integration: days since announced acquisition and prior games with team | Usage and assignment errors | A / P | [Official transactions][f-transactions]. Needs announcement timestamps; moves reflect player quality and team needs. |
| 59 | Joint-practice familiarity: confirmed sessions with this opponent before the game | Early-game recognition/execution | A / P | [Dated joint-practice notice][f-practice]. Distinguish scheduled from completed sessions; preseason game plans and selection differ. |
| 60 | Field infrastructure: historical drainage/heating/root-zone configuration | Field response conditional on weather | A / P | [Documented venue rebuild][f-turf]. Installed capability is not day-specific operation or measured turf temperature. |

## Initial test order and admission rules

Start with 02, 15, 16, 19, 27, 35, 36, 38, 48 and 56 after source/coverage checks. These have concrete definitions or plausible structured inputs. Keep 07, 10, 20, 29, 30 and 55 in the expensive acquisition queue until an adequate historical archive is demonstrated. Novelty alone is not a priority score.

NFL source timing needs a table-specific check: [FTN charting documentation](https://nflreadr.nflverse.com/reference/load_ftn_charting.html) describes charting within 48 hours after games, whereas the separate participation release is season-end from 2023 onward. Neither statement replaces historical publication/revision evidence for a specific record.

1. Define the prediction instant and outcome before extracting features. Pregame and next-point/next-play models are separate experiments. Every input needs event time, publication/observation time, source version, missingness and an auditable entity join. Future-match outcomes and full-match aggregates cannot predict earlier decisions.
2. Use games before 2024 for fitting and rolling temporal validation within that period; use calendar 2024 for the declared calibration/selection step. Lock 2025 onward as holdout. Count every alternative window, interaction and subgroup as part of the experiment family. A feature introduced only in 2024 has no pre-2024 fit history: keep it descriptive or preregister a separate future study instead of quietly changing the split.
3. Compare a baseline containing player/team strength, opponent, role/minutes expectations, venue, season, score where relevant, and established schedule/injury variables against the same model plus one feature family. Use game/match-level grouped splits and uncertainty; points from one match must not appear on both sides of a fold. Check robustness across seasons, players and venues.
4. Evaluate held-out probability quality with log loss/Brier score and calibration; evaluate stat distributions with appropriate likelihood/interval coverage. Report effect sizes and uncertainty. Account for multiple comparisons with a preregistered family-level procedure; include shuffled/placebo features to detect leakage and spurious discovery. A nonsignificant result is not proof of no effect.
5. Only after predictive improvement survives independent evaluation should a separate execution study consider quoted prices, spreads, fees, executable depth, fill rules, latency, voids and settlement definitions. Point order without wall-clock timestamps cannot establish a tradable post-serve entry. Recorded market-message counts are not completed trade volume.
6. All actual source ingestion, feature CSV generation and experiments run on GitHub-hosted runners or separately authorized cloud resources. This document is research planning metadata only. No sports observations were downloaded locally for this catalog, and no training or bets were run.

## Source references

[t-balls]: https://www.itftennis.com/en/about-us/tennis-tech/approved-balls/
[t-rules]: https://www.itftennis.com/media/11845/2024-rulebook-atp-update.pdf?embed=true
[t-surfaces]: https://www.itftennis.com/en/about-us/tennis-tech/classified-surfaces/
[t-grass]: https://www.wimbledon.com/pdf/6.%20Media%20Fact%20Sheet%20-%20Grounds%202023.pdf
[t-roof]: https://www.wimbledon.com/pdf/2020_Compendium.pdf
[t-replacement]: https://www.atptour.com/en/news/berrettini-australian-open-2024-withdrawal
[t-mcp]: https://github.com/JeffSackmann/tennis_MatchChartingProject
[t-rally]: https://www.tennisabstract.com/blog/2019/08/17/match-charting-project-rally-stats-glossary/
[solar]: https://www.gml.noaa.gov/grad/solcalc/solareqns.PDF
[n-calendar]: https://www.cryptoarena.com/events/all
[n-cup-floor]: https://www.nba.com/news/emirates-nba-cup-2024-courts-unveiled-official-release
[n-uniform]: https://lockervision.nba.com/
[n-ball]: https://pr.nba.com/wilson-reveals-nba-official-game-ball-in-advance-of-2021-21-nba-season/
[n-background]: https://www.nba.com/news/starting-5-oct-24-ot-thriller-in-intuit-dome-opener-risachers-nba-debut-and-morants-return
[n-light]: https://cares.nba.com/nba-arenas/
[n-time]: https://official.nba.com/rule-no-5-scoring-and-timing/
[n-refs]: https://official.nba.com/referee-assignments/
[n-scoring]: https://arxiv.org/abs/1602.08754
[n-faa]: https://www.fly.faa.gov/adv/advAdvisoryForm
[n-schedule]: https://pr.nba.com/nba-game-schedule-adjustments-1-26-24/
[n-ceremony]: https://www.nba.com/news/lakers-to-retire-pau-gasols-no-16-jersey-vs-grizzlies-on-march-7
[n-contest]: https://www.nba.com/news/2024-3-point-contest
[n-cba]: https://cms.nba.com/wp-content/uploads/sites/4/2024/11/2024-25-CBA-101.pdf
[n-contract]: https://www.nba.com/pistons/news/detroit-pistons-sign-chimezie-metu-to-10-day-contract
[n-old-rules]: https://ak-static-int.nba.com/wp-content/uploads/sites/3/2016/11/2016-2017-Rule-Book.pdf
[n-cup-rules]: https://cdn.nba.com/teams/uploads/sites/1610612744/2023/12/2324-gsw-media-guide.pdf
[n-shotclock]: https://www.nba.com/news/nba-board-governors-approves-rule-changes
[n-gleague]: https://official.nba.com/nba-g-league-to-test-experimental-free-throw-rule-for-2019-20-season/
[f-turf]: https://www.packers.com/news/new-turf-ready-to-welcome-packers-into-2018-season
[f-sod]: https://www.chiefs.com/news/chiefs-resod-portions-of-the-field-at-arrowhead-stadium-18423938
[f-operations]: https://www.chiefs.com/news/chiefs-spotlight-stadium-operations-facilities-12939428
[f-booth]: https://www.buffalobills.com/news/top-3-things-to-know-from-day-12-of-2022-bills-training-camp
[f-schedule]: https://nflreadr.nflverse.com/articles/dictionary_schedules.html
[f-coach]: https://www.chiefs.com/team/coaches-roster/steve-spagnuolo
[f-chart]: https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html
[f-facts]: https://static.www.nfl.com/image/upload/league/apps/league-site/media-guides/2024/2024_Record_and_Fact_Book_incl_Supplemental.pdf
[f-numbers]: https://operations.nfl.com/rules-officiating/nfl-football-basics/football-terms
[f-officials]: https://nflreadr.nflverse.com/reference/load_officials.html
[f-old-rules]: https://operations.nfl.com/media/2224/2016-nfl-rulebook.pdf
[f-replay]: https://operations.nfl.com/gameday/technology/technology-and-the-game
[f-helmet]: https://static.www.nfl.com/image/upload/v1712665965/league/yt3aubz9cjxmg3seascg.pdf
[f-punter]: https://www.clevelandbrowns.com/team/players-roster/corey-bojorquez/
[f-participation]: https://nflreadr.nflverse.com/reference/load_participation.html
[f-transactions]: https://www.nfl.com/transactions/league/trades/2024/8
[f-practice]: https://www.patriots.com/news/patriots-announce-updated-time-for-joint-practice-with-the-eagles
