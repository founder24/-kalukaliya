# artifacts/syrabit/services/backend/sqs_consumers/Dockerfile
#
# Task #560 — Multi-entrypoint AWS Lambda container image used by both
# the SQS-consumer Lambdas (`infra/aws/lambda-workers.tf`) and the
# batch-job Lambdas (`infra/aws/lambda-batch-jobs.tf`).
#
# One image, many handlers — each Lambda picks its entry point via the
# `image_config.command = [<handler>]` Terraform field, which AWS
# Lambda's Python base image dispatches to a `module.function` callable.
#
# What this image bundles:
#   1. `artifacts/syrabit-backend/` — the FastAPI backend tree. The
#      `aca_jobs/*` modules wrapped by `lambda_batch/*.handler` import
#      from `routes.*`, `providers.*`, `ai_input_cache`, etc., so the
#      whole backend has to ship inside the image.
#   2. `lambda_batch/` (Task #551 §B adapters) — daily / 6-hourly /
#      weekly batch-job handlers (as-translation-backfill,
#      embed-backfill, comprehend-sampler, cache-effectiveness).
#   3. `sqs_consumers/` (Task #332 consumers) — SQS event-source
#      Lambdas (seo-indexnow, discovery-engine-ingest, bing-*, etc.).
#
# Build context = repository root.
#   docker buildx build --platform linux/arm64 \
#     -f artifacts/syrabit/services/backend/sqs_consumers/Dockerfile \
#     -t <ecr>/syrabit/sqs-consumers:sqs-consumers-latest .
#
# Architecture: arm64 (Graviton2). Matches `architectures = ["arm64"]`
# in both lambda-workers.tf and lambda-batch-jobs.tf — building x86_64
# would silently make `aws_lambda_function.batch_job` fail to start
# with `Runtime.InvalidEntrypoint` because the image manifest arch
# would mismatch the function's declared architecture.

FROM public.ecr.aws/lambda/python:3.11-arm64

# ── Python deps via pre-downloaded arm64 wheels.
# Wheels are downloaded natively (no QEMU) in the CI runner before this
# build step using:
#   pip3 download --platform manylinux_2_17_aarch64 --only-binary=:all:
# They land at .build/arm64-wheels/ and are injected into the build
# context so pip never has to compile from source inside the emulated
# arm64 environment.  --find-links makes pip check the local cache first;
# --prefer-binary ensures pip takes a wheel over a source dist for any
# package not found in the cache.
COPY .build/arm64-wheels/ /tmp/wheels/
COPY artifacts/syrabit-backend/requirements.txt /tmp/requirements.txt
# --no-index forces pip to use ONLY the local wheel cache (never PyPI).
# Without --no-index, pip falls back to PyPI for any cache miss and
# downloads source tarballs that need gcc — which the Lambda base image
# doesn't have.  The pre-download step guarantees every package is in
# /tmp/wheels/ (C-extension packages as arm64 manylinux .whl, pure-Python
# packages as none-any .whl or .tar.gz).  Source dists of pure-Python
# packages install fine without a compiler.
RUN pip install --no-cache-dir --prefer-binary \
        --no-index \
        --find-links /tmp/wheels/ \
        -r /tmp/requirements.txt \
    && rm -rf /tmp/wheels/ /tmp/requirements.txt

# ── Application code. The Lambda runtime expects user code under
# ${LAMBDA_TASK_ROOT} (= /var/task on the AWS base image). Copy the
# FastAPI backend first so its packages (aca_jobs, routes, providers,
# ...) are importable by the lambda_batch wrappers, then layer the
# Lambda-specific entrypoint trees on top.
WORKDIR ${LAMBDA_TASK_ROOT}

COPY artifacts/syrabit-backend/ ${LAMBDA_TASK_ROOT}/
COPY artifacts/syrabit/services/backend/lambda_batch/ ${LAMBDA_TASK_ROOT}/lambda_batch/
COPY artifacts/syrabit/services/backend/sqs_consumers/ ${LAMBDA_TASK_ROOT}/sqs_consumers/

# Strip caches & test fixtures shipped under the backend tree — Lambda
# storage is billed per MB-second of cold-start image pull and the
# ACA backend ships with bench / tests subtrees that the handlers
# never reference.
RUN find ${LAMBDA_TASK_ROOT} -type d -name '__pycache__' -prune -exec rm -rf {} + \
    && rm -rf ${LAMBDA_TASK_ROOT}/tests ${LAMBDA_TASK_ROOT}/bench || true

# Default CMD — overridden per-Lambda by `image_config.command` in
# Terraform. Set to a clearly-wrong sentinel so a misconfigured
# function (no command override) fails loud at cold-start instead of
# silently running the wrong handler.
CMD ["lambda_batch._invalid.handler"]
