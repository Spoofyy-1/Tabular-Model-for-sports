"""Enforce the owner's cloud-only data storage policy."""
import os
from pathlib import Path


def require_github_hosted_runner():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Dataset collection, feature generation and training are allowed only on GitHub-hosted Actions runners. Local execution is disabled.")
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace or not Path(workspace).is_dir():
        raise RuntimeError("A valid GitHub Actions workspace is required")
