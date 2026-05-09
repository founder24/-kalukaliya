"""Shared Mongo connection helper for the lambda_batch handlers.

The Mongo URI lives in AWS Secrets Manager (single source of truth =
Azure Key Vault per V4 §6; AWS SM is a read-only replica). Cold-start
fetches the secret once, hot invocations re-use the cached client.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

_client = None
_db = None


def _resolve_mongo_uri() -> str:
    # Direct env var wins (used in shadow mode + local tests).
    direct = os.environ.get("MONGO_URL", "").strip()
    if direct:
        return direct
    arn = os.environ.get("MONGO_URL_SECRET_ARN", "").strip()
    if not arn:
        raise RuntimeError("Neither MONGO_URL nor MONGO_URL_SECRET_ARN is set")
    import boto3  # type: ignore
    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=arn)
    raw = resp.get("SecretString") or ""
    # The secret may be a bare URI or a JSON blob with a `uri` key.
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw).get("uri", raw)
    return raw


def get_db() -> Any:
    """Return a cached Motor database handle (`syrabit` by default)."""
    global _client, _db
    if _db is not None:
        return _db
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
    uri = _resolve_mongo_uri()
    _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db_name = os.environ.get("MONGO_DB_NAME", "syrabit")
    _db = _client[db_name]
    return _db


def close() -> None:
    """Optional: close the Motor client. Lambda kills the process anyway."""
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


# ── Secret → env bootstrap (Task #551 §B reviewer fix) ──────────────────────
# The aca_jobs modules read provider config from process env (e.g.
# `PINECONE_API_KEY`, `WORKERS_EMBED_URL`, `WORKERS_EMBED_SECRET`).
# Lambda receives the *ARN* of each secret via env and must fetch +
# inject the value into `os.environ` BEFORE the aca_jobs module is
# imported, otherwise the provider boots in a misconfigured state.

_SECRET_ENV_MAP: dict[str, tuple[str, ...]] = {
    # ARN-bearing env var on the Lambda  →  target env vars to populate.
    # All entries resolve via Secrets Manager (`secretsmanager:GetSecretValue`)
    # — non-secret config like `WORKERS_EMBED_URL` is passed through as
    # a plain Lambda env var by `lambda-batch-jobs.tf` and not listed here.
    "MONGO_URL_SECRET_ARN":                            ("MONGO_URL",),
    "PINECONE_API_KEY_SECRET":                         ("PINECONE_API_KEY", "PINECONE_KEY"),
    "WORKERS_EMBED_SECRET_ARN":                        ("WORKERS_EMBED_SECRET", "EMBED_SHARED_SECRET"),
    # Translation-provider creds for `as-translation-backfill`
    # (round-3 reviewer fix; other handlers harmlessly ignore them).
    "CLOUDFLARE_API_TOKEN_SECRET_ARN":                 ("CLOUDFLARE_API_TOKEN",),
    "CF_AI_GATEWAY_ACCOUNT_ID_SECRET":                 ("CF_AI_GATEWAY_ACCOUNT_ID",),
    "GEMINI_API_KEY_SECRET_ARN":                       ("GEMINI_API_KEY",),
    "GOOGLE_APPLICATION_CREDENTIALS_JSON_SECRET_ARN":  ("GOOGLE_APPLICATION_CREDENTIALS_JSON",),
    # Task #565 — `chat-credit-runway` Lambda publishes the integer
    # runway estimate to Upstash Redis (selector reads it via the
    # backend's `deps.redis_client` on a 60 s in-process cache) and
    # captures Sentry events on compute / publish failure.
    "UPSTASH_REDIS_REST_URL_SECRET_ARN":               ("UPSTASH_REDIS_REST_URL",),
    "UPSTASH_REDIS_REST_TOKEN_SECRET_ARN":             ("UPSTASH_REDIS_REST_TOKEN",),
    "SENTRY_DSN_SECRET_ARN":                           ("SENTRY_DSN",),
    # Task #13 — `prewarm-seo-routes` presents this on every HEAD as
    # `X-Prewarm-Auth` so the Cloudflare worker honours the
    # `X-Prewarm-Recommended-TTL` cache-TTL override. Other handlers
    # harmlessly ignore the variable.
    "PREWARM_AUTH_TOKEN_SECRET_ARN":                   ("PREWARM_AUTH_TOKEN",),
}

_bootstrapped = False


def _fetch_secret_value(arn: str) -> str:
    import boto3  # type: ignore
    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=arn)
    raw = (resp.get("SecretString") or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            for k in ("value", "uri", "secret", "key"):
                if k in data:
                    return str(data[k])
        except Exception:
            pass
    return raw


def bootstrap_env() -> None:
    """Hydrate `os.environ` from Secrets Manager / SSM ARNs once per cold-start.

    Idempotent. Failures are logged and swallowed per-secret — a
    missing Pinecone key surfaces loudly downstream when the provider
    actually tries to use it (V4 §12 no-silent-fallbacks: the provider
    raises, we do not pretend success).
    """
    import logging
    global _bootstrapped
    if _bootstrapped:
        return
    log = logging.getLogger("lambda_batch._db.bootstrap")
    for arn_var, target_envs in _SECRET_ENV_MAP.items():
        arn = os.environ.get(arn_var, "").strip()
        if not arn:
            continue
        # Skip when at least one of the target envs is already populated
        # (e.g. local test sets MONGO_URL directly).
        if any(os.environ.get(t, "").strip() for t in target_envs):
            continue
        try:
            value = _fetch_secret_value(arn)
        except Exception as exc:
            log.warning("bootstrap_env: failed to fetch %s (%s): %s", arn_var, arn[:40], exc)
            continue
        if not value:
            continue
        for tgt in target_envs:
            os.environ[tgt] = value
    _bootstrapped = True
