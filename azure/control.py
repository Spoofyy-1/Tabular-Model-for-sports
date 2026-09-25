#!/usr/bin/env python3
"""Metadata-only Azure controller. No local dataset/model download or training."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

API = "2025-10-02-preview"
STORAGE_API = "2023-05-01"
OWNER_KEY = "sports-props-owner"
OWNER = "Spoofyy-1/Tabular-Model-for-sports"
MODE_KEY = "sports-props-mode"
SMOKE_KEY = "sports-props-smoke-execution"
SMOKE_IMAGE = "mcr.microsoft.com/k8se/gpu-quickstart:latest"
SMOKE_ARGS = ["--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
ACR_PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
BLOB_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
TERMINAL = {"Succeeded", "Failed", "Stopped", "Canceled", "Cancelled"}


class ControlError(RuntimeError):
    pass


def checked_name(value, label, pattern=r"[A-Za-z0-9_.()-]+"):
    if not value or not re.fullmatch(pattern, value):
        raise ControlError("Invalid " + label)
    return value


def assert_subscription(account, expected_name, expected_id=None):
    if account.get("name") != expected_name or account.get("state", "Enabled") != "Enabled":
        raise ControlError("Active Azure subscription is not the expected enabled subscription; no mutation performed")
    if expected_id and account.get("id", "").lower() != expected_id.lower():
        raise ControlError("Active Azure subscription ID does not match the explicit target; no mutation performed")
    try:
        uuid.UUID(account["id"])
    except (ValueError, KeyError, TypeError):
        raise ControlError("Azure did not return a valid subscription ID")


def redact(text):
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:sig|accountkey|client_secret|access_token|password|token)\s*[=:]\s*)[^\s&\"']+", r"\1[REDACTED]", text)
    return text


class Azure:
    def __init__(self, args):
        self.args = args
        self.account = self._call(["account", "show"])
        assert_subscription(self.account, args.subscription_name, args.subscription_id)
        self.subscription = self.account["id"]

    def _call(self, argv, raw=False, optional=False, timeout=90):
        command = [self.args.az] + list(argv) + ["--only-show-errors"]
        if not raw:
            command += ["--output", "json"]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ControlError("Azure command timed out. Its remote outcome is unknown; inspect before retrying.")
        except FileNotFoundError:
            raise ControlError("Azure CLI executable not found; set --az to the installed executable")
        if result.returncode:
            error = result.stderr or result.stdout
            if optional and any(code in error for code in ["ResourceNotFound", "ResourceGroupNotFound", "(NotFound)"]):
                return None
            # CLI stdout/stderr can contain signed URLs or request bodies.
            # Report a bounded scrubbed error, never echo the full command.
            message = redact(error).strip().splitlines()
            raise ControlError("Azure command failed: " + (message[0][:400] if message else "no diagnostic returned"))
        if raw:
            return result.stdout
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def read(self, argv, **kwargs):
        return self._call(list(argv) + ["--subscription", self.subscription], **kwargs)

    def mutate(self, argv, **kwargs):
        if not self.args.subscription_id:
            raise ControlError("Mutations require --subscription-id from the inspected account")
        current = self._call(["account", "show"])
        assert_subscription(current, self.args.subscription_name, self.args.subscription_id)
        return self._call(list(argv) + ["--subscription", self.subscription], **kwargs)

    def resource(self, provider, kind, name):
        checked_name(name, "resource name")
        return "/subscriptions/%s/resourceGroups/%s/providers/%s/%s/%s" % (self.subscription, self.args.resource_group, provider, kind, name)

    def assert_scope(self, resource_id):
        if not resource_id.lower().startswith("/subscriptions/" + self.subscription.lower() + "/"):
            raise ControlError("Resource is outside the verified target subscription")

    def rest(self, method, resource_id, body=None, optional=False, api=API):
        self.assert_scope(resource_id)
        argv = ["rest", "--method", method, "--url", "https://management.azure.com" + resource_id + "?api-version=" + api]
        if body is not None:
            argv += ["--body", json.dumps(body, separators=(",", ":")), "--headers", "Content-Type=application/json"]
        return (self.read if method == "get" else self.mutate)(argv, optional=optional)


def derive_gpu(app, environment):
    props = app.get("properties", {})
    environment_id = environment.get("id")
    if not environment_id or (props.get("environmentId") or props.get("managedEnvironmentId", "")).lower() != environment_id.lower():
        raise ControlError("Known-good app does not belong to the selected environment")
    profile_name = props.get("workloadProfileName")
    profiles = environment.get("properties", {}).get("workloadProfiles", [])
    matching = [p for p in profiles if p.get("name") == profile_name]
    if len(matching) != 1 or "A100" not in matching[0].get("workloadProfileType", "").upper():
        raise ControlError("Live app workload profile is not an unambiguous A100 environment profile")
    containers = props.get("template", {}).get("containers", [])
    if len(containers) != 1:
        raise ControlError("Known-good GPU app must expose exactly one container for safe resource copying")
    resources = containers[0].get("resources", {})
    if resources.get("gpu") != 1 or isinstance(resources.get("gpu"), bool):
        raise ControlError("Live preview API must explicitly expose resources.gpu=1; refusing to infer or fall back to CPU")
    if not isinstance(resources.get("cpu"), (int, float)) or resources["cpu"] <= 0 or not resources.get("memory"):
        raise ControlError("Live app did not expose valid CPU and memory resources")
    if set(resources) - {"cpu", "memory", "gpu"}:
        raise ControlError("Live GPU resource schema has additional fields; review before generating the Job")
    return {"resources": copy.deepcopy(resources), "workloadProfileName": profile_name,
            "workloadProfileType": matching[0]["workloadProfileType"], "environmentId": environment_id,
            "location": environment.get("location")}


def assert_owned(job):
    if not job or job.get("tags", {}).get(OWNER_KEY) != OWNER:
        raise ControlError("Job is absent or lacks this project's ownership tag; refusing to modify an unrelated job")


def assert_private_container(container):
    if container.get("properties", {}).get("publicAccess") not in (None, "None"):
        raise ControlError("Output Blob container permits public access; a private existing container is required")


def job_body(gpu, mode, image=None, env=None, registry=None, commit=None, smoke_execution=None):
    if mode not in ("smoke", "training"):
        raise ControlError("Unknown execution mode")
    training = mode == "training"
    if training and (not image or "@sha256:" not in image or not registry or not commit or not smoke_execution):
        raise ControlError("Training requires an immutable image digest, registry, commit and verified smoke execution")
    tags = {OWNER_KEY: OWNER, MODE_KEY: mode}
    if commit:
        tags["sports-props-commit"] = commit
    if smoke_execution:
        tags[SMOKE_KEY] = smoke_execution
    container = {"name": "trainer", "image": image if training else SMOKE_IMAGE,
                 "command": ["python", "azure/entrypoint.py"] if training else ["nvidia-smi"],
                 "args": [] if training else SMOKE_ARGS, "resources": copy.deepcopy(gpu["resources"]),
                 "env": [{"name": k, "value": v} for k, v in sorted((env or {}).items())]}
    configuration = {"triggerType": "Manual", "replicaTimeout": 3600, "replicaRetryLimit": 0,
                     "manualTriggerConfig": {"parallelism": 1, "replicaCompletionCount": 1}}
    if training:
        configuration["registries"] = [{"server": registry, "identity": "system"}]
    return {"location": gpu["location"], "tags": tags, "identity": {"type": "SystemAssigned"},
            "properties": {"environmentId": gpu["environmentId"], "workloadProfileName": gpu["workloadProfileName"],
                           "configuration": configuration, "template": {"containers": [container]}}}


def assert_bounded_job(job, gpu):
    assert_owned(job)
    p = job.get("properties", {})
    cfg = p.get("configuration", {})
    if cfg.get("triggerType") != "Manual" or cfg.get("replicaRetryLimit") != 0 or cfg.get("replicaTimeout") != 3600:
        raise ControlError("Job is not manual, retry-free and limited to 3600 seconds")
    if cfg.get("manualTriggerConfig", {}) != {"parallelism": 1, "replicaCompletionCount": 1}:
        raise ControlError("Job parallelism/completion count is not exactly one")
    containers = p.get("template", {}).get("containers", [])
    if len(containers) != 1 or containers[0].get("resources") != gpu["resources"]:
        raise ControlError("Job resource shape differs from the live known-good A100 app")
    if p.get("workloadProfileName") != gpu["workloadProfileName"] or p.get("environmentId", "").lower() != gpu["environmentId"].lower():
        raise ControlError("Job GPU workload profile/environment differs from the known-good app")
    if p.get("template", {}).get("initContainers"):
        raise ControlError("Unexpected init containers require review")


def smoke_evidence(execution, logs):
    p = execution.get("properties", execution)
    if p.get("status") != "Succeeded":
        raise ControlError("Smoke execution has not succeeded; training cannot advance")
    containers = p.get("template", {}).get("containers", [])
    if len(containers) != 1 or containers[0].get("command") != ["nvidia-smi"] or containers[0].get("args") != SMOKE_ARGS:
        raise ControlError("Execution metadata does not identify this controller's nvidia-smi smoke command")
    if containers[0].get("resources", {}).get("gpu") != 1:
        raise ControlError("Smoke execution does not document exactly one GPU")
    observed = []
    for line in logs.splitlines():
        match = re.search(r"((?:NVIDIA|Tesla|A100)[^,\r\n]*),\s*(\d+)\s*(?:MiB)?\s*$", line, re.I)
        if match:
            observed.append((match.group(1).strip(), int(match.group(2))))
    if len(observed) != 1 or not re.search(r"\bA100\b", observed[0][0], re.I) or not 76000 <= observed[0][1] <= 83000:
        raise ControlError("Smoke logs do not show exactly one A100 80GB GPU; no automatic advancement")
    return {"execution": execution.get("name"), "status": "Succeeded", "gpu": observed[0][0], "memory_mib": observed[0][1], "count": 1,
            "log_sha256": hashlib.sha256(logs.encode()).hexdigest(), "checked_at_utc": datetime.now(timezone.utc).isoformat()}


class Controller:
    def __init__(self, azure, args):
        self.az, self.args = azure, args
        self.job_id = azure.resource("Microsoft.App", "jobs", args.job)

    def gpu(self):
        environment = self.az.read(["containerapp", "env", "show", "--name", self.args.environment, "--resource-group", self.args.resource_group])
        app = self.az.rest("get", self.az.resource("Microsoft.App", "containerApps", self.args.test_app))
        return derive_gpu(app, environment), app

    def job(self, optional=False):
        return self.az.rest("get", self.job_id, optional=optional)

    def executions(self):
        result = self.az.rest("get", self.job_id + "/executions")
        return result.get("value", [])

    def no_active_execution(self):
        active = [e.get("name") for e in self.executions() if e.get("properties", e).get("status") not in TERMINAL]
        if active:
            raise ControlError("An execution is already active or has unknown status; refusing another execution/update")

    def execution(self, name):
        checked_name(name, "execution name", r"[a-z0-9-]+")
        return self.az.rest("get", self.job_id + "/executions/" + name)

    def logs(self, name):
        self.execution(name)
        return self.az.read(["containerapp", "job", "logs", "show", "--name", self.args.job, "--resource-group", self.args.resource_group,
                             "--execution", name, "--container", "trainer", "--tail", str(self.args.tail), "--follow", "false", "--format", "text"], raw=True)

    def verify_smoke(self, name):
        if not name:
            raise ControlError("--smoke-execution is required; inspect a successful smoke execution first")
        return smoke_evidence(self.execution(name), self.logs(name))

    def registry(self):
        if not self.args.registry:
            raise ControlError("--registry must identify an existing ACR")
        registry = self.az.read(["acr", "show", "--name", self.args.registry])
        self.az.assert_scope(registry["id"])
        if "abac" in str(registry.get("roleAssignmentMode", "")).lower():
            raise ControlError("ACR uses ABAC repository permissions; AcrPull is not sufficient. Review repository-scoped access before continuing.")
        return registry

    def storage(self):
        if not self.args.storage_account or not self.args.container:
            raise ControlError("Training requires an existing --storage-account and private --container")
        account = self.az.read(["storage", "account", "show", "--name", self.args.storage_account, "--resource-group", self.args.storage_resource_group or self.args.resource_group])
        self.az.assert_scope(account["id"])
        container_id = account["id"] + "/blobServices/default/containers/" + self.args.container
        container = self.az.rest("get", container_id, api=STORAGE_API)
        assert_private_container(container)
        return account, container_id

    def grant(self, principal, role, scope):
        self.az.assert_scope(scope)
        existing = self.az.read(["role", "assignment", "list", "--assignee", principal, "--scope", scope])
        if any(x.get("roleDefinitionId", "").lower().endswith("/" + role) and x.get("scope", "").lower() == scope.lower() for x in existing):
            return
        self.az.mutate(["role", "assignment", "create", "--assignee-object-id", principal,
                        "--assignee-principal-type", "ServicePrincipal", "--role", role, "--scope", scope])

    def inspect(self):
        gpu, app = self.gpu()
        registries = self.az.read(["acr", "list", "--resource-group", self.args.resource_group])
        storage = self.az.read(["storage", "account", "list", "--resource-group", self.args.resource_group])
        job = self.job(optional=True)
        return {"subscription": {"name": self.az.account["name"], "id": self.az.subscription}, "gpu": gpu,
                "test_app": {"name": self.args.test_app, "scale": app.get("properties", {}).get("template", {}).get("scale")},
                "registries": [{k: x.get(k) for k in ["name", "id", "loginServer"]} for x in registries],
                "storage_accounts": [{k: x.get(k) for k in ["name", "id", "location"]} for x in storage],
                "job_exists": bool(job), "job_owned": bool(job and job.get("tags", {}).get(OWNER_KEY) == OWNER)}

    def build(self):
        registry = self.registry()
        if not self.args.commit or not re.fullmatch(r"[0-9a-f]{40}", self.args.commit):
            raise ControlError("Cloud build requires --commit with a full immutable public Git commit SHA")
        tag = self.args.commit + "-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        source = "https://github.com/" + self.args.repo + ".git#" + self.args.commit
        result = self.az.mutate(["acr", "build", "--registry", self.args.registry, "--image", "sports-props:" + tag,
                                 "--file", "azure/Dockerfile", "--platform", "linux/amd64", "--timeout", "1800",
                                 "--no-wait", "--no-logs", source])
        return {"build_id": result.get("runId") or result.get("name"), "status": result.get("status"),
                "registry": registry["name"], "image_tag": "sports-props:" + tag, "commit": self.args.commit,
                "next": "Use build-status, then image to resolve the digest; no job was started."}

    def image(self):
        registry = self.registry()
        if not self.args.image_tag or not re.fullmatch(r"sports-props:[0-9a-f]{40}-[0-9]{14}-[0-9a-f]{8}", self.args.image_tag):
            raise ControlError("--image-tag must be the immutable source tag emitted by build")
        metadata = self.az.read(["acr", "repository", "show", "--name", self.args.registry, "--image", self.args.image_tag])
        digest = metadata.get("digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ControlError("ACR did not return an immutable image digest")
        return {"image": registry["loginServer"] + "/sports-props@" + digest,
                "commit": self.args.image_tag.split(":", 1)[1][:40], "image_tag": self.args.image_tag}

    def desired(self):
        gpu, _ = self.gpu()
        if self.args.smoke:
            return job_body(gpu, "smoke"), gpu, None
        if not self.args.commit or not re.fullmatch(r"[0-9a-f]{40}", self.args.commit):
            raise ControlError("Training requires the full --commit from the cloud build")
        if not self.args.data_release or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", self.args.data_release):
            raise ControlError("Training requires --data-release for the immutable published CSV release")
        registry = self.registry()
        self.storage()
        prefix = registry["loginServer"] + "/sports-props@sha256:"
        if not self.args.image or not self.args.image.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", self.args.image[len(prefix):]):
            raise ControlError("Training image must use a digest in the selected ACR sports-props repository")
        metadata = self.az.read(["acr", "repository", "show", "--name", self.args.registry,
                                 "--image", self.args.image.split("/", 1)[1]])
        if not any(tag.startswith(self.args.commit + "-") for tag in (metadata.get("tags") or [])):
            raise ControlError("Selected image digest has no build tag matching the supplied source commit")
        proof = self.verify_smoke(self.args.smoke_execution)
        env = {"AZURE_STORAGE_ACCOUNT": self.args.storage_account, "AZURE_STORAGE_CONTAINER": self.args.container,
               "AZURE_BLOB_PREFIX": self.args.blob_prefix, "SPORTS_PROPS_GIT_SHA": self.args.commit,
               "SPORTS_PROPS_DATA_RELEASE": self.args.data_release, "SPORTS_PROPS_REPO": self.args.repo}
        body = job_body(gpu, "training", self.args.image, env, registry["loginServer"], self.args.commit, self.args.smoke_execution)
        return body, gpu, proof

    def deploy(self):
        current = self.job(optional=True)
        if current:
            assert_owned(current)
            self.no_active_execution()
        body, gpu, proof = self.desired()
        if not self.args.smoke:
            if not current:
                raise ControlError("Create and verify this project's public-image smoke Job before training deployment")
            principal = current.get("identity", {}).get("principalId")
            if not principal:
                raise ControlError("Existing smoke Job does not expose its system-assigned identity")
            registry = self.registry()
            _, container_id = self.storage()
            self.grant(principal, ACR_PULL, registry["id"])
            self.grant(principal, BLOB_CONTRIBUTOR, container_id)
        self.az.rest("put", self.job_id, body)
        actual = self.job()
        assert_bounded_job(actual, gpu)
        return {"job": self.args.job, "mode": body["tags"][MODE_KEY], "provisioning_state": actual.get("properties", {}).get("provisioningState"),
                "gpu": gpu, "smoke_evidence": proof, "started": False}

    def run(self):
        current = self.job()
        gpu, _ = self.gpu()
        assert_bounded_job(current, gpu)
        if current.get("properties", {}).get("provisioningState") != "Succeeded":
            raise ControlError("Job provisioning has not succeeded")
        mode = "smoke" if self.args.smoke else "training"
        if current["tags"].get(MODE_KEY) != mode:
            raise ControlError("Requested mode differs from the deployed Job; deploy the intended mode first")
        self.no_active_execution()
        if mode == "training":
            self.verify_smoke(current["tags"].get(SMOKE_KEY))
        template = copy.deepcopy(current["properties"]["template"])
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
        if mode == "training":
            env = template["containers"][0].get("env", [])
            env = [e for e in env if e.get("name") != "SPORTS_PROPS_RUN_ID"]
            env.append({"name": "SPORTS_PROPS_RUN_ID", "value": run_id})
            template["containers"][0]["env"] = env
        result = self.az.rest("post", self.job_id + "/start", template)
        return {"job": self.args.job, "mode": mode, "execution": result.get("name"), "execution_id": result.get("id"),
                "run_id": run_id, "timeout_seconds": 3600, "gpu_count": 1, "automatic_retries": 0}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "plan", "build", "build-status", "image", "deploy", "run", "status", "logs", "verify-smoke", "stop"])
    parser.add_argument("--az", default="az")
    parser.add_argument("--subscription-name", default="Azure subscription 1")
    parser.add_argument("--subscription-id")
    parser.add_argument("--resource-group", default="ml-training-rg")
    parser.add_argument("--environment", default="ml-training-env")
    parser.add_argument("--test-app", default="a100-test")
    parser.add_argument("--job", default="sports-props-a100")
    parser.add_argument("--registry")
    parser.add_argument("--storage-account")
    parser.add_argument("--storage-resource-group")
    parser.add_argument("--container")
    parser.add_argument("--blob-prefix", default="sports-props")
    parser.add_argument("--repo", default=OWNER)
    parser.add_argument("--commit")
    parser.add_argument("--image")
    parser.add_argument("--image-tag")
    parser.add_argument("--build-id")
    parser.add_argument("--data-release")
    parser.add_argument("--execution")
    parser.add_argument("--smoke-execution")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--tail", type=int, default=300)
    args = parser.parse_args(argv)
    for value, label in [(args.resource_group, "resource group"), (args.environment, "environment"), (args.test_app, "test app")]:
        checked_name(value, label)
    checked_name(args.job, "job", r"[a-z][a-z0-9-]{0,29}[a-z0-9]")
    if args.job == args.test_app or "--" in args.job:
        raise ControlError("Reserved or invalid job name")
    checked_name(args.repo, "public GitHub repository", r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
    checked_name(args.blob_prefix, "Blob prefix", r"[A-Za-z0-9][A-Za-z0-9/_-]*")
    if args.container:
        checked_name(args.container, "Blob container", r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
    if not 1 <= args.tail <= 300:
        raise ControlError("--tail must be 1..300")
    if args.action in ["build", "deploy", "run", "stop"] and not args.subscription_id:
        raise ControlError("This action requires an explicit --subscription-id; inspect first")
    return args


def main(argv=None):
    args = parse_args(argv)
    azure = Azure(args)
    controller = Controller(azure, args)
    if args.action == "inspect":
        result = controller.inspect()
    elif args.action == "plan":
        result = controller.desired()[0]
    elif args.action == "build":
        result = controller.build()
    elif args.action == "build-status":
        controller.registry()
        if not args.build_id:
            raise ControlError("--build-id is required")
        run = azure.read(["acr", "task", "show-run", "--registry", args.registry, "--run-id", args.build_id])
        result = {k: run.get(k) for k in ["runId", "status", "startTime", "finishTime"]}
    elif args.action == "image":
        result = controller.image()
    elif args.action == "deploy":
        result = controller.deploy()
    elif args.action == "run":
        result = controller.run()
    else:
        job = controller.job()
        assert_owned(job)
        if args.action == "status":
            executions = [controller.execution(args.execution)] if args.execution else controller.executions()
            result = {"job": args.job, "mode": job["tags"].get(MODE_KEY), "provisioning_state": job.get("properties", {}).get("provisioningState"),
                      "executions": [{"name": e.get("name"), **{k: e.get("properties", e).get(k) for k in ["status", "startTime", "endTime"]}} for e in executions]}
        else:
            if not args.execution:
                raise ControlError("--execution is required; implicit latest/all execution actions are prohibited")
            if args.action == "logs":
                print(redact(controller.logs(args.execution)))
                return
            if args.action == "verify-smoke":
                result = controller.verify_smoke(args.execution)
            elif args.action == "stop":
                controller.execution(args.execution)
                azure.mutate(["containerapp", "job", "stop", "--name", args.job, "--resource-group", args.resource_group,
                              "--job-execution-name", args.execution])
                result = {"job": args.job, "execution": args.execution, "stop_requested": True}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ControlError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
