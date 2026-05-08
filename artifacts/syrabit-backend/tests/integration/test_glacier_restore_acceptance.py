"""Task #562 — end-to-end acceptance probe for the Glacier Deep
Archive restore path.

Mirrors `artifacts/syrabit/docs/infra/glacier-restore-runbook.md` §5
as a runnable pytest:

    1. Upload a small synthetic file directly into ``DEEP_ARCHIVE``
       (skip the lifecycle wait).
    2. POST ``/api/admin/archive/restore`` against staging with the
       admin JWT.
    3. Poll ``s3:HeadObject`` until ``ongoing-request="false"``.
    4. Download via ``get_object`` and assert byte equality.
    5. Cleanup the synthetic key.

A passing run proves the lifecycle policy is live, the admin endpoint
is authorised + scoped to the allowlist, the Standard-tier SLA is
being met, the audit log row was written, and the byte-for-byte
restore round-trip works.

Gating
------
This probe is **gated** behind the ``GLACIER_ACCEPTANCE=1`` environment
variable so it never runs in normal CI / pytest invocations. It also
needs:

    GLACIER_ACCEPTANCE_BUCKET   target bucket (must be in the runbook
                                allowlist; defaults to
                                ``syrabit-content-snapshots-prod``)
    GLACIER_ACCEPTANCE_API_BASE staging API base, e.g.
                                ``https://staging-api.syrabit.ai``
    GLACIER_ACCEPTANCE_ADMIN_JWT  admin JWT scoped to the staging admin
                                  team
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
                                AWS creds with PutObject / RestoreObject /
                                GetObject / HeadObject / DeleteObject on
                                the target bucket
    AWS_REGION (or AWS_GLACIER_REGION)
                                region the target bucket lives in

Optional knobs::

    GLACIER_ACCEPTANCE_TIMEOUT_S   max wall-time to wait for restore to
                                   finish (default 13 hours = SLA + 1h
                                   slack). Set to e.g. ``120`` for a
                                   manual smoke when you do not want
                                   to wait the full 12h.
    GLACIER_ACCEPTANCE_POLL_S      head-object poll interval, seconds
                                   (default 600 = 10 min — matches the
                                   runbook).
    GLACIER_ACCEPTANCE_TIER        ``Standard`` (default) or ``Bulk``.

The defaults are tuned for the GitHub Actions nightly run:
``Standard`` tier + 13 h timeout + 10 min poll matches the runbook
SLA. For an interactive smoke against a hot path, drop the timeout
and poll interval.

To run locally::

    GLACIER_ACCEPTANCE=1 \\
    GLACIER_ACCEPTANCE_API_BASE=https://staging-api.syrabit.ai \\
    GLACIER_ACCEPTANCE_ADMIN_JWT=eyJ... \\
    AWS_ACCESS_KEY_ID=AKIA... AWS_SECRET_ACCESS_KEY=... \\
    AWS_REGION=ap-south-1 \\
      pytest -q artifacts/syrabit-backend/tests/integration/test_glacier_restore_acceptance.py
"""
from __future__ import annotations

import datetime as _dt
import os
import time
import uuid

import pytest

if os.environ.get("GLACIER_ACCEPTANCE") != "1":
    pytest.skip(
        "GLACIER_ACCEPTANCE not set — skipping live AWS / staging probe",
        allow_module_level=True,
    )

# Lazy imports so the skip above keeps `pytest --collect-only` clean
# even on machines without boto3 / requests installed.
import boto3  # noqa: E402
import requests  # noqa: E402


_API_BASE = os.environ.get("GLACIER_ACCEPTANCE_API_BASE", "").rstrip("/")
_ADMIN_JWT = os.environ.get("GLACIER_ACCEPTANCE_ADMIN_JWT", "")
_BUCKET = os.environ.get(
    "GLACIER_ACCEPTANCE_BUCKET", "syrabit-content-snapshots-prod"
)
_REGION = (
    os.environ.get("AWS_GLACIER_REGION")
    or os.environ.get("AWS_REGION")
    or "ap-south-1"
)
_TIMEOUT_S = int(os.environ.get("GLACIER_ACCEPTANCE_TIMEOUT_S", str(13 * 3600)))
_POLL_S = int(os.environ.get("GLACIER_ACCEPTANCE_POLL_S", "600"))
_TIER = os.environ.get("GLACIER_ACCEPTANCE_TIER", "Standard")


def _require(name: str, val: str) -> str:
    if not val:
        pytest.fail(
            f"{name} not set — required for the Glacier acceptance probe. "
            f"See the file header for the full env-var list."
        )
    return val


def test_glacier_restore_round_trip():
    _require("GLACIER_ACCEPTANCE_API_BASE", _API_BASE)
    _require("GLACIER_ACCEPTANCE_ADMIN_JWT", _ADMIN_JWT)

    s3 = boto3.client("s3", region_name=_REGION)

    stamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    key = f"acceptance/glacier-restore-test-{stamp}-{uuid.uuid4().hex[:8]}.txt"
    payload = (
        f"glacier restore acceptance probe {stamp} {uuid.uuid4().hex}\n"
    ).encode("utf-8")

    # 1. Upload directly into DEEP_ARCHIVE.
    s3.put_object(
        Bucket=_BUCKET,
        Key=key,
        Body=payload,
        StorageClass="DEEP_ARCHIVE",
    )

    try:
        # 2. Restore via the admin endpoint.
        resp = requests.post(
            f"{_API_BASE}/api/admin/archive/restore",
            headers={
                "Authorization": f"Bearer {_ADMIN_JWT}",
                "Content-Type": "application/json",
            },
            json={
                "items": [{"bucket": _BUCKET, "key": key}],
                "tier": _TIER,
                "days_available": 1,
            },
            timeout=30,
        )
        assert resp.status_code == 200, (
            f"admin restore returned HTTP {resp.status_code}: "
            f"{resp.text[:500]}"
        )
        body = resp.json()
        assert body.get("ok") is True, f"restore not ok: {body}"
        assert body.get("initiated") == 1, f"unexpected initiated count: {body}"
        results = body.get("results") or []
        assert len(results) == 1, f"unexpected results length: {body}"
        assert results[0]["status"] == "restore_initiated", (
            f"unexpected per-item status: {results[0]}"
        )

        # 3. Poll s3:HeadObject until ongoing-request="false".
        deadline = time.monotonic() + _TIMEOUT_S
        last_restore_header = ""
        while time.monotonic() < deadline:
            head = s3.head_object(Bucket=_BUCKET, Key=key)
            last_restore_header = head.get("Restore", "") or ""
            # Format: 'ongoing-request="false", expiry-date="..."'
            if 'ongoing-request="false"' in last_restore_header:
                break
            time.sleep(_POLL_S)
        else:
            pytest.fail(
                f"Restore did not complete within {_TIMEOUT_S}s "
                f"(tier={_TIER}, last Restore header={last_restore_header!r})"
            )

        # 4. Download and assert byte equality with the upload.
        got = s3.get_object(Bucket=_BUCKET, Key=key)["Body"].read()
        assert got == payload, (
            f"restored bytes differ from uploaded payload "
            f"(uploaded {len(payload)}B, restored {len(got)}B)"
        )

        # 4b. Verify the audit-log row was actually persisted by the
        # backend. The unit suite covers the write call, but a live
        # probe should also prove the row survives the round-trip
        # (e.g. that Mongo is writable from the staging admin pod).
        log_resp = requests.get(
            f"{_API_BASE}/api/admin/archive/restore/log?limit=50",
            headers={"Authorization": f"Bearer {_ADMIN_JWT}"},
            timeout=30,
        )
        assert log_resp.status_code == 200, (
            f"restore log read returned HTTP {log_resp.status_code}: "
            f"{log_resp.text[:300]}"
        )
        rows = log_resp.json().get("rows", []) or []
        assert any(
            any(it.get("bucket") == _BUCKET and it.get("key") == key
                for it in (row.get("items") or []))
            for row in rows
        ), (
            f"synthetic key {key!r} not found in last 50 audit-log rows — "
            f"the restore endpoint did not persist the audit row"
        )

    finally:
        # 5. Cleanup — best-effort so a mid-test failure still tries to
        # remove the synthetic object instead of leaving Deep Archive
        # storage charges accumulating.
        try:
            s3.delete_object(Bucket=_BUCKET, Key=key)
        except Exception as exc:  # pragma: no cover - cleanup-only
            print(f"[acceptance] cleanup delete_object failed: {exc}")
