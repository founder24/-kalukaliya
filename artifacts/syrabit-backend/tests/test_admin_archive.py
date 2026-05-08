"""Task #562 — unit tests for `routes/admin_archive.py`.

These tests are hermetic: they replace ``boto3`` in ``sys.modules`` with
a recording fake so no AWS credentials, network, or live S3 buckets are
touched. They lock the response shape consumed by the runbook
(`docs/infra/glacier-restore-runbook.md` §3 / §5) and the admin-only
behaviours that protect the endpoint:

  * bucket allowlist enforcement (400 on out-of-allowlist target);
  * ``Expedited`` tier explicitly rejected for DEEP_ARCHIVE;
  * one ``admin_archive_restore_log`` audit row written per request;
  * per-item failure isolation — one failing key does not abort the batch;
  * the response shape (``ok``, ``initiated``, ``failed``, ``results``,
    ``sla_hours``, ``next_step``) the runbook depends on.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ─── fake boto3 ──────────────────────────────────────────────────────────────

class _FakeS3Client:
    def __init__(self):
        self.calls: list[dict] = []
        # Optional per-key error injector — set by the test before calling
        # the route. Maps "bucket/key" → exception instance to raise.
        self.errors: dict[str, Exception] = {}

    def restore_object(self, *, Bucket, Key, RestoreRequest):
        self.calls.append({
            "Bucket": Bucket,
            "Key": Key,
            "RestoreRequest": RestoreRequest,
        })
        err = self.errors.get(f"{Bucket}/{Key}")
        if err is not None:
            raise err
        return {}


def _install_fake_boto3(client: _FakeS3Client):
    fake = types.ModuleType("boto3")

    def _client(name, **kwargs):  # noqa: ARG001
        assert name == "s3", f"unexpected boto3 client request: {name!r}"
        return client

    fake.client = _client  # type: ignore[attr-defined]
    sys.modules["boto3"] = fake
    return fake


# ─── app builder ─────────────────────────────────────────────────────────────

def _build_app(*, admin_ok: bool = True, db: object | None = None):
    """Build a FastAPI test app with the admin archive router mounted."""
    from auth_deps import get_admin_user
    from routes import admin_archive as m

    # Reset cached allowlist (env-driven) so each test starts clean.
    m._allowed_buckets = None

    if db is not None:
        m.init_admin_archive(db)
    else:
        m.init_admin_archive(None)

    app = FastAPI()
    app.include_router(m.router, prefix="/api")

    async def _ok_admin():
        return {"id": "admin-1", "email": "admin@example.com"}

    async def _deny():
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_admin_user] = _ok_admin if admin_ok else _deny
    return app


def _make_db_mock():
    """A motor-shaped async mock collection that records insert_one calls."""
    db = MagicMock()
    coll = MagicMock()
    coll.insert_one = AsyncMock(return_value=None)
    db.__getitem__ = MagicMock(return_value=coll)
    return db, coll


@pytest.fixture(autouse=True)
def _fake_aws_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    # Pin the allowlist so the test does not depend on the production
    # default in admin_archive._resolve_allowed_buckets.
    monkeypatch.setenv(
        "GLACIER_ARCHIVE_BUCKETS",
        "syrabit-content-snapshots-prod,syrabit-razorpay-receipts-prod",
    )


@pytest.fixture
def fake_s3():
    client = _FakeS3Client()
    _install_fake_boto3(client)
    yield client
    sys.modules.pop("boto3", None)


# ─── tests ───────────────────────────────────────────────────────────────────

def test_requires_admin_auth(fake_s3):
    app = _build_app(admin_ok=False)
    client = TestClient(app)
    r = client.post(
        "/api/admin/archive/restore",
        json={"items": [{"bucket": "syrabit-content-snapshots-prod", "key": "k"}]},
    )
    assert r.status_code == 401
    # Dep failed before any boto3 call.
    assert fake_s3.calls == []


def test_bucket_allowlist_enforced(fake_s3):
    db, _coll = _make_db_mock()
    app = _build_app(db=db)
    client = TestClient(app)
    r = client.post(
        "/api/admin/archive/restore",
        json={
            "items": [
                {"bucket": "syrabit-content-snapshots-prod", "key": "ok.txt"},
                {"bucket": "some-random-bucket", "key": "evil.txt"},
            ]
        },
    )
    assert r.status_code == 400
    assert "archive allowlist" in r.json()["detail"]
    # Whole batch rejected — no S3 calls were made.
    assert fake_s3.calls == []


def test_expedited_tier_rejected(fake_s3):
    app = _build_app()
    client = TestClient(app)
    r = client.post(
        "/api/admin/archive/restore",
        json={
            "items": [{"bucket": "syrabit-content-snapshots-prod", "key": "k"}],
            "tier": "Expedited",
        },
    )
    assert r.status_code == 400
    assert "Expedited" in r.json()["detail"]
    assert fake_s3.calls == []


def test_unknown_tier_rejected(fake_s3):
    app = _build_app()
    client = TestClient(app)
    r = client.post(
        "/api/admin/archive/restore",
        json={
            "items": [{"bucket": "syrabit-content-snapshots-prod", "key": "k"}],
            "tier": "Lightning",
        },
    )
    assert r.status_code == 400


def test_happy_path_response_shape_and_audit_row(fake_s3):
    db, coll = _make_db_mock()
    app = _build_app(db=db)
    client = TestClient(app)

    r = client.post(
        "/api/admin/archive/restore",
        json={
            "items": [
                {"bucket": "syrabit-content-snapshots-prod", "key": "a.txt"},
                {"bucket": "syrabit-content-snapshots-prod", "key": "b.txt"},
            ],
            "tier": "Standard",
            "days_available": 7,
        },
    )
    assert r.status_code == 200
    body = r.json()

    # Shape locked by docs/infra/glacier-restore-runbook.md §3.
    for key in ("ok", "initiated", "failed", "results", "sla_hours", "next_step"):
        assert key in body, f"missing key {key!r} in response"
    assert body["ok"] is True
    assert body["initiated"] == 2
    assert body["failed"] == 0
    assert body["sla_hours"] == 12
    assert "HeadObject" in body["next_step"]
    for row in body["results"]:
        assert row["status"] == "restore_initiated"
        assert row["tier"] == "Standard"
        assert row["available_for_days"] == 7

    # Both restore_object calls actually issued.
    assert len(fake_s3.calls) == 2
    assert {c["Key"] for c in fake_s3.calls} == {"a.txt", "b.txt"}
    for c in fake_s3.calls:
        assert c["RestoreRequest"]["Days"] == 7
        assert c["RestoreRequest"]["GlacierJobParameters"]["Tier"] == "Standard"

    # Exactly one audit row written.
    assert coll.insert_one.await_count == 1
    audit = coll.insert_one.await_args.args[0]
    assert audit["admin_email"] == "admin@example.com"
    assert audit["tier"] == "Standard"
    assert audit["days_available"] == 7
    assert len(audit["items"]) == 2
    assert len(audit["results"]) == 2


def test_per_item_failure_isolation(fake_s3):
    """One failing key must not abort the whole batch."""
    db, coll = _make_db_mock()
    app = _build_app(db=db)

    # Inject a synthetic boto3 error on the second key only.
    fake_s3.errors["syrabit-content-snapshots-prod/missing.txt"] = RuntimeError(
        "NoSuchKey"
    )

    client = TestClient(app)
    r = client.post(
        "/api/admin/archive/restore",
        json={
            "items": [
                {"bucket": "syrabit-content-snapshots-prod", "key": "good.txt"},
                {"bucket": "syrabit-content-snapshots-prod", "key": "missing.txt"},
                {"bucket": "syrabit-content-snapshots-prod", "key": "also-good.txt"},
            ]
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["initiated"] == 2
    assert body["failed"] == 1
    statuses = [row["status"] for row in body["results"]]
    assert statuses == ["restore_initiated", "error", "restore_initiated"]
    # The failing row carries an error code + a truncated detail.
    err_row = body["results"][1]
    assert err_row["error"] == "RuntimeError"
    assert "NoSuchKey" in err_row["detail"]

    # Audit row still written exactly once for the whole batch.
    assert coll.insert_one.await_count == 1


def test_bulk_tier_reports_48h_sla(fake_s3):
    app = _build_app()
    client = TestClient(app)
    r = client.post(
        "/api/admin/archive/restore",
        json={
            "items": [{"bucket": "syrabit-content-snapshots-prod", "key": "k"}],
            "tier": "Bulk",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sla_hours"] == 48
    assert fake_s3.calls[0]["RestoreRequest"]["GlacierJobParameters"]["Tier"] == "Bulk"


def test_audit_log_failure_does_not_break_response(fake_s3):
    """A Mongo write failure must not surface as a 5xx — the restore
    request itself succeeded on the AWS side, the audit row is best-effort.
    """
    db = MagicMock()
    coll = MagicMock()
    coll.insert_one = AsyncMock(side_effect=RuntimeError("mongo down"))
    db.__getitem__ = MagicMock(return_value=coll)

    app = _build_app(db=db)
    client = TestClient(app)
    r = client.post(
        "/api/admin/archive/restore",
        json={"items": [{"bucket": "syrabit-content-snapshots-prod", "key": "k"}]},
    )
    assert r.status_code == 200
    assert r.json()["initiated"] == 1
