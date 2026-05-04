"""email-fallback SQS consumer (Task #332).

The primary email path is Resend; on transient failure the API
producer drops the original payload onto the email-fallback SQS
queue and SES is the secondary route. This handler unpacks the
queued payload and calls SES via boto3 — the same to/subject/html
shape Resend expects so callers don't need a parallel template path.
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
        raise ValueError("email-fallback message missing 'to'")

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
