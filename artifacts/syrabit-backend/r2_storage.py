"""
Cloudflare R2 Object Storage — async helpers.

R2 is S3-compatible, so we use boto3 with a custom endpoint URL.
All boto3 operations are synchronous; they run in a thread-pool executor
so they don't block the FastAPI event loop.

Required env vars:
  R2_ACCESS_KEY_ID      — from CF Dashboard → R2 → Manage R2 API Tokens
  R2_SECRET_ACCESS_KEY  — paired secret
  R2_BUCKET_NAME        — bucket (default: syrabit-media)
  CF_AI_GATEWAY_ACCOUNT_ID — CF account ID (auto-builds endpoint URL)
  R2_PUBLIC_URL         — optional public-read URL base (e.g. https://media.syrabit.ai)

Usage:
  from r2_storage import r2_upload, r2_delete, r2_presign, r2_public_url

  url = await r2_upload(key="images/abc.png", data=raw_bytes, content_type="image/png")
  await r2_delete("images/abc.png")
  signed = await r2_presign("pdfs/doc.pdf", expires=3600)
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import time
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


# ── lazy boto3 client ─────────────────────────────────────────────────────────

def _live_key() -> str:
    return os.environ.get("R2_ACCESS_KEY_ID", "").strip()

def _live_secret() -> str:
    return os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()

def _live_endpoint() -> str:
    explicit = os.environ.get("R2_ENDPOINT_URL", "").strip()
    if explicit:
        return explicit
    acct = os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
    return f"https://{acct}.r2.cloudflarestorage.com" if acct else ""

def _live_bucket() -> str:
    return os.environ.get("R2_BUCKET_NAME", "syrabit-media").strip()

def _live_public_url() -> str:
    return os.environ.get("R2_PUBLIC_URL", "").strip().rstrip("/")


@lru_cache(maxsize=1)
def _get_r2_client():
    """Return a cached boto3 S3 client pointed at R2. Raises if not configured."""
    key = _live_key()
    secret = _live_secret()
    endpoint = _live_endpoint()
    if not (key and secret and endpoint):
        raise RuntimeError(
            "R2 not configured — set R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, "
            "and CF_AI_GATEWAY_ACCOUNT_ID (or R2_ENDPOINT_URL)"
        )
    import boto3
    from botocore.config import Config
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(
            retries={"max_attempts": 3, "mode": "adaptive"},
            connect_timeout=10,
            read_timeout=60,
        ),
    )
    return client


def _is_r2_ready() -> bool:
    """Read env vars live so monkeypatching works in tests."""
    return bool(_live_key() and _live_secret() and _live_endpoint())


# ── ensure bucket exists ──────────────────────────────────────────────────────

_bucket_ensured = False

def _ensure_bucket_sync(bucket: str) -> None:
    global _bucket_ensured
    if _bucket_ensured:
        return
    client = _get_r2_client()
    try:
        client.head_bucket(Bucket=bucket)
        _bucket_ensured = True
    except Exception:
        try:
            client.create_bucket(Bucket=bucket)
            logger.info(f"r2: created bucket '{bucket}'")
            _bucket_ensured = True
        except Exception as exc:
            if "BucketAlreadyOwnedByYou" in str(exc) or "BucketAlreadyExists" in str(exc):
                _bucket_ensured = True
            else:
                raise


async def ensure_bucket() -> None:
    """Create the R2 bucket if it doesn't exist yet."""
    if not _is_r2_ready():
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ensure_bucket_sync, _live_bucket())


# ── upload ────────────────────────────────────────────────────────────────────

def _upload_sync(key: str, data: bytes, content_type: str, bucket: str,
                  cache_control: str, metadata: dict) -> str:
    _ensure_bucket_sync(bucket)
    client = _get_r2_client()
    extra: dict = {"ContentType": content_type}
    if cache_control:
        extra["CacheControl"] = cache_control
    if metadata:
        extra["Metadata"] = {k: str(v) for k, v in metadata.items()}
    client.put_object(Bucket=bucket, Key=key, Body=data, **extra)
    return key


async def r2_upload(
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    cache_control: str = "public, max-age=31536000, immutable",
    metadata: Optional[dict] = None,
    bucket: Optional[str] = None,
) -> str:
    """
    Upload ``data`` to R2 at ``key``.
    Returns the public URL if R2_PUBLIC_URL is set, otherwise the key.
    Raises RuntimeError if R2 is not configured.
    """
    _bucket = bucket or _live_bucket()
    loop = asyncio.get_event_loop()
    t0 = time.monotonic()
    await loop.run_in_executor(
        None,
        _upload_sync,
        key, data, content_type, _bucket, cache_control, metadata or {},
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    logger.info(f"r2: uploaded {key} ({len(data)} bytes) in {elapsed}ms")
    return r2_public_url(key, bucket=_bucket)


# ── delete ────────────────────────────────────────────────────────────────────

def _delete_sync(key: str, bucket: str) -> None:
    client = _get_r2_client()
    client.delete_object(Bucket=bucket, Key=key)


async def r2_delete(key: str, bucket: Optional[str] = None) -> None:
    """Delete an object from R2. Silent no-op if key doesn't exist."""
    if not _is_r2_ready():
        return
    _bucket = bucket or _live_bucket()
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _delete_sync, key, _bucket)
        logger.info(f"r2: deleted {key}")
    except Exception as exc:
        logger.warning(f"r2: delete failed for {key}: {exc}")


# ── presigned URL ─────────────────────────────────────────────────────────────

def _presign_sync(key: str, bucket: str, expires: int) -> str:
    client = _get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


async def r2_presign(key: str, expires: int = 3600, bucket: Optional[str] = None) -> str:
    """Generate a time-limited presigned GET URL (for private objects)."""
    _bucket = bucket or _live_bucket()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _presign_sync, key, _bucket, expires)


# ── primary read URL helper ───────────────────────────────────────────────────

def r2_primary_read_url(key: str, *, s3_fallback_url: str = "",
                        bucket: Optional[str] = None) -> str:
    """Task #383 — return the canonical read URL for ``key`` honouring
    the ``R2_PRIMARY_ON`` flag.

    When R2 is the primary store, callers should serve assets from R2
    directly so we stop paying S3 egress. The function is dependency-
    injected for tests via ``s3_fallback_url`` so we don't have to
    rewrite every existing chapter-PDF / audio caller in one shot:

      * If R2 is configured **and** ``R2_PRIMARY_ON`` is on → R2 URL.
      * Otherwise → ``s3_fallback_url`` (typically the existing
        ``s3_public_url(...)`` value), or empty string when no
        fallback was supplied.

    Centralising the choice here means a future swap (R2 → another
    store) only changes one helper instead of every call site.
    """
    try:
        from config import R2_PRIMARY_ON as _R2_PRIMARY_ON
    except Exception:
        _R2_PRIMARY_ON = False
    if _R2_PRIMARY_ON and _is_r2_ready():
        return r2_public_url(key, bucket=bucket)
    if s3_fallback_url:
        return s3_fallback_url
    return r2_public_url(key, bucket=bucket) if _is_r2_ready() else ""


# ── public URL helper ─────────────────────────────────────────────────────────

def r2_public_url(key: str, bucket: Optional[str] = None) -> str:
    """
    Return the public-facing URL for an R2 object.
    Uses R2_PUBLIC_URL if set (e.g. https://media.syrabit.ai),
    otherwise falls back to the R2 endpoint URL pattern.
    """
    _bucket = bucket or _live_bucket()
    pub = _live_public_url()
    if pub:
        return f"{pub}/{key}"
    endpoint = _live_endpoint()
    if endpoint:
        return f"{endpoint}/{_bucket}/{key}"
    return key


# ── list objects ──────────────────────────────────────────────────────────────

def _list_sync(prefix: str, bucket: str, max_keys: int) -> list[dict]:
    client = _get_r2_client()
    resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
    return [
        {
            "key": obj["Key"],
            "size": obj["Size"],
            "last_modified": obj["LastModified"].isoformat(),
            "url": r2_public_url(obj["Key"], bucket),
        }
        for obj in resp.get("Contents", [])
    ]


async def r2_list(prefix: str = "", max_keys: int = 1000, bucket: Optional[str] = None) -> list[dict]:
    """List objects in R2 under a prefix. Returns list of {key, size, last_modified, url}."""
    if not _is_r2_ready():
        return []
    _bucket = bucket or _live_bucket()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _list_sync, prefix, _bucket, max_keys)


# ── download ──────────────────────────────────────────────────────────────────

def _download_sync(key: str, bucket: str) -> bytes:
    client = _get_r2_client()
    resp = client.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


async def r2_download(key: str, bucket: Optional[str] = None) -> bytes:
    """Download and return the raw bytes of an R2 object."""
    _bucket = bucket or _live_bucket()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_sync, key, _bucket)


# ── key helpers ───────────────────────────────────────────────────────────────

def make_key(folder: str, filename: str) -> str:
    """Sanitize filename and build a deterministic key under ``folder``."""
    import uuid, re
    safe = re.sub(r"[^\w.\-]", "_", filename)
    uid = uuid.uuid4().hex[:8]
    return f"{folder}/{uid}_{safe}"


def content_type_for(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


# ── health check ──────────────────────────────────────────────────────────────

async def r2_health() -> dict:
    """Quick connectivity check — list up to 1 object."""
    if not _is_r2_ready():
        return {"ok": False, "reason": "R2 not configured"}
    try:
        items = await r2_list(max_keys=1)
        return {
            "ok": True,
            "bucket": _live_bucket(),
            "endpoint": _live_endpoint(),
            "public_url": _live_public_url() or "(none — using endpoint URL)",
            "sample_count": len(items),
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
