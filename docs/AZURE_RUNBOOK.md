# Azure A100 training controller

`azure/control.py` controls the existing serverless Container Apps environment.
The existing `a100-test` app is read only and is never changed, stopped, deleted
or used for training. The controller creates or updates only a project-owned
manual Job named `sports-props-a100` by default. It does not create resource
groups, environments, registries, storage accounts or Blob containers.

Local work is limited to code and resource metadata. Dataset downloads and
training happen in the remote A100 container. Models, predictions and evaluation
artifacts persist in private Azure Blob Storage through managed identity.
Nothing here downloads those artifacts to the workstation.

The approved job boundary is one A100, parallelism one, one required completion,
zero retries and a 3,600-second replica timeout. CPU, memory, GPU and workload
profile values are copied from the live `a100-test` resource; none are guessed.
Missing GPU fields, non-A100 profiles, changed resource shapes or unknown
execution states fail closed. An active execution blocks another start or job
update. This check is not a distributed lock: use one controller at a time.

The REST API is `2025-10-02-preview`, whose `Microsoft.App/jobs` schema explicitly
includes `resources.gpu`. The controller also reads the test app through that
preview API. It never silently switches to a stable API that drops GPU fields.

Before execution, the operator completes Azure CLI login. The controller never
logs in, changes subscriptions, registers providers or manages account secrets.
All mutation commands require the explicit subscription ID. They recheck both
the active ID and the expected name, `Azure subscription 1`, before each write.
Wrong-account checks fail before any mutation. The ID is supplied at runtime,
not committed to this repository.

Read the live configuration and available resources:

```sh
python azure/control.py inspect
```

If the installed Azure CLI is outside `PATH`, append `--az /absolute/path/to/az`
to each command. Use the returned subscription ID as `<subscription-id>` below.
The known defaults are `ml-training-rg`, `ml-training-env`, and `a100-test`.

Review, deploy and start the public-image GPU smoke Job:

```sh
python azure/control.py plan --smoke
python azure/control.py deploy --smoke --subscription-id <subscription-id>
python azure/control.py run --smoke --subscription-id <subscription-id>
python azure/control.py status --execution <smoke-execution>
python azure/control.py logs --execution <smoke-execution>
python azure/control.py verify-smoke --execution <smoke-execution>
```

The smoke image is Microsoft's GPU quickstart. Its only command queries
`nvidia-smi` for GPU name and memory. Training deployment requires a successful
smoke execution whose metadata and CSV logs show exactly one NVIDIA A100 with
76,000–83,000 MiB reported memory, allowing driver-reserved memory on an 80 GB
device. The evidence records the actual reported GPU name and memory.
Neither elapsed time nor a successful resource deployment counts as a passed
smoke test. No automatic advancement or execution retry is performed.

Container Apps replica logs may disappear when a completed pod is cleaned up.
Run the status and log checks promptly. If the proof is unavailable, the
controller stops; it does not infer success. An explicitly requested new smoke
execution can provide fresh proof. Long-term training artifacts persist in Blob
Storage, independent of replica retention.

Cloud-build the committed public source using an existing ACR:

```sh
python azure/control.py build --subscription-id <subscription-id> \
  --registry <existing-acr> --commit <full-40-character-commit>
python azure/control.py build-status --registry <existing-acr> --build-id <build-id>
python azure/control.py image --registry <existing-acr> --image-tag <returned-image-tag>
```

The build uses `az acr build`, Linux/amd64, `azure/Dockerfile`, and the immutable
public Git commit as its remote context. It uploads no local build context.
The committed `.dockerignore` must permit only source/configuration needed by
the image. Dataset and model files never belong in that context. Build tags
include the full commit, UTC timestamp and random suffix. Deployment pins the
resolved image digest and checks that ACR associates it with the supplied commit.
Build queuing returns immediately; inspect build status before resolving the
image. Build time is separately bounded at 1,800 seconds.

Use an existing private Blob container and deploy the training image:

```sh
python azure/control.py deploy --subscription-id <subscription-id> \
  --registry <existing-acr> --image <acr/sports-props@sha256:digest> \
  --commit <full-commit> --data-release <published-csv-release-tag> \
  --storage-account <existing-storage-account> --container <private-container> \
  --smoke-execution <verified-smoke-execution>
python azure/control.py run --subscription-id <subscription-id>
```

The controller reuses the Job's system-assigned managed identity. When needed,
it grants **AcrPull** at the selected registry resource and **Storage Blob Data
Contributor** only at the selected Blob container, never subscription/resource
group scope. It neither enables ACR admin passwords nor uses storage account
keys or SAS tokens. ACRs using ABAC repository permissions require a separate
repository-role review; the controller stops rather than granting a broader role.
Allow any necessary role propagation before starting; no training retry is
configured. If the ACR, storage account or private container is absent, resource
creation must be handled separately using explicit names and the authorized
training scope.

Training command and non-secret environment interface:

```text
python azure/entrypoint.py
AZURE_STORAGE_ACCOUNT
AZURE_STORAGE_CONTAINER
AZURE_BLOB_PREFIX                  default: sports-props
SPORTS_PROPS_RUN_ID                new value for each execution
SPORTS_PROPS_GIT_SHA
SPORTS_PROPS_DATA_RELEASE
SPORTS_PROPS_REPO                  default: Spoofyy-1/Tabular-Model-for-sports
```

The entrypoint validates remote Job identity and actual A100/CUDA access,
downloads the selected dataset release inside Azure, runs the trainer and uploads
artifacts under `<blob-prefix>/<run-id>`. Its outputs must be written before the
finite replica stops. Models are not stored in the local controller workspace.

Inspect or stop one specific execution:

```sh
python azure/control.py status --execution <training-execution>
python azure/control.py logs --execution <training-execution>
python azure/control.py stop --subscription-id <subscription-id> --execution <training-execution>
```

`stop` targets only that execution; it does not delete the Job or affect
`a100-test`. Logs are bounded to 300 lines and common credential forms are
redacted. The application must also avoid logging credentials. The controller
does not call secret-list, registry credential or storage key APIs. Existing Jobs
without the `sports-props-owner` project tag are never repurposed, including an
unrelated `a100-training` Job.

Pure local verification does not invoke Azure or access sports data:

```sh
python -m unittest discover -s azure -p test_control.py -v
```

References: [GPU-capable Jobs resource schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/2025-10-02-preview/jobs),
[Container Apps Job commands](https://learn.microsoft.com/en-us/cli/azure/containerapp/job?view=azure-cli-latest),
[Job logs and replica retention](https://learn.microsoft.com/en-us/cli/azure/containerapp/job/logs?view=azure-cli-latest),
and [ACR cloud builds](https://learn.microsoft.com/en-us/cli/azure/acr?view=azure-cli-latest#az-acr-build).
