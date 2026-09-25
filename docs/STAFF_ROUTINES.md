# Public staff and preparation annotations

`matchup/staff_routines.py` is a bounded primary-source collector for publicly documented staff roles and preparation mentions. It is separate from statistical team archetypes. A reported coaching habit is a candidate annotation, not a demonstrated explanation of wins or a predictive effect.

The source catalog deliberately starts small:

| Official source | Extraction scope |
|---|---|
| [Brooklyn staff announcement](https://www.nba.com/nets/news/brooklyn-nets-announce-staff-additions-and-promotions-2024) | A performance-dietitian role in a dated announcement. |
| [Philadelphia staff directory](https://www.nba.com/sixers/team/staff-directory) | A current dietitian listing, observed only when retrieved. |
| [Denver nutrition seminar](https://www.denverbroncos.com/news/rookie-seminar-nutrition-with-bryan-snyder-17186893) | Nutrition director role; pregame meal and in-game snack advice. No dosage, medical-effect or adherence claims. |
| [ATP final preparation interview](https://www.atptour.com/en/news/michael-russell-us-open-2024-final-preview) | Coach role, planned opponent video/analytics review and light practice. |
| [ATP team-routine interview](https://www.atptour.com/en/news/paul-miami-2024-feature) | Fitness-coach/physiotherapist roles, reported team breakfast and warm-up habits. |

The dated sources include a 2016 NFL seminar and 2024 NBA/ATP reporting. These are illustrative historical mentions, not comprehensive 2023–2025 coverage. The current directory cannot fill historical staff gaps. The collector captures names only when the configured source contains an explicit relationship pattern; it does not seed fact rows from this research or infer a nutritionist from a sponsorship.

Publication date, publication time with timezone, modification date, retrieval time, observed listing date, and employment validity are separate fields. A date without timezone never becomes a made-up UTC timestamp. Article publication is not appointment, departure, or routine observation: all staff-validity intervals remain null in this first catalog. Reported advice, plans and habits have distinct assertion codes, and none is an observation that an athlete followed a routine during a specific match.

The output excludes article bodies, quotes, medical diagnoses, private details and pictures. It preserves source URLs, source-content hashes and evidence-block hashes. No open license was identified for the articles; the deliverable is a small set of normalized factual annotations with attribution, not a licensed article corpus. Commercial reuse is not cleared. Fetch failures, changed layouts and access restrictions produce audit records without bypasses.

All rows have `automatic_training_join_allowed=false`, `historical_publication_verified=false`, `identity_independently_verified=false`, `match_adherence_observed=false`, and `performance_effect_established=false`. Historical as-of snapshots, canonical identities, exact game associations, and valid staff intervals need separate review before model use. Even after that review, team quality, injuries, schedule difficulty, selection into media coverage and reporting after a win can confound associations. These annotations must not turn into retrospective labels such as “winning nutritionist” or “clutch coach.”

## Hosted execution

Dependencies: Python 3.11+, `requests`, and `beautifulsoup4`. Real ingestion is blocked outside genuine GitHub-hosted Actions by the repository runtime guard; local tests use synthetic HTML and mocked transport only. No workflow is included in this change.

```sh
python matchup/staff_routines.py --output-dir data/matchup/staff_routines
python -m unittest discover -s matchup -p test_staff_routines.py -v
```

The collector makes at most 20 requests and allows 20 MB total / 4 MB per page, with no retries or redirects. The fixed first catalog needs five requests. Standard output is aggregate-only JSON.

Output contract:

- `data/matchup/staff_routines/summary.json`: status (`completed` or `audit_only`), request/byte counts, annotation counts by sport/type/assertion, and zero counts for automatic training eligibility, verified historical publication, matched games and performance-effect claims.
- `staff_routine_annotations.csv.gz`: normalized facts and provenance; IDs and text are strings, unknown values use `\N`, flags are booleans serialized as `True`/`False`. No source prose is retained.
- `source_audit.json`: configured URLs/parsers, request/parse outcomes, hashes and aggregate per-source counts.
- `schema.json`: stable columns, null convention and no-article-text declaration.

The collector does not train models, join player outcomes, classify wins or place trades.
