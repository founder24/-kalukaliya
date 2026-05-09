"""Task #27 — Cohere `embed-multilingual-v3` via AWS Bedrock.

Bedrock-only Indic embed route. Reuses the existing AWS credential
plumbing already shared with SES / S3 / Lambda — **no** new vendor
key, **no** Cohere SDK, **no** `COHERE_API_KEY`. The route is selected
only when the language detector classifies the input as Assamese / Indic
(see `llm._embed_feature_for`); English and unknown-language inputs
continue to use `providers.workers_embed` unchanged.

Returned vectors are pinned to **1024-dim** to match the locked Pinecone
index (`founder lock`); a different dimension fails loud per V4 §12.

Failure shape:
  * IAM `AccessDeniedException` / Bedrock "model access not granted"
    → raises `BedrockEmbedAccessDenied` so the dispatcher can fall back
      to Workers AI for that single call (logged + Sentry breadcrumb).
  * Throttling, transient 5xx, dim mismatch, empty payload
    → raises `BedrockEmbedError` (caller decides; default behaviour
      in `llm.call_embed_with_dispatch` is the same Workers-AI fallback).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger("providers.cohere_bedrock_embed")

# Pinecone is locked at 1024-dim (founder lock — replit.md gotcha).
EMBED_DIM = 1024

# Bedrock model id — quoted literal here is allowed by the umbrella CI
# guard refinement in Task #27 (the ban was scoped to `import cohere`,
# `from cohere`, and `COHERE_API_KEY` so the Bedrock model id literal
# can survive). DO NOT collapse the dotted form into a bare `cohere`
# token elsewhere.
MODEL_ID = "cohere.embed-multilingual-v3"

# Region knob (defaults to us-east-1 where Cohere Embed Multilingual v3
# is generally available and on-demand priced). Operators can flip via
# ACA env var without a code change.
DEFAULT_REGION = os.environ.get("BEDROCK_EMBED_REGION", "us-east-1")

PROVIDER_NAME = "cohere_multilingual_v3_bedrock"

# Bedrock on-demand pricing for `cohere.embed-multilingual-v3` is
# $0.0001 per 1k input tokens (us-east-1, AWS pricing page sampled
# 2026-05-09). See `cost_caps.BEDROCK_COHERE_EMBED_USD_PER_1K_TOKENS`
# for the constant the meter consumes.

# Lazy boto3 import + client cache — keeps cold-start cheap and keeps
# the module importable in dev shells without boto3 installed.
_CLIENT_LOCK = threading.Lock()
_CLIENTS: dict[str, Any] = {}


class BedrockEmbedError(RuntimeError):
    """Generic non-recoverable Bedrock embed failure."""


class BedrockEmbedAccessDenied(BedrockEmbedError):
    """IAM denied or Bedrock model access not granted — caller should
    fall back to Workers AI for this single call (Task #27 §10:
    `Absence or revocation of the Bedrock IAM permission puts the Indic
    route in degraded mode`)."""


def _get_client(region: str):
    cl = _CLIENTS.get(region)
    if cl is not None:
        return cl
    with _CLIENT_LOCK:
        cl = _CLIENTS.get(region)
        if cl is not None:
            return cl
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover — dev shell
            raise BedrockEmbedError(
                "boto3 not installed — Bedrock Indic embed unavailable"
            ) from exc
        cl = boto3.client("bedrock-runtime", region_name=region)
        _CLIENTS[region] = cl
        return cl


def is_configured() -> bool:
    """True when boto3 is importable. The actual IAM grant is verified
    on first call (boto3 doesn't expose a cheap reachability probe
    that doesn't burn a token)."""
    try:
        import boto3  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def _input_type_for(task_type: str | None) -> str:
    """Map our internal `task_type` to Cohere's `input_type` enum.

    Cohere requires one of: `search_document`, `search_query`,
    `classification`, `clustering`. We mirror the Workers-AI route's
    `RETRIEVAL_QUERY` → `search_query` mapping so cache parity holds.
    """
    if (task_type or "").upper().endswith("QUERY"):
        return "search_query"
    return "search_document"


async def embed_one(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
    *,
    region: str | None = None,
) -> list[float]:
    """Embed a single text via Bedrock `cohere.embed-multilingual-v3`.

    Returns a 1024-dim float list. Raises:
      * `BedrockEmbedAccessDenied` on IAM / model-access errors.
      * `BedrockEmbedError` on throttle / 5xx / dim mismatch.
    """
    if not text or not text.strip():
        raise BedrockEmbedError("empty text passed to bedrock cohere embed")
    region = (region or DEFAULT_REGION).strip() or DEFAULT_REGION
    client = _get_client(region)

    body = {
        "texts": [text],
        "input_type": _input_type_for(task_type),
        "embedding_types": ["float"],
    }

    # boto3 is sync — run in default loop executor so we don't block the
    # FastAPI event loop on a slow Bedrock call.
    import asyncio
    loop = asyncio.get_running_loop()

    def _invoke() -> dict:
        try:
            from botocore.exceptions import ClientError  # type: ignore
        except ImportError:  # pragma: no cover
            ClientError = Exception  # type: ignore
        try:
            t0 = time.perf_counter()
            resp = client.invoke_model(
                modelId=MODEL_ID,
                accept="application/json",
                contentType="application/json",
                body=json.dumps(body).encode("utf-8"),
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            payload = resp.get("body")
            raw = payload.read() if payload is not None else b""
            return {"raw": raw, "elapsed_ms": elapsed_ms}
        except ClientError as ce:
            code = (
                getattr(ce, "response", {}).get("Error", {}).get("Code", "")
                if hasattr(ce, "response") else ""
            )
            msg = str(ce)
            if code in ("AccessDeniedException", "UnauthorizedOperation") or (
                "model access" in msg.lower() and "not" in msg.lower()
            ):
                raise BedrockEmbedAccessDenied(
                    f"bedrock cohere embed: IAM/model-access denied "
                    f"({code or 'unknown'}): {msg[:200]}"
                ) from ce
            raise BedrockEmbedError(
                f"bedrock cohere embed: ClientError {code or 'unknown'}: {msg[:200]}"
            ) from ce
        except Exception as exc:
            raise BedrockEmbedError(
                f"bedrock cohere embed: unexpected error: {exc}"
            ) from exc

    out = await loop.run_in_executor(None, _invoke)
    raw = out["raw"]
    try:
        decoded = json.loads(raw or b"{}")
    except Exception as exc:
        raise BedrockEmbedError(
            f"bedrock cohere embed: invalid JSON payload: {exc}"
        ) from exc

    # Cohere v3 response shape: {"embeddings": {"float": [[...]]}}
    # or legacy {"embeddings": [[...]]}.
    embeddings = decoded.get("embeddings")
    if isinstance(embeddings, dict):
        vecs = embeddings.get("float") or embeddings.get("Float") or []
    elif isinstance(embeddings, list):
        vecs = embeddings
    else:
        vecs = []
    if not vecs or not isinstance(vecs[0], list):
        raise BedrockEmbedError("bedrock cohere embed: empty embeddings array")
    vec = [float(x) for x in vecs[0]]
    if len(vec) != EMBED_DIM:
        # Founder-lock: Pinecone is 1024-dim. A dimension drift would
        # silently corrupt the index. Fail loud (V4 §12).
        raise BedrockEmbedError(
            f"bedrock cohere embed: dim mismatch — got {len(vec)} expected {EMBED_DIM}"
        )
    return vec


__all__ = [
    "BedrockEmbedAccessDenied",
    "BedrockEmbedError",
    "DEFAULT_REGION",
    "EMBED_DIM",
    "MODEL_ID",
    "PROVIDER_NAME",
    "embed_one",
    "is_configured",
]
