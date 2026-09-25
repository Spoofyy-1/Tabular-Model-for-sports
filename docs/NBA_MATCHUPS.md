# NBA shot locations and defender matchups

The cloud collector `context/nba_matchups.py` reads the producer's archive index at a pinned Git commit, records hashes and license attribution, and requests NBA shot-detail seasons beginning 1996–2025 plus available matchup seasons beginning 2017–2025, including postseason. Actual coverage is reported by the remote build. Source: [shufinskiy/nba_data](https://github.com/shufinskiy/nba_data), an Apache-2.0 repository compiling NBA data for research.

Shot records describe shooter, game, period, court location, distance, type, and result. Matchup records identify offensive and defensive players and include recorded matchup possessions, scoring, and other tracking measures when supplied. Source fields and official NBA identifiers are preserved separately from ESPN identifiers.

An explicit game crosswalk matches calendar date and home/away teams against the first published ESPN game archive. Ambiguous and unmatched games are not assigned invented IDs. Player IDs are not automatically translated between providers. Files are sorted and partitioned by season **start** year and competition phase; the existing ESPN season field uses the ending year.

These records describe what happened during a game. A target game's actual defender assignment, shot distribution, or tracking totals cannot predict that same game. Future models must use earlier games, verified roster information, and a defensible player crosswalk. Expected defensive assignments would need a separate pregame model.

Development/holdout labels use matched UTC game starts. Where only a source calendar date exists, December 31, 2024 remains explicitly unresolved at the UTC boundary; missing dates also remain unresolved. The original models and their held-out results are unchanged.

Output: `data/context/matchups/`, including shots and matchup Parquet partitions, game crosswalk, dictionary, source manifest, and aggregate `context_summary.json`. All data access and storage are restricted to GitHub-hosted Actions.
