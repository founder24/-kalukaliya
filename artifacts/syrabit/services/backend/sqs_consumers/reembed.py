"""deferred-embed reembed SQS consumer (Task #489 follow-up landed in #489).

Drains `syrabit-reembed-queue` (Cloudflare-Workers-AI cache-only Option D
fallback path defined in V4 §15 + the four-cloud delegation matrix §A
"Deferred-embed replay queue" row).

Producer side: when `EMBED_DEGRADED_MODE=true`, the FastAPI ingest path
calls `sqs_fanout.enqueue("reembed", {chunk_id, text, namespace})`
instead of trying a third-party embedder. When the degraded flag is
cleared, this Lambda walks the backlog: each message is sent to
`https://embed.syrabit.ai` (Workers-AI EmbeddingGemma + Qwen3 fused →
1024-dim mean-pooled), the resulting vector is upserted into Pinecone
namespace `cached_gemma_today`, and the SQS message is deleted only on
confirmed write. On any failure the message is reported back to SQS via
ReportBatchItemFailures so it redelivers up to the queue's redrive
policy (5 attempts → DLQ).

Secrets are read from AWS Secrets Manager via the ARNs supplied by
`artifacts/syrabit/infra/aws/sqs-reembed.tf`. boto3 / httpx are
packaged in the consumer image (same pattern as `email_fallback.py`).
"""
from __future__ import annotations

import json
import os
from typing import Any

from ._common import run_batch


def _secret_value(arn: str) -> str:
    import boto3  # type: ignore — packaged in the Lambda image

    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    return sm.get_secret_value(SecretId=arn)["SecretString"]


async def _handle(body: dict[str, Any]) -> None:
    import httpx  # type: ignore — packaged in the Lambda image

    chunk_id = body.get("chunk_id")
    text = body.get("text")
    namespace = body.get("namespace") or os.environ.get("PINECONE_NAMESPACE") or "cached_gemma_today"
    if not chunk_id or not text:
        raise ValueError("reembed message missing 'chunk_id' or 'text'")

    embed_url = os.environ["EMBED_WORKER_URL"].rstrip("/")
    workers_embed_secret = _secret_value(os.environ["WORKERS_EMBED_SECRET_ARN"])
    pinecone_api_key = _secret_value(os.environ["PINECONE_API_KEY_ARN"])
    pinecone_index = os.environ["PINECONE_INDEX"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1) Fuse + mean-pool via the Workers-AI primary embedder.
        embed_resp = await client.post(
            f"{embed_url}/embed",
            headers={"X-Workers-Embed-Secret": workers_embed_secret},
            json={"texts": [text]},
        )
        embed_resp.raise_for_status()
        vector = embed_resp.json()["embeddings"][0]
        if len(vector) != 1024:
            raise ValueError(f"reembed vector dim mismatch: {len(vector)} != 1024")

        # 2) Upsert into the primary namespace. Pinecone REST is region-pinned
        #    by host; the index host comes from the index-describe done at
        #    image build time and ships in env as `PINECONE_INDEX_HOST`. If
        #    absent we let Pinecone resolve it via the control-plane (slower
        #    but functional — the alarms will fire on prolonged backlog).
        index_host = os.environ.get("PINECONE_INDEX_HOST") or f"{pinecone_index}.svc.aws-ap-south-1.pinecone.io"
        upsert_resp = await client.post(
            f"https://{index_host}/vectors/upsert",
            headers={"Api-Key": pinecone_api_key, "Content-Type": "application/json"},
            content=json.dumps(
                {
                    "namespace": namespace,
                    "vectors": [{"id": str(chunk_id), "values": vector}],
                }
            ),
        )
        upsert_resp.raise_for_status()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
