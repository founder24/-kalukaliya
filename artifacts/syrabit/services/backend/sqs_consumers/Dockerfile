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
# requirements-lambda.txt = requirements.txt minus pywebpush / py-vapid.
# Those packages have no Python-3.11-compatible binary wheel (http-ece
# transitive dep requires Python < 3.11) and Lambda functions never send
# web-push notifications, so they are safe to exclude here.
COPY artifacts/syrabit-backend/requirements-lambda.txt /tmp/requirements.txt
# All .whl files in /tmp/wheels/ are pre-built manylinux arm64 binaries
# downloaded natively in CI.  pip refuses to install them inside the QEMU
# emulated arm64 container because QEMU's kernel interface prevents the
# manylinux audit (auditwheel / ldd) from returning the right glibc tags,
# so pip sees the wheels as "incompatible with this platform".
#
# Workaround: bypass pip's compatibility gate by extracting each wheel (a
# plain ZIP archive) directly into site-packages.  Wheel layout maps 1-to-1
# onto site-packages:  package/ and package-N.dist-info/ land in the right
# place with no further processing.  Post-extraction, pip validates that
# the packages are "installed" via .dist-info and won't try to re-download
# them.
RUN python3 -c "\
import zipfile, glob, os, sys; \
SP = '/var/lang/lib/python3.11/site-packages'; \
os.makedirs(SP, exist_ok=True); \
ok = skip = 0; \
[(__import__('zipfile').ZipFile(w).extractall(SP), globals().update(ok=ok+1)) \
    for w in sorted(glob.glob('/tmp/wheels/*.whl'))]; \
print(f'Extracted wheels to {SP}')" \
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
# aca_jobs/seo_baseline.py lazily imports ``seo_baseline`` (the script at
# repo-root scripts/seo_baseline.py).  lambda_batch/seo_baseline.py calls
# _ensure_scripts_on_path() which prepends /var/task/scripts to sys.path.
# Without this COPY the import fails with ModuleNotFoundError at runtime.
COPY scripts/ ${LAMBDA_TASK_ROOT}/scripts/

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
