FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04@sha256:4a801ef9232d2b05e69df4eb8aa054dbbe2824e5499e1e6e857320bb01ac41a9
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python-is-python3 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY azure/training_requirements.txt azure/publication_requirements.txt /app/azure/
RUN python -m pip install --no-cache-dir -r azure/training_requirements.txt -r azure/publication_requirements.txt
COPY azure/*.py /app/azure/
# Check the actual entrypoint import path without credentials, clients or I/O.
RUN python -c "import sys; sys.path.insert(0, '/app/azure'); from train_gpu import require_azure_job; from azure.identity import ManagedIdentityCredential; from azure.storage.blob import BlobServiceClient, ContentSettings"
RUN useradd --uid 10001 --create-home sports && chown -R sports:sports /app
USER 10001
CMD ["python", "azure/entrypoint.py"]
