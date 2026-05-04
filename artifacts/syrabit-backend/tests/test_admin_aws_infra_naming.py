"""CI guard: admin_aws_infra._QUEUE_INVENTORY matches Terraform.

Task #332 reviewer rev #6 caught a drift between the admin card and
the Lambda function names actually created by Terraform. This test
parses ``infra/aws/sqs.tf`` for the queue map and verifies that for
every key:

    queue name    == sqs_worker_queues[<key>].aws
    consumer name == "<lz_project>-<key>-consumer"

so a future producer/consumer rename can never silently break the
``/admin/aws/workers/health`` error-rate column again.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from routes.admin_aws_infra import _QUEUE_INVENTORY  # type: ignore

# The repo root the backend tree lives under in CI is variable
# (artifacts/syrabit/ when the cron-runner image is being built,
# the backend repo root otherwise). Locate the TF file by walking up.
def _find_tf() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "infra" / "aws" / "sqs.tf"
        if candidate.exists():
            return candidate
        candidate = parent / "artifacts" / "syrabit" / "infra" / "aws" / "sqs.tf"
        if candidate.exists():
            return candidate
    pytest.skip("infra/aws/sqs.tf not present in this checkout")


# `${local.lz_project}` resolves to "syrabit" in foundations.tf. We
# repeat it here as a literal so the test does not need to parse two
# files — drift is unlikely (it's the project name) and the assert
# below would catch it.
LZ_PROJECT = "syrabit"


def _tf_queue_map() -> dict[str, str]:
    """Return {gcp_key: aws_queue_name} parsed from sqs.tf."""
    src = _find_tf().read_text(encoding="utf-8")
    m = re.search(r"sqs_worker_queues\s*=\s*\{", src)
    assert m, "Could not find `sqs_worker_queues = {` in sqs.tf"
    tail = src[m.end():]
    end = re.search(r"^\s{2}\}", tail, re.MULTILINE)
    block = tail[: end.start() if end else len(tail)]
    out: dict[str, str] = {}
    for line in block.splitlines():
        # "<key>" = { aws = "<name>", ... }
        match = re.match(
            r'^\s*"([a-z0-9-]+)"\s*=\s*\{\s*aws\s*=\s*"([^"]+)"', line
        )
        if match:
            out[match.group(1)] = match.group(2)
    return out


def test_queue_inventory_matches_terraform() -> None:
    tf_map = _tf_queue_map()
    inv_keys = set(_QUEUE_INVENTORY.keys())
    tf_keys = set(tf_map.keys())
    assert inv_keys == tf_keys, (
        f"Queue key drift between admin_aws_infra._QUEUE_INVENTORY and "
        f"infra/aws/sqs.tf — only-in-inv={sorted(inv_keys - tf_keys)}, "
        f"only-in-tf={sorted(tf_keys - inv_keys)}"
    )
    for key, info in _QUEUE_INVENTORY.items():
        assert info["queue"] == tf_map[key], (
            f"Queue name drift for {key!r}: inv={info['queue']!r} "
            f"tf={tf_map[key]!r}"
        )
        # Lambda is created via `${local.lz_project}-${each.key}-consumer`
        # in lambda-workers.tf — EXCEPT for `email-fallback` which is
        # an event source mapping onto the pre-existing
        # `aws_lambda_function.email_worker` (function_name =
        # "<project>-email-worker"), so we special-case that key.
        if key == "email-fallback":
            expected_consumer = f"{LZ_PROJECT}-email-worker"
        else:
            expected_consumer = f"{LZ_PROJECT}-{key}-consumer"
        assert info["consumer"] == expected_consumer, (
            f"Consumer name drift for {key!r}: inv={info['consumer']!r} "
            f"expected={expected_consumer!r}"
        )
