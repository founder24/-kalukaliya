"""
Tests for r2_storage.py — Cloudflare R2 Object Storage.

All boto3 S3 calls are monkeypatched.  No live network traffic.
"""
from __future__ import annotations

import asyncio
import io
import pytest


def _run(coro):
    # Task #284 — fresh loop per call (Python 3.11+ no longer auto-creates
    # a main-thread loop on demand).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def r2_env(monkeypatch):
    """Inject R2 env vars so R2_ENABLED=True."""
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-r2-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-r2-secret")
    monkeypatch.setenv("CF_AI_GATEWAY_ACCOUNT_ID", "acct-abc123")
    monkeypatch.setenv("R2_BUCKET_NAME", "syrabit-media-test")
    monkeypatch.delenv("R2_PUBLIC_URL", raising=False)


@pytest.fixture(autouse=True)
def reset_r2_state():
    """Reset module-level cache and singleton between tests."""
    import r2_storage
    _try_clear_cache(r2_storage)
    r2_storage._bucket_ensured = False
    yield
    _try_clear_cache(r2_storage)
    r2_storage._bucket_ensured = False


def _try_clear_cache(module):
    """Clear lru_cache if present (may be replaced by monkeypatch)."""
    try:
        module._get_r2_client.cache_clear()
    except AttributeError:
        pass  # monkeypatched to a plain function — no cache to clear


# ─── fake boto3 S3 client ─────────────────────────────────────────────────────

class FakeS3Client:
    """Minimal stub that records calls for assertion."""

    def __init__(self):
        self.calls: list[dict] = []
        self._objects: dict[str, bytes] = {}

    def head_bucket(self, *, Bucket):
        self.calls.append({"op": "head_bucket", "Bucket": Bucket})

    def create_bucket(self, *, Bucket):
        self.calls.append({"op": "create_bucket", "Bucket": Bucket})

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.calls.append({"op": "put_object", "Bucket": Bucket, "Key": Key, "kwargs": kwargs})
        self._objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        self.calls.append({"op": "get_object", "Bucket": Bucket, "Key": Key})
        data = self._objects.get(Key, b"test-data")
        return {"Body": io.BytesIO(data)}

    def delete_object(self, *, Bucket, Key):
        self.calls.append({"op": "delete_object", "Bucket": Bucket, "Key": Key})
        self._objects.pop(Key, None)

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        self.calls.append({"op": "presign", "Params": Params, "ExpiresIn": ExpiresIn})
        return f"https://presigned.r2.dev/{Params['Key']}?exp={ExpiresIn}"

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys):
        self.calls.append({"op": "list_objects_v2", "Prefix": Prefix})
        return {
            "Contents": [
                {"Key": f"{Prefix}file1.png", "Size": 1024, "LastModified": __import__("datetime").datetime.utcnow()},
                {"Key": f"{Prefix}file2.pdf", "Size": 2048, "LastModified": __import__("datetime").datetime.utcnow()},
            ]
        }


@pytest.fixture()
def fake_s3(monkeypatch):
    client = FakeS3Client()

    def _get_fake_client():
        return client

    import r2_storage
    monkeypatch.setattr(r2_storage, "_get_r2_client", _get_fake_client)
    r2_storage._bucket_ensured = True  # skip bucket creation in most tests
    return client


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Config / is_ready
# ═══════════════════════════════════════════════════════════════════════════════

def test_r2_enabled_with_env(r2_env):
    from config import R2_ENABLED, R2_ENDPOINT_URL, R2_BUCKET_NAME
    assert R2_ENABLED is True
    assert "acct-abc123" in R2_ENDPOINT_URL
    assert R2_BUCKET_NAME == "syrabit-media-test"


def test_r2_disabled_without_key(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    assert cfg_module.R2_ENABLED is False


def test_r2_endpoint_derived_from_account_id(r2_env):
    from config import R2_ENDPOINT_URL
    assert R2_ENDPOINT_URL == "https://acct-abc123.r2.cloudflarestorage.com"


def test_r2_endpoint_override(monkeypatch, r2_env):
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://custom-endpoint.example.com")
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    assert cfg_module.R2_ENDPOINT_URL == "https://custom-endpoint.example.com"


def test_is_r2_ready_true(r2_env):
    import r2_storage
    assert r2_storage._is_r2_ready() is True


def test_is_r2_ready_false(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    assert r2_storage._is_r2_ready() is False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Upload
# ═══════════════════════════════════════════════════════════════════════════════

def test_r2_upload_calls_put_object(fake_s3, r2_env):
    import r2_storage
    raw = b"hello world"
    url = _run(r2_storage.r2_upload("images/test.png", raw, content_type="image/png"))
    assert any(c["op"] == "put_object" for c in fake_s3.calls)
    put = next(c for c in fake_s3.calls if c["op"] == "put_object")
    assert put["Key"] == "images/test.png"
    assert put["Bucket"] == "syrabit-media-test"


def test_r2_upload_returns_url(fake_s3, r2_env):
    import r2_storage
    url = _run(r2_storage.r2_upload("images/test.png", b"data"))
    assert "images/test.png" in url


def test_r2_upload_returns_public_url_when_configured(monkeypatch, fake_s3, r2_env):
    monkeypatch.setenv("R2_PUBLIC_URL", "https://media.syrabit.ai")
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    url = _run(r2_storage.r2_upload("img/a.png", b"px"))
    assert url == "https://media.syrabit.ai/img/a.png"


def test_r2_upload_cache_control_passed(fake_s3, r2_env):
    import r2_storage
    _run(r2_storage.r2_upload("f.png", b"d", cache_control="no-cache"))
    put = next(c for c in fake_s3.calls if c["op"] == "put_object")
    assert put["kwargs"].get("CacheControl") == "no-cache"


def test_r2_upload_metadata_passed(fake_s3, r2_env):
    import r2_storage
    _run(r2_storage.r2_upload("f.png", b"d", metadata={"subject": "physics", "year": "2025"}))
    put = next(c for c in fake_s3.calls if c["op"] == "put_object")
    assert put["kwargs"]["Metadata"]["subject"] == "physics"


def test_r2_upload_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    with pytest.raises(RuntimeError, match="R2 not configured"):
        _run(r2_storage.r2_upload("key", b"data"))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Download
# ═══════════════════════════════════════════════════════════════════════════════

def test_r2_download_calls_get_object(fake_s3, r2_env):
    import r2_storage
    data = _run(r2_storage.r2_download("images/test.png"))
    assert any(c["op"] == "get_object" for c in fake_s3.calls)
    assert isinstance(data, bytes)


def test_r2_download_returns_correct_data(fake_s3, r2_env):
    import r2_storage
    # Pre-store data via upload
    _run(r2_storage.r2_upload("round_trip.txt", b"round-trip-content"))
    data = _run(r2_storage.r2_download("round_trip.txt"))
    assert data == b"round-trip-content"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Delete
# ═══════════════════════════════════════════════════════════════════════════════

def test_r2_delete_calls_delete_object(fake_s3, r2_env):
    import r2_storage
    _run(r2_storage.r2_delete("images/old.png"))
    assert any(c["op"] == "delete_object" for c in fake_s3.calls)
    delete = next(c for c in fake_s3.calls if c["op"] == "delete_object")
    assert delete["Key"] == "images/old.png"


def test_r2_delete_silent_when_not_ready(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    # Should not raise
    _run(r2_storage.r2_delete("nonexistent.png"))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Presigned URL
# ═══════════════════════════════════════════════════════════════════════════════

def test_r2_presign_returns_url(fake_s3, r2_env):
    import r2_storage
    url = _run(r2_storage.r2_presign("pdfs/doc.pdf", expires=1800))
    assert "doc.pdf" in url
    assert "1800" in url


def test_r2_presign_uses_correct_expiry(fake_s3, r2_env):
    import r2_storage
    _run(r2_storage.r2_presign("f.pdf", expires=7200))
    presign = next(c for c in fake_s3.calls if c["op"] == "presign")
    assert presign["ExpiresIn"] == 7200


def test_r2_presign_correct_key(fake_s3, r2_env):
    import r2_storage
    _run(r2_storage.r2_presign("uploads/pdfs/exam.pdf"))
    presign = next(c for c in fake_s3.calls if c["op"] == "presign")
    assert presign["Params"]["Key"] == "uploads/pdfs/exam.pdf"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. List
# ═══════════════════════════════════════════════════════════════════════════════

def test_r2_list_returns_items(fake_s3, r2_env):
    import r2_storage
    items = _run(r2_storage.r2_list(prefix="images/"))
    assert len(items) == 2
    assert all("key" in i for i in items)
    assert all("size" in i for i in items)
    assert all("url" in i for i in items)


def test_r2_list_empty_when_not_ready(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    items = _run(r2_storage.r2_list())
    assert items == []


def test_r2_list_item_url_format(fake_s3, r2_env):
    import r2_storage
    items = _run(r2_storage.r2_list(prefix="content-images/"))
    for item in items:
        assert "content-images/" in item["key"]
        assert "content-images/" in item["url"]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Public URL helper
# ═══════════════════════════════════════════════════════════════════════════════

def test_public_url_with_public_url_env(monkeypatch, r2_env):
    monkeypatch.setenv("R2_PUBLIC_URL", "https://media.syrabit.ai")
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    url = r2_storage.r2_public_url("images/hero.jpg")
    assert url == "https://media.syrabit.ai/images/hero.jpg"


def test_public_url_falls_back_to_endpoint(r2_env):
    import r2_storage
    url = r2_storage.r2_public_url("images/hero.jpg")
    assert "acct-abc123.r2.cloudflarestorage.com" in url
    assert "images/hero.jpg" in url


def test_public_url_different_bucket(monkeypatch, r2_env):
    monkeypatch.setenv("R2_PUBLIC_URL", "https://cdn.syrabit.ai")
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    url = r2_storage.r2_public_url("docs/guide.pdf", bucket="syrabit-docs")
    assert url == "https://cdn.syrabit.ai/docs/guide.pdf"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Key helpers
# ═══════════════════════════════════════════════════════════════════════════════

def test_make_key_sanitizes_filename():
    import r2_storage
    key = r2_storage.make_key("uploads", "my file (1).pdf")
    assert " " not in key
    assert "(" not in key
    assert key.startswith("uploads/")
    assert key.endswith(".pdf")


def test_make_key_unique_per_call():
    import r2_storage
    k1 = r2_storage.make_key("folder", "file.png")
    k2 = r2_storage.make_key("folder", "file.png")
    assert k1 != k2  # UUID hex prefix differs


def test_content_type_for_known_types():
    import r2_storage
    assert r2_storage.content_type_for("photo.jpg") == "image/jpeg"
    assert r2_storage.content_type_for("doc.pdf") == "application/pdf"
    assert r2_storage.content_type_for("data.json") == "application/json"


def test_content_type_for_unknown_falls_back():
    import r2_storage
    ct = r2_storage.content_type_for("file.xyz123unknown")
    assert ct == "application/octet-stream"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Health check
# ═══════════════════════════════════════════════════════════════════════════════

def test_r2_health_when_not_configured(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    result = _run(r2_storage.r2_health())
    assert result["ok"] is False
    assert "not configured" in result["reason"]


def test_r2_health_ok(fake_s3, r2_env):
    import r2_storage
    result = _run(r2_storage.r2_health())
    assert result["ok"] is True
    assert result["bucket"] == "syrabit-media-test"
    assert "r2.cloudflarestorage.com" in result["endpoint"]


def test_r2_health_contains_sample_count(fake_s3, r2_env):
    import r2_storage
    result = _run(r2_storage.r2_health())
    assert "sample_count" in result
    assert isinstance(result["sample_count"], int)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Bucket ensure
# ═══════════════════════════════════════════════════════════════════════════════

def test_ensure_bucket_creates_if_missing(monkeypatch, r2_env):
    """When head_bucket raises, create_bucket should be called."""
    import r2_storage

    class S3NoHead(FakeS3Client):
        def head_bucket(self, *, Bucket):
            raise Exception("NoSuchBucket")

    client = S3NoHead()
    monkeypatch.setattr(r2_storage, "_get_r2_client", lambda: client)
    r2_storage._bucket_ensured = False

    _run(r2_storage.ensure_bucket())
    assert any(c["op"] == "create_bucket" for c in client.calls)
    assert r2_storage._bucket_ensured is True


def test_ensure_bucket_skips_when_already_ensured(monkeypatch, r2_env):
    import r2_storage

    client = FakeS3Client()
    monkeypatch.setattr(r2_storage, "_get_r2_client", lambda: client)
    r2_storage._bucket_ensured = True  # already done

    _run(r2_storage.ensure_bucket())
    assert not any(c["op"] in ("head_bucket", "create_bucket") for c in client.calls)


def test_ensure_bucket_silent_when_not_ready(monkeypatch):
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    import importlib, config as cfg_module
    importlib.reload(cfg_module)
    import r2_storage
    # Should complete without error
    _run(r2_storage.ensure_bucket())
