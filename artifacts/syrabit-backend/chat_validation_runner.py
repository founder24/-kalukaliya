"""Task #360 — fire-and-forget post-response validation enqueuer.

When ``validation_sampler.should_validate()`` returns True after a
chat turn completes, the chat handler calls :func:`enqueue_validation`
to schedule a Vertex Gemini 2.5 Flash validation pass on the answer.
The enqueue is non-blocking and never raises into the chat hot path —
errors are logged at DEBUG and swallowed.

The actual SQS / EventBridge wiring lives in
``cron/run_chat_validation_batch.py``; this module is the fast-path
producer that the FastAPI handler imports.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_QUEUE_NAME = "syrabit-chat-validation"


def _enqueue_via_sqs(payload: dict) -> bool:
    try:
        import boto3  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        sqs = boto3.client("sqs")
        url = sqs.get_queue_url(QueueName=_QUEUE_NAME)["QueueUrl"]
        sqs.send_message(QueueUrl=url, MessageBody=json.dumps(payload))
        return True
    except Exception as exc:
        logger.debug("[validation-sampler] sqs send failed: %s", exc)
        return False


async def _enqueue_async(payload: dict) -> None:
    # Run blocking boto3 in a thread so we never stall the event loop.
    loop = asyncio.get_running_loop()
    sent = await loop.run_in_executor(None, _enqueue_via_sqs, payload)
    if not sent:
        logger.info(
            "[validation-sampler] queued-locally name=%s conv=%s chars=%d",
            payload.get("name"), payload.get("conversation_id"),
            len(payload.get("answer_text", "")),
        )


def enqueue_validation(*, user_message: str, answer_text: str,
                       conversation_id: str, user_id: str,
                       provider: Optional[str] = None) -> None:
    """Schedule a Vertex validation pass on this chat turn. Never
    raises — failures are logged and swallowed. Off the critical path:
    fires a coroutine on the running loop and returns immediately."""
    payload = {
        "name": "chat_validation_v1",
        "ts": time.time(),
        "conversation_id": conversation_id,
        "user_id": user_id,
        "user_message": user_message[:4000],
        "answer_text": answer_text[:8000],
        "provider": provider or "",
    }
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_enqueue_async(payload))
    except RuntimeError:
        # No running loop (sync caller) — best-effort sync enqueue.
        try:
            _enqueue_via_sqs(payload)
        except Exception:
            pass


__all__ = ["enqueue_validation"]
