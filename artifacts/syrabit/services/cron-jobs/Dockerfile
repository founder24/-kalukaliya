# services/cron-jobs/Dockerfile
#
# Phase 4 — Cron port (Task #332).
#
# Single image baked for every Azure Container Apps Job in
# `infra/azure/container-apps-jobs.tf`. Each job's entrypoint is
# `services/cron-jobs/run.py`, which dispatches on $JOB_NAME to the
# matching coroutine in the legacy backend tree.
#
# Because the dispatch targets import a wide swath of the FastAPI
# backend (server.py, routes/*, seo_engine.py, etc.), the image
# installs the FULL backend `requirements.txt` — installing only the
# cron runner's narrow runtime would fail-import on the first job.
#
# Image size optimisation strategy:
#   • python:3.11-slim base — ~120 MB before our deps
#   • multi-stage with a builder so build-essential never lands in
#     the runtime layer
#   • shared image with the Lambda SQS consumers is intentionally
#     NOT done — Lambda needs the AWS Lambda Runtime Interface
#     Client and Container Apps Jobs does not.

# ─── Build stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libffi-dev gcc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Release pipeline stages the legacy backend tree under
# services/cron-jobs/_backend/ before `docker build`. _backend/
# carries its own requirements.txt; we install that FIRST so import
# of any backend module succeeds, then layer the cron-runner extras
# (azure-monitor exporter, etc.) on top.
COPY _backend /build/_backend
COPY requirements.txt /build/requirements.txt

RUN pip install --prefix=/install -r /build/_backend/requirements.txt \
 && pip install --prefix=/install -r /build/requirements.txt

# ─── Runtime stage ───────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/_backend

# libpq for asyncpg and libssl for some C extensions used by the
# backend (pymongo SRV resolver, cryptography).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libffi8 libssl3 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/_backend /app/_backend
COPY run.py /app/run.py

USER app

# Container Apps Jobs invokes the image with no args and expects the
# process to run once and exit. `run.py` reads JOB_NAME from env and
# returns the exit code from the dispatched coroutine.
ENTRYPOINT ["python", "/app/run.py"]
