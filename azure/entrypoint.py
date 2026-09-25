"""Azure-only training with private, durable artifact publication."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from train_gpu import require_azure_job


def configuration(env):
    fields = {
        "account": "AZURE_STORAGE_ACCOUNT", "container": "AZURE_STORAGE_CONTAINER",
        "prefix": "AZURE_BLOB_PREFIX", "run_id": "SPORTS_PROPS_RUN_ID",
        "git_sha": "SPORTS_PROPS_GIT_SHA", "release": "SPORTS_PROPS_DATA_RELEASE",
    }
    values = {key: env.get(name, "") for key, name in fields.items()}
    if any(not value for value in values.values()):
        raise ValueError("Required storage and experiment metadata are missing")
    if not re.fullmatch(r"[a-z0-9]{3,24}", values["account"]):
        raise ValueError("Invalid storage account")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61})[a-z0-9]", values["container"]) or "--" in values["container"]:
        raise ValueError("Invalid private container")
    for key in ["prefix", "run_id", "release"]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", values[key]):
            raise ValueError("Unsafe experiment path component: " + key)
    if not re.fullmatch(r"[0-9a-f]{40}", values["git_sha"]):
        raise ValueError("A full immutable source commit is required")
    values["repo"] = env.get("SPORTS_PROPS_REPO", "Spoofyy-1/Tabular-Model-for-sports")
    if values["repo"] != "Spoofyy-1/Tabular-Model-for-sports":
        raise ValueError("Unexpected source repository")
    return values


def file_inventory(root):
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink publication is prohibited")
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        files.append((path, {"path": path.relative_to(root).as_posix(),
                             "bytes": path.stat().st_size, "sha256": digest.hexdigest()}))
    return files


def require_private_container(properties):
    if properties.get("public_access"):
        raise RuntimeError("Training outputs require a private Blob container")


def trainer_result(outputs, exit_code, failure_type=None):
    """A process exit alone never proves that all nine heads completed."""
    path = outputs / "run_status.json"
    try:
        status = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(status, dict):
            raise ValueError("Trainer status must be an object")
    except (OSError, ValueError):
        return exit_code or 1, failure_type or "InvalidTrainerStatus"
    if exit_code == 0 and (status.get("status") != "complete" or status.get("completed_heads") != 9):
        return 1, failure_type or "MissingTrainerCompletion"
    return exit_code, failure_type or (status.get("error_type") or "TrainerProcessFailed" if exit_code else None)


def publish_artifacts(container, base, root, cfg, exit_code, failure_type, json_settings):
    """Record partial publication as failed; write the terminal marker last."""
    uploaded = []
    publication_error = None
    try:
        inventory = file_inventory(root)
        for path, item in inventory:
            with path.open("rb") as data:
                container.upload_blob(base + item["path"], data, overwrite=False,
                    metadata={"sha256": item["sha256"]}, max_concurrency=2)
            uploaded.append(item)
    except Exception as exc:
        exit_code = exit_code or 1
        publication_error = type(exc).__name__
    publication = {**cfg, "status": "completed" if exit_code == 0 else "failed",
                   "exit_code": exit_code, "failure_type": failure_type,
                   "publication_error_type": publication_error,
                   "finished_at": datetime.now(timezone.utc).isoformat(),
                   "artifacts": uploaded}
    # If this upload also fails, propagate the exception. The immutable started
    # marker remains without a completed publication, never a false success.
    container.upload_blob(base + "publication.json", json.dumps(publication, indent=2).encode(),
                          overwrite=False, content_settings=json_settings)
    return publication


def main():
    require_azure_job()  # Never initialize cloud clients or data paths locally.
    cfg = configuration(os.environ)
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings
    identity = os.environ.get("AZURE_CLIENT_ID")
    credential = ManagedIdentityCredential(**({"client_id": identity} if identity else {}))
    service = BlobServiceClient("https://%s.blob.core.windows.net" % cfg["account"], credential=credential)
    try:
        container = service.get_container_client(cfg["container"])
        props = container.get_container_properties()
        require_private_container(props)
        base = cfg["prefix"] + "/" + cfg["run_id"] + "/"
        # A unique non-overwritten marker proves write access before a GPU/data run.
        container.upload_blob(base + "started.json", json.dumps({**cfg, "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "execution": os.environ["CONTAINER_APP_JOB_EXECUTION_NAME"]}).encode(), overwrite=False)
        exit_code = 1
        with tempfile.TemporaryDirectory(prefix="sports-a100-") as directory:
            root = Path(directory)
            outputs = root / "outputs"
            outputs.mkdir()
            failure_type, child = None, None
            try:
                command = [sys.executable, str(Path(__file__).with_name("train_gpu.py").resolve()),
                           "--data-release", cfg["release"], "--repo", cfg["repo"], "--output-dir", str(outputs)]
                with (root / "training.log").open("w") as log:
                    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             text=True, bufsize=1)
                    for line in child.stdout:
                        print(line, end="", flush=True)
                        log.write(line)
                    exit_code = child.wait()
            except Exception as exc:
                failure_type = type(exc).__name__
                if child is not None and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
            finally:
                if child is not None and child.stdout is not None:
                    child.stdout.close()
            exit_code, failure_type = trainer_result(outputs, exit_code, failure_type)
            publication = publish_artifacts(container, base, root, cfg, exit_code, failure_type,
                                            ContentSettings(content_type="application/json"))
            print(json.dumps({"status": publication["status"], "private_blob_prefix": base,
                              "published_files": len(publication["artifacts"])}), flush=True)
            return publication["exit_code"]
    finally:
        service.close()
        credential.close()


if __name__ == "__main__":
    sys.exit(main())
