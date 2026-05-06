"""S3 → R2 nightly sync Lambda (Task #489 §D row "S3 → R2 nightly
EventBridge sync"). Promotes every object under `s3://$S3_FINALS_BUCKET/
finals/` to Cloudflare R2, deleting the S3 source only on a confirmed
R2 write.

Trigger: EventBridge Scheduler `syrabit-s3-to-r2-nightly-prod` at 02:11
UTC daily — see `artifacts/syrabit/infra/aws/s3-to-r2-sync.tf`.

R2 is reached via its S3-compatible endpoint with boto3, so no
Cloudflare SDK is needed; credentials live in Secrets Manager (sourced
from AKV per V4 §6).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("s3_to_r2_sync")
log.setLevel(logging.INFO)


def _r2_creds() -> tuple[str, str]:
    import boto3  # type: ignore — packaged in the Lambda image

    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    raw = sm.get_secret_value(SecretId=os.environ["R2_ACCESS_KEY_SECRET_ARN"])["SecretString"]
    parsed = json.loads(raw)
    return parsed["access_key_id"], parsed["secret_access_key"]


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    import boto3  # type: ignore

    s3_bucket = os.environ["S3_FINALS_BUCKET"]
    r2_bucket = os.environ["R2_FINALS_BUCKET"]
    r2_endpoint = os.environ["R2_ENDPOINT_URL"]

    s3 = boto3.client("s3")
    r2_key, r2_secret = _r2_creds()
    r2 = boto3.client(
        "s3",
        endpoint_url=r2_endpoint,
        aws_access_key_id=r2_key,
        aws_secret_access_key=r2_secret,
        region_name="auto",
    )

    promoted = 0
    failed = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=s3_bucket, Prefix="finals/"):
        for obj in page.get("Contents") or []:
            key = obj["Key"]
            try:
                body = s3.get_object(Bucket=s3_bucket, Key=key)["Body"].read()
                r2.put_object(Bucket=r2_bucket, Key=key, Body=body)
                # Verify before deleting the S3 source.
                head = r2.head_object(Bucket=r2_bucket, Key=key)
                if head["ContentLength"] != len(body):
                    raise IOError(f"R2 size mismatch for {key}")
                s3.delete_object(Bucket=s3_bucket, Key=key)
                promoted += 1
            except Exception:
                log.exception("s3-to-r2 promote failed for key=%s", key)
                failed += 1

    log.info("s3-to-r2 nightly run complete — promoted=%d failed=%d", promoted, failed)
    if failed > 0:
        # Surface the failure count to CloudWatch so the Errors alarm in
        # s3-to-r2-sync.tf catches partial-success runs too.
        raise RuntimeError(f"s3-to-r2 nightly: {failed} object(s) failed to promote")
    return {"promoted": promoted}
