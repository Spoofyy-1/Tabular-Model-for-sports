The owner requires cloud-only dataset storage and processing.

- Keep code and documentation locally; never download, generate, cache, or train on sports datasets on the owner's machine.
- Run ingestion, CSV conversion and packaging on GitHub-hosted Actions runners. The owner additionally authorized training/testing on the existing Azure serverless A100 infrastructure on 2026-09-25. Azure training must run in a finite Container Apps Job with verified A100 access; preserve the existing a100-test app. Never spoof cloud environment variables or bypass guards. Local and self-hosted workstation data execution remain prohibited.
- Publish generated datasets as GitHub release assets. Persist Azure-trained models, predictions and evaluation results in private Azure Blob Storage. Do not commit datasets/models to Git. Inspect aggregate remote reports without downloading dataset/model assets locally.
- Development uses games before January 1, 2025 UTC. The initial experiment fits parameters before 2024, calibrates on 2024, and holds out 2025 onward. Do not tune using the holdout.
- Preserve source licenses, timestamps, IDs, missing values, and data-quality exclusions. Outcome-derived features must use earlier games only.
- Do not claim betting profitability from stat-prediction accuracy or from unverified quote samples. This project analyzes data and does not place bets.
- Small synthetic in-memory unit tests are permitted; they must not download real data.
