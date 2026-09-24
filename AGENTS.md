The owner requires cloud-only dataset storage and processing.

- Keep code and documentation locally; never download, generate, cache, or train on sports datasets on the owner's machine.
- Run ingestion, feature generation, training, and packaging only on GitHub-hosted Actions runners. Never spoof runner environment variables or bypass the guards. Self-hosted runners are not permitted.
- Publish generated data and models as GitHub release assets. Do not commit them to Git. Inspect aggregate remote reports without downloading dataset/model assets locally.
- Development uses games before January 1, 2025 UTC. The initial experiment fits parameters before 2024, calibrates on 2024, and holds out 2025 onward. Do not tune using the holdout.
- Preserve source licenses, timestamps, IDs, missing values, and data-quality exclusions. Outcome-derived features must use earlier games only.
- Do not claim betting profitability from stat-prediction accuracy or from unverified quote samples. This project analyzes data and does not place bets.
- Small synthetic in-memory unit tests are permitted; they must not download real data.
