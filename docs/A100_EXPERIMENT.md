# Azure A100 baseline-feature experiment

Status on September 25, 2026: implementation and synthetic checks are prepared, but the owner deferred Azure sign-in and requested data collection only. No A100 training job has been launched. Resume this experiment only when the owner resumes GPU work.

The owner explicitly authorized training and testing on the existing Azure A100 after the CSV export. Dataset ingestion remains cloud-only. This entry point runs on an actual Azure Container Apps Job; it refuses the owner's machine and GitHub runners. It does not provision infrastructure, place bets, or upload artifacts itself.

Run in the authorized Azure job, using a new empty output directory:

```sh
python azure/train_gpu.py --data-release CSV_RELEASE_TAG --output-dir /tmp/a100-results
```

Both sports run by default. `--sport nba` or `--sport nfl` isolates a bundle. `--repo` defaults to `Spoofyy-1/Tabular-Model-for-sports`. The job prefers `csv-base-nba.tar.gz` and `csv-base-nfl.tar.gz`, reading `data/{sport}/development.csv.gz`, `holdout.csv.gz`, and `feature_schema.json`. String IDs and the export's `\N` null sentinel are preserved. If CSV assets do not exist, it accepts the baseline `dataset-{sport}.tar.gz` Parquet archives. It never loads a pickle or a baseline model. Downloads are limited to 1 GB per asset and 3 GB total; required archive members are copied by exact name without extracting arbitrary paths.

The frozen comparison uses four NBA targets (points, rebounds, assists, threes) and five NFL targets (passing yards/TDs, rushing yards, receiving yards, receptions). Eligibility matches the baseline: an observed appearance, ten prior appearances, valid target/prior mean, and target-specific NFL roles from the prior recorded appearance. Absence from the source is not a zero result.

Parameters fit on dates before 2024. The 2024 residual distribution sets empirical 80% intervals. Dates from January 1, 2025 UTC onward form the benchmark. All features come from the existing shifted feature table; no new injuries, matchups or odds enter this run. Each sport's models and calibrators are saved before that sport's benchmark rows are loaded. Parameters are fixed at 180 rounds, depth 4, learning rate 0.05, minimum child weight 50, L2 5, 128 bins and seed 41. There is no early stopping, parameter search, benchmark-based feature selection or retraining. The earlier baseline already reported 2025+ results, so this is a reused benchmark rather than a fresh untouched test. Report model MAE/RMSE, last-ten baseline error, yearly errors and interval coverage together; do not select a strategy from these results.

Use Python 3.10+ with `azure/training_requirements.txt`. The recommended compatibility pin is `xgboost==3.2.0` and `nvidia/cuda:12.8.1-runtime-ubuntu22.04`, with Python, pip and libgomp installed in the image. This deliberately preserves CUDA 12 compatibility with the existing resource; it is not a claim that 3.2.0 is the latest release. The [version-specific GPU guide](https://xgboost.readthedocs.io/en/release_3.2.0/gpu/index.html) specifies `device=cuda` with `tree_method=hist` and CUDA 12 support. [Release metadata](https://pypi.org/project/xgboost/3.2.0/) provides the Linux CUDA-enabled wheel; do not install `xgboost-cpu`.

Before any real-data request, the job checks Linux, Azure's actual built-in job/execution variables, `nvidia-smi`, the CUDA driver's device 0 (A100, compute capability 8.0), XGBoost's CUDA build, and a small synthetic GPU fit. Never set environment variables to bypass these checks. After every real fit, the runtime `save_config()` must contain `cuda:0`, histogram trees and the GPU histogram updater; fallback fails the job. CSV parsing and matrix construction may run on the Azure CPU. [Azure's built-in variables](https://learn.microsoft.com/en-us/azure/container-apps/environment-variables) and [XGBoost configuration/model serialization](https://xgboost.readthedocs.io/en/release_3.2.0/python/python_api.html) document these interfaces.

Outputs include nine portable model JSON files and runtime configs, calibration metadata/residual CSVs, `{sport}_predictions.csv.gz`, `{sport}_evaluation.json`, `experiment.json`, `gpu_smoke.json`, `run_status.json`, and a checksum/provenance `manifest.json`. Source downloads are temporary and deleted on exit. The parent Azure runner publishes outputs to cloud storage; inspect aggregate reports remotely. A failed run may leave partial artifacts with `run_status=failed` and must not be presented as nine completed models. No verified betting ROI is produced: executable quotes, historical settlement rules and fees remain separate requirements.

Synthetic policy checks can run locally without data or model training:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s azure -p test_train_gpu.py -v
```
