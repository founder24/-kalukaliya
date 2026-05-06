"""Syrabit.ai — Shared mutable state, client initialization, and core dependencies."""
import logging, asyncio, time as _time_mod, httpx, contextvars
from typing import Optional, Any

__all__ = [
    "db", "mongo_client", "redis_client", "supa", "pg_pool", "pwd_ctx", "security",
    "sarvam_llm_client", "sarvam_llm_client_direct",
    "logger",
    "is_mongo_available", "mark_mongo_down",
    "_cms_request_ctx", "_assert_not_cms_context", "_init_pg_pool",
    "_create_supa", "_rate_cleanup_task",
    "_sarvam_headers", "_sarvam_timeout", "_sarvam_llm_timeout", "_sarvam_pool_limits",
]
try:
    import asyncpg as _asyncpg
except ImportError:
    _asyncpg = None
try:
    from supabase import create_client as _create_supa
except ImportError:
    _create_supa = None
from fastapi import HTTPException
from passlib.context import CryptContext
from fastapi.security import HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from config import (
    MONGO_URL, DB_NAME, SARVAM_API_KEY, SARVAM_BASE_URL, SUPABASE_URL, SUPABASE_SERVICE_KEY,
    _PG_DSN,
    CF_GATEWAY_ENABLED, cf_gateway_url, _CF_PROVIDER_SLUGS,
    CF_AI_GATEWAY_TOKEN, CF_CACHE_TTL,
    REDIS_URL, REDIS_TOKEN,
)

logger = logging.getLogger(__name__)

# ── Upstash Redis (REST-based, serverless) ────────────────────────────────────
# Uses the HTTP REST API — works from any environment without TCP access.
# Initialized here so every module that does `from deps import redis_client`
# automatically gets a live client when credentials are present.
redis_client: Optional[Any] = None
try:
    if REDIS_URL and REDIS_TOKEN:
        from upstash_redis import Redis as _UpstashRedis
        redis_client = _UpstashRedis(url=REDIS_URL, token=REDIS_TOKEN)
        _ping = redis_client.ping()
        logging.info(f"Upstash Redis ready — ping={_ping}")
    else:
        logging.info("Upstash Redis not configured (UPSTASH_REDIS_REST_URL/TOKEN missing) — L1 in-memory cache only")
except ImportError:
    logging.warning("upstash-redis package not installed — Redis disabled. Run: pip install upstash-redis")
except Exception as _redis_init_err:
    redis_client = None
    logging.warning(f"Upstash Redis init failed — L1 cache only: {_redis_init_err}")

# MongoDB with fast timeout — wrapped so bad URLs don't crash startup
try:
    _raw_mongo_url = MONGO_URL.strip()
    if not (_raw_mongo_url.startswith("mongodb://") or _raw_mongo_url.startswith("mongodb+srv://")):
        raise ValueError(f"MONGO_URL has invalid scheme — must begin with mongodb:// or mongodb+srv://. Got: {_raw_mongo_url[:30]!r}...")
    mongo_client = AsyncIOMotorClient(
        _raw_mongo_url,
        # ── Flex-tier tuned settings (2026-04-30) ──────────────────────────
        # Flex tier has better SLAs and dedicated compute vs shared M0/M10.
        # serverSelection and connect timeouts reduced: Flex handshake is ~2s.
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
        socketTimeoutMS=30000,
        # Higher pool: Flex supports more concurrent connections; Railway has
        # 3 gunicorn workers × up to 10 concurrent async tasks each = ~30 in-
        # flight at peak. 100 gives plenty of headroom.
        maxPoolSize=100,
        minPoolSize=5,
        maxIdleTimeMS=60000,
        waitQueueTimeoutMS=15000,
        retryReads=True,
        retryWrites=True,
        # zlib compresses wire traffic on JSON-heavy BSON (chapters/PYQ).
        # zstd/snappy would be better but require extra C packages
        # (backports.zstd / python-snappy) not in the current Python env.
        compressors=["zlib"],
        # Route reads to secondaries where possible — Flex provisions
        # hidden secondaries that take read load off the primary.
        readPreference="secondaryPreferred",
    )
    db = mongo_client[DB_NAME]
    logging.info("MongoDB client initialised (connection not yet verified)")
except Exception as _mongo_init_err:
    logging.warning(f"MongoDB client could not be initialised — content/RAG features disabled: {_mongo_init_err}")
    mongo_client = None  # type: ignore[assignment]
    db = None            # type: ignore[assignment]

_mongo_available = None
_mongo_last_check = 0.0
_MONGO_CHECK_COOLDOWN = 60
_MONGO_DOWN_COOLDOWN = 30

async def is_mongo_available():
    global _mongo_available, _mongo_last_check
    if db is None:
        return False
    now = _time_mod.time()
    cooldown = _MONGO_DOWN_COOLDOWN if _mongo_available is False else _MONGO_CHECK_COOLDOWN
    if _mongo_available is not None and (now - _mongo_last_check) < cooldown:
        return _mongo_available
    try:
        await asyncio.wait_for(db.command("ping"), timeout=3.0)
        _mongo_available = True
    except Exception:
        _mongo_available = False
    _mongo_last_check = now
    return _mongo_available

def mark_mongo_down():
    global _mongo_available, _mongo_last_check
    _mongo_available = False
    _mongo_last_check = _time_mod.time()

# Supabase client (sync, used for users/conversations)
supa: Optional[Any] = None
try:
    if SUPABASE_SERVICE_KEY and _create_supa:
        _supa_client = _create_supa(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        _supa_client.table("users").select("id").limit(1).execute()
        supa = _supa_client
        logging.getLogger(__name__).info("Supabase client ready")
except Exception as _supa_err:
    supa = None
    _err_str = str(_supa_err)
    if "401" in _err_str or "Invalid API key" in _err_str:
        logging.getLogger(__name__).warning("Supabase API key invalid — using MongoDB only")
    else:
        logging.getLogger(__name__).warning(f"Supabase unavailable (using MongoDB only): {_supa_err}")

# ── Replit PostgreSQL (asyncpg pool) — primary relational store ──────────────
pg_pool: Optional[Any] = None   # filled in lifespan startup

_PG_INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT 'free',
    credits_used INTEGER NOT NULL DEFAULT 0,
    credits_limit INTEGER NOT NULL DEFAULT 30,
    document_access TEXT NOT NULL DEFAULT 'zero',
    onboarding_done BOOLEAN NOT NULL DEFAULT FALSE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',
    bio TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    saved_subjects JSONB NOT NULL DEFAULT '[]',
    has_free_credits_issued BOOLEAN NOT NULL DEFAULT TRUE,
    board_id TEXT,
    board_name TEXT,
    class_id TEXT,
    class_name TEXT,
    stream_id TEXT,
    stream_name TEXT,
    referred_by_code TEXT,
    referred_by_user_id TEXT,
    credits_used_today INTEGER NOT NULL DEFAULT 0,
    credits_reset_date TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_used_today INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_reset_date TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TEXT NOT NULL DEFAULT '';
-- Task #530: cross-device ad opt-out preference. Default FALSE so
-- existing accounts are not auto-opted-out on first read.
ALTER TABLE users ADD COLUMN IF NOT EXISTS ads_opt_out BOOLEAN NOT NULL DEFAULT FALSE;
-- Task #591: persisted role column so admins can promote trusted users to
-- 'educator' (the get_educator_user dependency already gates on this).
ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider TEXT NOT NULL DEFAULT 'email';
-- DPDP Act consent columns (India Digital Personal Data Protection Act)
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_dpdp BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_dpdp_version TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS consent_dpdp_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New Chat',
    preview TEXT NOT NULL DEFAULT '',
    subject_id TEXT,
    subject_name TEXT,
    starred BOOLEAN NOT NULL DEFAULT FALSE,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    messages TEXT NOT NULL DEFAULT '[]',
    tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_anonymous BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS anon_id TEXT;
CREATE INDEX IF NOT EXISTS conversations_user_id_idx ON conversations(user_id);
CREATE INDEX IF NOT EXISTS conversations_id_user_idx ON conversations(id, user_id);
CREATE INDEX IF NOT EXISTS conversations_updated_idx ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS conversations_anon_idx ON conversations(is_anonymous) WHERE is_anonymous = TRUE;
CREATE INDEX IF NOT EXISTS users_email_idx ON users(email);
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    registrations_open BOOLEAN NOT NULL DEFAULT TRUE,
    maintenance_mode BOOLEAN NOT NULL DEFAULT FALSE,
    app_name TEXT NOT NULL DEFAULT 'Syrabit.ai',
    tagline TEXT NOT NULL DEFAULT 'AI-Powered Exam Prep'
);
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS crawl_coverage_red INTEGER NOT NULL DEFAULT 30;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS crawl_coverage_yellow INTEGER NOT NULL DEFAULT 50;
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS bot_missing_days INTEGER NOT NULL DEFAULT 3;
INSERT INTO app_settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING;
CREATE TABLE IF NOT EXISTS password_resets (
    token TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_feedback (
    id SERIAL PRIMARY KEY,
    user_id TEXT,
    anon_id TEXT,
    conversation_id TEXT,
    message_index INTEGER,
    message_preview TEXT,
    reaction TEXT,
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS chat_feedback_created_idx ON chat_feedback(created_at DESC);
CREATE INDEX IF NOT EXISTS chat_feedback_user_idx ON chat_feedback(user_id);
ALTER TABLE chat_feedback ADD COLUMN IF NOT EXISTS anon_id TEXT;
CREATE TABLE IF NOT EXISTS activity_log (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    admin_name TEXT NOT NULL DEFAULT '',
    admin_email TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'info',
    audience TEXT NOT NULL DEFAULT 'all',
    status TEXT NOT NULL DEFAULT 'sent',
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT ''
);
"""

async def _init_pg_pool():
    global pg_pool
    if not _asyncpg or not _PG_DSN:
        logging.getLogger(__name__).warning("[WARN] PostgreSQL not configured — asyncpg disabled")
        return
    _log = logging.getLogger(__name__)
    try:
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(_PG_DSN)
        _host = _parsed.hostname or "unknown"
        _log.info(f"PG connecting to host={_host} port={_parsed.port}")
        import socket
        try:
            socket.getaddrinfo(_host, _parsed.port or 5432)
            _log.info(f"PG DNS resolved for {_host}")
        except socket.gaierror as dns_err:
            _log.warning(f"PG DNS resolution FAILED for '{_host}': {dns_err} — raw DSN length={len(_PG_DSN)}, first 30 chars={repr(_PG_DSN[:30])}")
            return
    except Exception as _parse_err:
        _log.warning(f"PG DSN parse error: {_parse_err}")
    try:
        pg_pool = await _asyncpg.create_pool(_PG_DSN, min_size=3, max_size=40)
        async with pg_pool.acquire() as conn:
            await conn.execute(_PG_INIT_SQL)
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_code TEXT")
            except Exception:
                pass
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_user_id TEXT")
            except Exception:
                pass
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id TEXT")
            except Exception:
                pass
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider TEXT")
            except Exception:
                pass
        _pg_host = "supabase-pooler" if "pooler.supabase" in _PG_DSN else "replit-internal"
        logging.getLogger(__name__).info(f"PostgreSQL pool ready ({_pg_host}) — tables created/verified")
    except Exception as _pg_err:
        pg_pool = None
        logging.getLogger(__name__).warning(f"PostgreSQL unavailable: {_pg_err}")

# ── Sarvam AI — Assamese chat LLM client ONLY (V4 §15, Task #492) ────────────
# Per Task #492 Sarvam was scoped down to a single surface: the
# `assamese_rag_chat` LLM dispatch in `llm.py` (sarvam-m chat → Workers-AI
# IndicTrans2 fallback). The translate / TTS / transliterate / non-LLM
# direct clients were removed.
#
# `sarvam_llm_client_direct` is the **CF-Gateway-bypass twin** of the
# streaming chat client. It is part of the surviving chat surface — not
# an additional non-chat client — and is required by `_pick_sarvam_client`
# in `llm.py` for two specific failure modes:
#   1. CF AI Gateway is unhealthy (`is_cf_gateway_up()` is False) → bypass
#      the gateway and call Sarvam directly.
#   2. Gateway-routed call returns 401 (BYOK key-substitution edge case
#      verified 2026-04-20) → retry once via direct.
# It is therefore retained intentionally; removing it would silently break
# Assamese chat the moment CF Gateway flakes.
_sarvam_pool_limits = httpx.Limits(
    max_keepalive_connections=100,
    max_connections=200,
    keepalive_expiry=120,
)
_sarvam_timeout       = httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=5.0)
_sarvam_llm_timeout   = httpx.Timeout(connect=3.0, read=60.0, write=10.0, pool=5.0)
_sarvam_gw_base = cf_gateway_url("sarvam") if (CF_GATEWAY_ENABLED and "sarvam" in _CF_PROVIDER_SLUGS) else None
_sarvam_effective_base = _sarvam_gw_base or SARVAM_BASE_URL
# Sarvam auth: send the real `SARVAM_API_KEY` ALWAYS. The CF custom-provider
# does not support BYOK key-substitution (verified 2026-04-20).
_sarvam_sub_key = SARVAM_API_KEY
_sarvam_headers = {
    'api-subscription-key': _sarvam_sub_key,
    'Content-Type': 'application/json',
}
if _sarvam_gw_base and CF_AI_GATEWAY_TOKEN:
    _sarvam_headers['cf-aig-authorization'] = f'Bearer {CF_AI_GATEWAY_TOKEN}'
    _sarvam_headers['cf-aig-cache-ttl'] = str(CF_CACHE_TTL)
sarvam_llm_client: Optional[httpx.AsyncClient] = None
sarvam_llm_client_direct: Optional[httpx.AsyncClient] = None
_sarvam_llm_ready = bool(SARVAM_API_KEY) or bool(_sarvam_gw_base)
if _sarvam_llm_ready:
    sarvam_llm_client = httpx.AsyncClient(
        base_url=_sarvam_effective_base,
        headers={**_sarvam_headers, 'Accept': 'text/event-stream'},
        limits=_sarvam_pool_limits,
        timeout=_sarvam_llm_timeout,
        http2=True,
        verify=True,
    )
    if _sarvam_gw_base:
        sarvam_llm_client_direct = httpx.AsyncClient(
            base_url=SARVAM_BASE_URL,
            headers={**_sarvam_headers, 'Accept': 'text/event-stream'},
            limits=_sarvam_pool_limits,
            timeout=_sarvam_llm_timeout,
            http2=True,
            verify=True,
        )
    _via = "Cloudflare AI Gateway" if _sarvam_gw_base else "direct"
    logging.getLogger(__name__).info(f"Sarvam-m LLM client ready (HTTP/2 pooled, {_via}: {_sarvam_effective_base})")
else:
    logging.getLogger(__name__).warning("SARVAM_API_KEY not set — assamese_rag_chat will fall back to Workers-AI IndicTrans2 only")

pwd_ctx  = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)

# ── CMS request context guard ─────────────────────────────────────────────────
# Set to True during /cms/{user_id}/* request processing so that web-search /
# library-scrape functions can detect and refuse outbound calls made in this context.
_cms_request_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("_cms_request_ctx", default=False)


def _assert_not_cms_context(operation: str = "web search"):
    """Raise HTTPException 403 if called from within a CMS personalized-plan request."""
    if _cms_request_ctx.get():
        raise HTTPException(
            status_code=403,
            detail=f"Outbound {operation} is not permitted in personalized CMS context.",
        )


_rate_cleanup_task = None
