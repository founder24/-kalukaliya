"""SES retry-queue SQS consumer (Task #332; renamed semantically by
Task #556 retirement — 2026-05-07).

This is **not** a provider fallback. Amazon SES is the sole
transactional email path (Task #556 — no fallback, no break-glass,
V4 §12). The retry queue exists only so a transient SES error
(throttle, 5xx, network blip) on the synchronous path can be
re-driven asynchronously against the **same** SES endpoint instead
of dropping the message. Both the primary call and this consumer
talk to SES — there is no second provider involved at any point.

The legacy "email-fallback" queue + Terraform resource names are
retained because renaming SQS queues forces a destructive replace;
the operator-facing semantics live in this docstring and in
`infra/four-cloud-delegation.md` §A "Transactional email".
"""
from __future__ import annotations

import os
from typing import Any

from ._common import run_batch


async def _handle(body: dict[str, Any]) -> None:
    import boto3  # type: ignore — packaged in the Lambda image
    to = body.get("to")
    subject = body.get("subject") or ""
    html = body.get("html") or ""
    text = body.get("text") or ""
    if not to:
        raise ValueError("SES retry-queue message missing 'to'")

    region = os.environ.get("AWS_REGION", "ap-south-1")
    sender = os.environ.get("SES_SENDER", "no-reply@syrabit.ai")
    ses = boto3.client("ses", region_name=region)
    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": [to] if isinstance(to, str) else list(to)},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Html": {"Data": html, "Charset": "UTF-8"} if html else {"Data": "", "Charset": "UTF-8"},
                "Text": {"Data": text, "Charset": "UTF-8"} if text else {"Data": "", "Charset": "UTF-8"},
            },
        },
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    return run_batch(event, _handle)
