"""Task #13 round-3 reviewer fix — Lambda env wiring regression.

Architect flagged that `prewarm-seo-routes` will silently skip the
`X-Prewarm-Recommended-TTL` override in production unless the Lambda
env actually carries `PREWARM_AUTH_TOKEN_SECRET_ARN` AND the cold-
start bootstrap mapping in `lambda_batch/_db.py` knows to hydrate it
into `PREWARM_AUTH_TOKEN`. Without both, the worker treats every HEAD
as unauthenticated and ignores the cache-calendar TTL — which means
the prewarm engine never extends KV TTLs during exam windows.

This test pins both signals against the file system so a future
refactor cannot quietly drop the wiring.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TF = REPO / "syrabit" / "infra" / "aws" / "lambda-batch-jobs.tf"
SECRETS_TF = REPO / "syrabit" / "infra" / "aws" / "secrets.tf"
DB = REPO / "syrabit" / "services" / "backend" / "lambda_batch" / "_db.py"


def test_origin_shared_secret_is_declared_in_secrets_tf() -> None:
    txt = SECRETS_TF.read_text()
    assert '"origin/shared-secret"' in txt, (
        "secrets.tf must declare `origin/shared-secret` so the prewarm "
        "Lambda can fetch the worker-equivalent BACKEND_ORIGIN_SECRET."
    )


def test_lambda_env_block_wires_prewarm_auth_token_secret_arn() -> None:
    txt = TF.read_text()
    # Data source declared.
    assert re.search(
        r'data\s+"aws_secretsmanager_secret"\s+"origin_shared_secret"',
        txt,
    ), "lambda-batch-jobs.tf must declare a data source for origin/shared-secret."
    # Env var injected on every batch-job Lambda (other handlers ignore it).
    assert "PREWARM_AUTH_TOKEN_SECRET_ARN" in txt, (
        "lambda-batch-jobs.tf env block must inject PREWARM_AUTH_TOKEN_SECRET_ARN "
        "= data.aws_secretsmanager_secret.origin_shared_secret.arn — without it "
        "the worker's getPrewarmOverrideTtl() returns null and cache_calendar "
        "TTL overrides are silently dropped (V4 §12 fail-loud violation)."
    )
    assert (
        "PREWARM_AUTH_TOKEN_SECRET_ARN       = data.aws_secretsmanager_secret.origin_shared_secret.arn"
        in txt
    ), "PREWARM_AUTH_TOKEN_SECRET_ARN must point at the origin_shared_secret ARN."


def test_db_bootstrap_maps_arn_into_prewarm_auth_token_env() -> None:
    txt = DB.read_text()
    assert '"PREWARM_AUTH_TOKEN_SECRET_ARN"' in txt and '"PREWARM_AUTH_TOKEN"' in txt, (
        "lambda_batch/_db.py _SECRET_ENV_MAP must hydrate "
        "PREWARM_AUTH_TOKEN_SECRET_ARN → PREWARM_AUTH_TOKEN at cold-start; "
        "otherwise prewarm_seo_routes never sets the X-Prewarm-Auth header."
    )
    # Pin the exact mapping line (single source of truth for cold-start hydration).
    assert re.search(
        r'"PREWARM_AUTH_TOKEN_SECRET_ARN"\s*:\s*\("PREWARM_AUTH_TOKEN",\)',
        txt,
    ), "Bootstrap entry shape changed — cold-start hydration may break."
