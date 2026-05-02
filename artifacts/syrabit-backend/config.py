"""Syrabit.ai — Configuration constants and environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

__all__ = [
    "ADMIN_JWT_SECRET",
    "CF_CACHE_TTL", "CF_GATEWAY_ENABLED",
    "CF_TURNSTILE_ENABLED", "CF_TURNSTILE_SECRET_KEY",
    "CHAT_ENHANCE_ENABLED",
    "COOKIE_DOMAIN", "COOKIE_SAMESITE",
    "CORS_ORIGINS", "CORS_ORIGIN_REGEX",
    "DB_NAME", "EMAIL_FROM", "FRONTEND_URL",
    "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
    "JWT_ACCESS_EXPIRE_MINUTES", "JWT_ALGORITHM",
    "JWT_EXPIRE_MINUTES", "JWT_REFRESH_EXPIRE_MINUTES", "JWT_SECRET",
    "LLM_MODEL", "LLM_PROVIDER",
    "MONGO_URL", "OPENAI_API_KEY", "PLAN_LIMITS",
    "PROVIDER_PRIORITY", "PROVIDER_CREDITS",
    "REDIS_AI_CACHE_TTL", "REDIS_TOKEN", "REDIS_URL",
    "MEMORYSTORE_REDIS_URL", "REDIS_AI_CACHE_NAMESPACE",
    "REDIS_AI_CACHE_MAX_ENTRY_BYTES",
    "REDIS_AI_CACHE_CONNECT_TIMEOUT_MS", "REDIS_AI_CACHE_OP_TIMEOUT_MS",
    "ROOT_DIR",
    "SARVAM_API_KEY", "SARVAM_BASE_URL", "SARVAM_THINK_BUFFER",
    "SARVAM_TRANSLATE_KEY",
    "SECURE_COOKIES", "SEED_DATA", "SLOW_QUERY_THRESHOLD_MS",
    "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_URL",
    "_ASSEMBLYAI_KEY", "ASSEMBLYAI_STT_MODEL",
    "_AWS_ACCESS_KEY", "_AWS_REGION", "_AWS_SECRET_KEY",
    "_CEREBRAS_KEY", "_CF_PROVIDER_SLUGS", "_CORS_ALLOW_CREDENTIALS",
    "_ELEVENLABS_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL_ID",
    "_GEMINI_KEY", "_GEMINI_KEY_2",
    "_GROQ_KEY",
    "_OPENAI_KEY", "_OPENROUTER_KEY",
    "_PG_DSN",
    "_SARVAM_LLM_KEY", "_SARVAM_LLM_KEY_2", "_SARVAM_LLM_KEY_3",
    "_XAI_KEY",
    "cf_gateway_url", "get_provider_base_url",
    "is_cf_gateway_up", "mark_cf_gateway_down",
    "Configurator",
    "GOOGLE_BILLING_ACCOUNT_ID",
    "GOOGLE_BILLING_BIGQUERY_PROJECT",
    "GOOGLE_BILLING_BIGQUERY_DATASET",
    "GOOGLE_BILLING_BIGQUERY_TABLE",
    "GOOGLE_BILLING_BIGQUERY_LOCATION",
]

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')


class Configurator:
    """Lightweight runtime environment override store.

    Allows code to set or retrieve env-var overrides at runtime without
    mutating os.environ globally. Falls back to os.environ when no
    runtime override exists.
    """
    _overrides: dict = {}

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        if key in cls._overrides:
            return cls._overrides[key]
        return os.environ.get(key, default)

    @classmethod
    def set_runtime_env(cls, key: str, value: str) -> None:
        cls._overrides[key] = value
        os.environ[key] = value

MONGO_URL    = (os.environ.get('MONGO_URL') or os.environ.get('MONGODB_URI') or 'mongodb://localhost:27017').strip().strip('"').strip("'")
DB_NAME      = os.environ.get('DB_NAME', 'test_database')
# ── JWT signing secrets (Task #770 — audit finding S2) ───────────────────
# `JWT_SECRET` and `ADMIN_JWT_SECRET` MUST be set explicitly. The
# previous implementation fell back to a deterministic value derived
# from `MONGO_URL + DB_NAME + REPL_ID` whenever the env var was unset.
# That meant any leak of the database connection string (logs,
# screenshots, a contractor's machine) was equivalent to a leak of the
# admin signing key — an attacker could forge admin sessions without
# touching the database. We now refuse to start in any non-test
# environment when either secret is missing.
#
# Test runs (pytest) get a freshly generated ephemeral secret per
# process — NOT derived from any deployment value — so unit tests
# don't need to wire env in conftest. The ephemeral secret dies with
# the process and can never be recomputed from anything else.
_RUNNING_UNDER_PYTEST = (
    "PYTEST_CURRENT_TEST" in os.environ
    or "pytest" in os.environ.get("_", "")
    or any("pytest" in (a or "") for a in __import__("sys").argv[:2])
)


def _require_secret(name: str, *, min_len: int = 64) -> str:
    raw = os.environ.get(name, "").strip()
    if raw:
        if len(raw) < min_len:
            raise RuntimeError(
                f"{name} is set but only {len(raw)} chars long — "
                f"refusing to start. Use at least {min_len} chars of "
                f"high-entropy randomness (e.g. `python3 -c 'import secrets; "
                f"print(secrets.token_hex(48))'`)."
            )
        return raw
    if _RUNNING_UNDER_PYTEST:
        import secrets as _secrets
        ephemeral = _secrets.token_hex(48)
        import warnings as _w
        _w.warn(
            f"{name} unset under pytest — using an ephemeral random "
            f"secret for this process only. Tokens signed in this "
            f"process cannot be verified anywhere else.",
            stacklevel=2,
        )
        return ephemeral
    raise RuntimeError(
        f"{name} is not set. Refusing to start: the previous "
        f"deterministic fallback derived from MONGO_URL+DB_NAME was a "
        f"security hole (audit finding S2 — DB connection string leak "
        f"became admin access). Set {name} to 64+ chars of randomness "
        f"in Replit Secrets and your production env (Railway / "
        f"Cloud Run). Generate one with: "
        f"`python3 -c 'import secrets; print(secrets.token_hex(48))'`."
    )


JWT_SECRET = _require_secret("JWT_SECRET")
JWT_ALGORITHM    = 'HS256'
JWT_ACCESS_EXPIRE_MINUTES = int(os.environ.get('JWT_ACCESS_EXPIRE_MINUTES', '60'))
JWT_REFRESH_EXPIRE_MINUTES = int(os.environ.get('JWT_REFRESH_EXPIRE_MINUTES', str(60 * 24 * 30)))
JWT_EXPIRE_MINUTES = JWT_ACCESS_EXPIRE_MINUTES

ADMIN_JWT_SECRET = _require_secret("ADMIN_JWT_SECRET")
if ADMIN_JWT_SECRET == JWT_SECRET:
    raise RuntimeError(
        "ADMIN_JWT_SECRET must be different from JWT_SECRET. "
        "Reusing the same key for user and admin tokens means a "
        "leaked user token signing key is also an admin token "
        "signing key. Generate two independent secrets."
    )

# ── Google Analytics (GA4) OAuth — NOT used for Google sign-in (see Supabase) ─
# Google sign-in is handled by Supabase. These vars are only for ga4_client.py.
GOOGLE_OAUTH_CLIENT_ID     = os.environ.get('GOOGLE_OAUTH_CLIENT_ID', '').strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', '').strip()

# ── Email Configuration ───────────────────────────────────────────────────────
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '').strip()
EMAIL_FROM     = os.environ.get('EMAIL_FROM', 'noreply@syrabit.ai').strip()
FRONTEND_URL   = os.environ.get('FRONTEND_URL', 'https://syrabit.ai').strip().rstrip('/')

# ── Cloudflare API tokens (Task #534 contract) ──────────────────────────────
# Three tokens, three roles. Priority order respects the spec while keeping
# legacy names working so operators don't have to rotate secrets just to
# upgrade to the new naming:
#
#   Runtime / analytics (Vectorize:Edit, Cache Purge, Analytics:Read):
#     1. CLOUDFLARE_ANALYTICS_TOKEN  — Task #534 spec name (preferred)
#     2. CF_ANALYTICS_API_TOKEN      — legacy alias (logs warning)
#     3. CLOUDFLARE_API_TOKEN        — last-resort fallback (logs warning)
#   Pages-scoped names (CF_PAGES_API_TOKEN) and undifferentiated legacy
#   names (CF_API_TOKEN) are NOT accepted here — see _runtime_cf_token()
#   in cloudflare_client.py for the strict runtime policy.
#
#   Pages CI (Pages:Edit + Vectorize:Edit):
#     1. CLOUDFLARE_PAGES_TOKEN      — Task #534 spec name
#     2. CF_PAGES_API_TOKEN          — legacy alias (logs warning)
#
# Wrangler deploy reads CLOUDFLARE_API_TOKEN itself (auto-detect); we don't
# expose it through this module since the FastAPI process never deploys.
_ANALYTICS_TOKEN_ENV_NAMES = (
    'CF_PAGES_API_TOKEN',
    'CLOUDFLARE_PAGES_TOKEN',
    'CLOUDFLARE_ANALYTICS_TOKEN',
    'CF_ANALYTICS_API_TOKEN',
    'CLOUDFLARE_API_TOKEN',
)
_PAGES_TOKEN_ENV_NAMES = (
    'CLOUDFLARE_PAGES_TOKEN',
    'CF_PAGES_API_TOKEN',
)


_ANALYTICS_LEGACY_LOGGED = False
_PAGES_LEGACY_LOGGED = False

# Fallback names that are documented permanent policy (DEPLOY.md): both
# `CLOUDFLARE_API_TOKEN` (analytics) and `CF_PAGES_API_TOKEN` (Pages) are
# accepted forever — they map to the same secret value as the spec name.
# We log a single one-line INFO that we used the fallback (for operator
# transparency) instead of a multi-line WARNING that shows up in error
# log dashboards / Railway alert filters.
_ANALYTICS_ACCEPTED_FALLBACKS = {'CF_ANALYTICS_API_TOKEN', 'CLOUDFLARE_API_TOKEN'}
_PAGES_ACCEPTED_FALLBACKS = {'CF_PAGES_API_TOKEN'}


def _resolve_cf_analytics_token() -> str:
    global _ANALYTICS_LEGACY_LOGGED
    for _name in _ANALYTICS_TOKEN_ENV_NAMES:
        _val = os.environ.get(_name, '').strip()
        if _val:
            if _name != _ANALYTICS_TOKEN_ENV_NAMES[0] and not _ANALYTICS_LEGACY_LOGGED:
                _ANALYTICS_LEGACY_LOGGED = True
                # Documented-fallback: INFO. Unknown alias: keep WARNING.
                level = "INFO" if _name in _ANALYTICS_ACCEPTED_FALLBACKS else "WARNING"
                print(
                    f"[config] {level}: CF analytics token resolved from "
                    f"{_name!r} (CLOUDFLARE_ANALYTICS_TOKEN preferred but optional).",
                    flush=True,
                )
            return _val
    return ''


def _resolve_cf_pages_token() -> str:
    global _PAGES_LEGACY_LOGGED
    for _name in _PAGES_TOKEN_ENV_NAMES:
        _val = os.environ.get(_name, '').strip()
        if _val:
            if _name != _PAGES_TOKEN_ENV_NAMES[0] and not _PAGES_LEGACY_LOGGED:
                _PAGES_LEGACY_LOGGED = True
                level = "INFO" if _name in _PAGES_ACCEPTED_FALLBACKS else "WARNING"
                print(
                    f"[config] {level}: CF Pages token resolved from "
                    f"{_name!r} (CLOUDFLARE_PAGES_TOKEN preferred but optional).",
                    flush=True,
                )
            return _val
    return ''


CF_ANALYTICS_API_TOKEN = _resolve_cf_analytics_token()
CF_PAGES_DEPLOY_TOKEN = _resolve_cf_pages_token()
CF_ZONE_ID = os.environ.get('CF_ZONE_ID', '').strip()
CF_API_TOKEN = os.environ.get('CF_API_TOKEN', '').strip() or CF_ANALYTICS_API_TOKEN

# ── Cloudflare Access / Zero Trust (Task #637) ──────────────────────────────
# When enforcement is on, every admin / internal request must carry a valid
# Cf-Access-Jwt-Assertion header signed by the team domain's JWKS and
# matching one of the configured AUD tags. See ``cf_access.py`` and
# ``docs/CLOUDFLARE_ZERO_TRUST.md`` for the full handshake.
CF_ACCESS_TEAM_DOMAIN = os.environ.get('CF_ACCESS_TEAM_DOMAIN', '').strip().rstrip('/')
CF_ACCESS_AUD_ADMIN = os.environ.get('CF_ACCESS_AUD_ADMIN', '').strip()
CF_ACCESS_AUD_INTERNAL = os.environ.get('CF_ACCESS_AUD_INTERNAL', '').strip()
CF_ACCESS_ENFORCE = os.environ.get('CF_ACCESS_ENFORCE', '').strip().lower() in ('1', 'true', 'yes', 'on')

# ── Cloudflare Turnstile ────────────────────────────────────────────────────
CF_TURNSTILE_SECRET_KEY = os.environ.get('CF_TURNSTILE_SECRET_KEY', '').strip()
CF_TURNSTILE_ENABLED = bool(CF_TURNSTILE_SECRET_KEY)

# ── Cloudflare R2 Object Storage ─────────────────────────────────────────────
# R2 uses S3-compatible API with account-scoped endpoint.
# Create R2 API tokens at: CF Dashboard → R2 → Manage R2 API Tokens
R2_ACCESS_KEY_ID     = os.environ.get('R2_ACCESS_KEY_ID', '').strip()
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY', '').strip()
R2_BUCKET_NAME       = os.environ.get('R2_BUCKET_NAME', 'syrabit-media').strip()
R2_PUBLIC_URL        = os.environ.get('R2_PUBLIC_URL', '').strip().rstrip('/')
# Endpoint derived from account ID: https://<account_id>.r2.cloudflarestorage.com
_R2_ACCOUNT_ID = os.environ.get('CF_AI_GATEWAY_ACCOUNT_ID', '').strip()
R2_ENDPOINT_URL = (
    os.environ.get('R2_ENDPOINT_URL', '').strip()
    or (f'https://{_R2_ACCOUNT_ID}.r2.cloudflarestorage.com' if _R2_ACCOUNT_ID else '')
)
R2_ENABLED = bool(R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_ENDPOINT_URL)

# ── Chat Enhancement Feature Flag ────────────────────────────────────────────
# Controls whether cognitive anchors, engagement hooks, and trend signals are
# injected into AI chat responses.  Defaults ON; set CHAT_ENHANCE_ENABLED=0
# to disable for A/B testing or debugging.
CHAT_ENHANCE_ENABLED = os.environ.get('CHAT_ENHANCE_ENABLED', '1').strip() not in ('0', 'false', 'no', 'off')

# ── Cloudflare AI Gateway ────────────────────────────────────────────────────
import time as _time

_CF_ACCOUNT_ID = os.environ.get('CF_AI_GATEWAY_ACCOUNT_ID', '').strip()
_CF_GATEWAY_ID = os.environ.get('CF_AI_GATEWAY_ID', '').strip()
# Authenticated Gateway token (Cloudflare dashboard → AI Gateway →
# <gateway> → Settings → Authenticated Gateway). When the gateway has
# auth turned on, every request must carry
#   cf-aig-authorization: Bearer <token>
# or Cloudflare returns HTTP 401 with `{code: 2009, message: Unauthorized}`
# — which is exactly the error we kept seeing in production logs every
# few minutes (one wasted round trip per request before the direct-URL
# fallback kicks in for 5 min). Leaving this env var unset disables the
# header (gateway must then have auth turned OFF in the dashboard).
CF_AI_GATEWAY_TOKEN = os.environ.get('CF_AI_GATEWAY_TOKEN', '').strip()
CF_GATEWAY_ENABLED = bool(_CF_ACCOUNT_ID and _CF_GATEWAY_ID)
CF_GATEWAY_BASE = (
    f"https://gateway.ai.cloudflare.com/v1/{_CF_ACCOUNT_ID}/{_CF_GATEWAY_ID}"
    if CF_GATEWAY_ENABLED else ""
)
# AI Gateway response cache TTL — cache hits are free on the Standard plan.
# Gateway caches by exact request hash (messages + model + params), so only
# identical requests benefit. 86 400s = 24 h is safe for this workload.
# Override with CF_AI_GATEWAY_CACHE_TTL env var (seconds) if needed.
CF_CACHE_TTL = int(os.environ.get('CF_AI_GATEWAY_CACHE_TTL', '86400'))

_CF_PROVIDER_SLUGS = {
    "openai":      "openai",
    "groq":        "groq/openai/v1",
    "xai":         "grok/v1",
    "gemini":      "google-ai-studio/v1beta/openai",
    # Sarvam: slug has NO /v1 because callers already send
    # /v1/chat/completions, /translate, /text-to-speech, etc.
    # CF custom provider forwards {base}/custom-sarvam/<path> → https://api.sarvam.ai/<path>
    "sarvam":      "custom-sarvam",
    "cerebras":    "cerebras/v1",
    "openrouter":  "openrouter/v1",
    # New providers routed through CF AI Gateway
    "cohere":      "cohere/v1",      # Embeddings/RAG — embed-multilingual-v3.0 (1024-dim)
    "cartesia":    "cartesia/v1",    # Voice TTS — Sonic-2 model
    "baseten":     "baseten/v1",     # Fine-tuned EdTech LLMs — OpenAI-compatible endpoint
    "assemblyai":  "assemblyai/v2",  # STT — /v2/upload, /v2/transcript
    "elevenlabs":  "elevenlabs/v1",  # TTS — /v1/text-to-speech
    # Task #250 — Phase 2 providers routed via CF AI Gateway BYOK
    "bedrock":      "aws-bedrock",       # AWS Bedrock — Converse API; CF handles SigV4
    "azure_openai": "azure-openai",      # Azure OpenAI — chat/completions; CF handles key
}

_DIRECT_PROVIDER_URLS = {
    "openai":      None,
    "groq":        None,
    "xai":         "https://api.x.ai/v1",
    "gemini":      "https://generativelanguage.googleapis.com/v1beta/openai/",
    # Sarvam direct URL has NO /v1 — callers already supply /v1/chat/completions
    # and non-LLM endpoints like /translate, /text-to-speech live at root.
    "sarvam":      "https://api.sarvam.ai",
    "cerebras":    "https://api.cerebras.ai/v1",
    "openrouter":  "https://openrouter.ai/api/v1",
    # Fallback direct URLs (used when CF gateway is down)
    "cohere":      "https://api.cohere.com/v1",
    "cartesia":    "https://api.cartesia.ai/v1",
    "baseten":     "https://api.baseten.co/v1",   # Baseten universal OpenAI-compatible gateway
    # Bedrock direct: region-scoped; Azure direct: tenant endpoint (requires env var)
    "bedrock":     None,   # Set at runtime via BEDROCK_DIRECT_URL or derived from AWS_REGION
    "azure_openai": None,  # Set at runtime via AZURE_OPENAI_ENDPOINT
}

_cf_gw_healthy = True
_cf_gw_fail_ts = 0.0
_CF_GW_RETRY_AFTER = 300

def is_cf_gateway_up() -> bool:
    global _cf_gw_healthy, _cf_gw_fail_ts
    if not CF_GATEWAY_ENABLED:
        return False
    if not _cf_gw_healthy and _time.time() - _cf_gw_fail_ts > _CF_GW_RETRY_AFTER:
        _cf_gw_healthy = True
    return _cf_gw_healthy

def mark_cf_gateway_down():
    global _cf_gw_healthy, _cf_gw_fail_ts
    _cf_gw_healthy = False
    _cf_gw_fail_ts = _time.time()

def cf_gateway_url(provider: str) -> str:
    slug = _CF_PROVIDER_SLUGS.get(provider)
    if slug:
        return f"{CF_GATEWAY_BASE}/{slug}"
    return ""

def get_provider_base_url(provider: str) -> str | None:
    if is_cf_gateway_up() and provider in _CF_PROVIDER_SLUGS:
        return cf_gateway_url(provider)
    return _DIRECT_PROVIDER_URLS.get(provider)


# ── BYOK (Bring-Your-Own-Keys) via Cloudflare AI Gateway ─────────────────────
# When CF AI Gateway is enabled with BYOK configured in the CF dashboard, the
# backend no longer needs real provider API keys in its environment. The flow:
#
#   1. Backend sends request to gateway URL with:
#        api_key="byok"                          (placeholder, CF ignores it)
#        header cf-aig-byok-key: default         (tells CF to substitute)
#   2. CF AI Gateway replaces the auth with its stored BYOK key for the
#      provider and forwards the request upstream.
#   3. Upstream provider sees its real key and responds normally.
#
# Removing the provider env vars (GEMINI_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY,
# OPENROUTER_API_KEY, SARVAM_API_KEY, …) is SAFE once BYOK is wired — the
# backend sends placeholders and CF does the real auth. Keep the CF gateway
# env vars themselves (CF_AI_GATEWAY_ACCOUNT_ID, CF_AI_GATEWAY_ID,
# CF_AI_GATEWAY_TOKEN) — those bootstrap the gateway connection itself.
BYOK_PLACEHOLDER = "x"  # openai SDK rejects empty api_key; "x" is a harmless dummy


def byok_headers(include_ttl: bool = True, clear_upstream_auth: bool = True) -> dict:
    """Return CF AI Gateway headers for a BYOK request.

    Verified BYOK invocation (2026-04-20 live probe against gateway `syrabit`):
      - ``Authorization: ''``         → empty upstream auth **mandatory**. If
        we send a dummy bearer like ``Bearer byok`` CF forwards it raw to
        upstream and gets 401. BYOK only fires when the upstream auth header
        is empty (or missing), signalling to CF that it should inject its
        stored key.
      - ``cf-aig-byok-key: true``     → opt-in flag. Without it, CF leaves the
        empty Authorization untouched and upstream 401s.
      - ``cf-aig-cache-ttl: <N>``     → response cache TTL hint.
      - ``cf-aig-authorization: …``  → Authenticated-Gateway bearer, only
        sent when the gateway has auth mode enabled.

    ``clear_upstream_auth=False`` is used by the Sarvam httpx client, which
    has its own ``api-subscription-key`` header (not ``Authorization``) —
    that callsite clears it separately.

    Returns ``{}`` when the gateway is down so callers can short-circuit.
    """
    if not is_cf_gateway_up():
        return {}
    h: dict = {"cf-aig-byok-key": "true"}
    if clear_upstream_auth:
        # Empty string overrides the openai/httpx SDK's auto-inserted
        # ``Authorization: Bearer <api_key>`` header so CF sees no upstream
        # auth and injects its stored BYOK key.
        h["Authorization"] = ""
    if include_ttl:
        h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
    if CF_AI_GATEWAY_TOKEN:
        h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
    return h

import logging as _logging
_cfg_log = _logging.getLogger(__name__)
if CF_GATEWAY_ENABLED:
    _cfg_log.info(f"Cloudflare AI Gateway ENABLED — base={CF_GATEWAY_BASE}, cache_ttl={CF_CACHE_TTL}s")
else:
    _cfg_log.info("Cloudflare AI Gateway DISABLED — using direct provider URLs")

# ── LLM Configuration ─────────────────────────────────────────────────────────
_GROQ_KEY = os.environ.get('GROQ_API_KEY', '').strip()
# GROQ_API_KEY_2 removed — key was deleted from Railway as part of env-var cleanup.
# CF AI Gateway BYOK (single key, edge-managed retries) replaces secondary key rotation.
# Gemini re-enabled (2026-04-20) — AI Studio Tier 1 confirmed (2000 RPM/key),
# CF AI Gateway BYOK verified working for google-ai-studio provider.
_GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
_GEMINI_KEY_2 = os.environ.get('GEMINI_API_KEY_2', '').strip()
_GEMINI_KEY_RAW = _GEMINI_KEY
_GEMINI_KEY_2_RAW = _GEMINI_KEY_2
_XAI_KEY = os.environ.get('XAI_API_KEY', '').strip()
_OPENAI_KEY = os.environ.get('OPENAI_API_KEY', '').strip()
_SARVAM_LLM_KEY = os.environ.get('SARVAM_API_KEY', '').strip()
_SARVAM_LLM_KEY_2 = os.environ.get('SARVAM_API_KEY_2', '').strip()
_SARVAM_LLM_KEY_3 = os.environ.get('SARVAM_API_KEY_3', '').strip()
_CEREBRAS_KEY = os.environ.get('CEREBRAS_API_KEY', '').strip()
_OPENROUTER_KEY = os.environ.get('OPENROUTER_API_KEY', '').strip()

# ── New AI provider keys (Cohere, Cartesia, Baseten) ─────────────────────────
# All three route through CF AI Gateway (BYOK) so local keys are optional
# once the keys are registered in the CF dashboard. When gateway is enabled
# and the local env var is missing, BYOK_PLACEHOLDER is substituted so the
# provider module activates and CF injects the real key on every request.
#
# Baseten model selection: BASETEN_MODEL_ID is the deployment ID shown in
# the Baseten dashboard (e.g. "xyz123abc"). Required to use Baseten even in
# BYOK mode — it is sent as the "model" field in the chat/completions body.
_COHERE_KEY       = os.environ.get('COHERE_API_KEY',       '').strip()
_CARTESIA_KEY     = os.environ.get('CARTESIA_API_KEY',     '').strip()
_BASETEN_KEY      = os.environ.get('BASETEN_API_KEY',      '').strip()
_ASSEMBLYAI_KEY   = os.environ.get('ASSEMBLYAI_API_KEY',   '').strip()
_ELEVENLABS_KEY   = os.environ.get('ELEVENLABS_API_KEY',   '').strip()
BASETEN_MODEL_ID  = os.environ.get('BASETEN_MODEL_ID', '').strip()

# AssemblyAI STT config
ASSEMBLYAI_STT_MODEL = os.environ.get('ASSEMBLYAI_STT_MODEL', 'best').strip() or 'best'

# ElevenLabs TTS config
ELEVENLABS_VOICE_ID = os.environ.get('ELEVENLABS_VOICE_ID', '').strip()
ELEVENLABS_MODEL_ID = os.environ.get('ELEVENLABS_MODEL_ID', 'eleven_multilingual_v2').strip() or 'eleven_multilingual_v2'

# Cohere embed config
COHERE_EMBED_MODEL   = os.environ.get('COHERE_EMBED_MODEL',   'embed-multilingual-v3.0').strip() or 'embed-multilingual-v3.0'
COHERE_EMBED_PRIMARY = os.environ.get('COHERE_EMBED_PRIMARY', '1').strip().lower() not in ('0', 'false', 'no', 'off')

# Cartesia voice config
CARTESIA_DEFAULT_VOICE_ID = os.environ.get('CARTESIA_VOICE_ID', '').strip()
CARTESIA_MODEL_ID         = os.environ.get('CARTESIA_MODEL_ID', 'sonic-2').strip() or 'sonic-2'

# ── Vertex AI Gemini Flash chat (Task #607) ─────────────────────────────────
# When VERTEX_PROJECT_ID is set, the chat path can route through Vertex AI's
# Gemini Flash streaming endpoint for lower TTFT. Auth is via Application
# Default Credentials (GOOGLE_APPLICATION_CREDENTIALS pointing at a SA JSON)
# or the inline VERTEX_SERVICE_ACCOUNT_JSON blob. See vertex_chat.py.
VERTEX_PROJECT_ID = os.environ.get('VERTEX_PROJECT_ID', '').strip()
VERTEX_LOCATION = os.environ.get('VERTEX_LOCATION', 'us-central1').strip() or 'us-central1'
VERTEX_GEMINI_MODEL = os.environ.get('VERTEX_GEMINI_MODEL', 'gemini-2.5-flash').strip() or 'gemini-2.5-flash'
# CHAT_DEFAULT_MODEL is a *system-wide* default consulted by the chat route
# when the client does not pin a specific model. Admin UI can override this
# at runtime (db.api_config.chat_model.default), which takes precedence.
# Accepted values:
#   "openai/gpt-oss-20b"   — Workers AI GPT-OSS-20B (primary, no quota issues)
#   "openai/gpt-oss-120b"  — Workers AI GPT-OSS-120B (higher quality, content tasks)
#   "vertex/gemini-flash"  — Vertex AI Gemini Flash (set via CHAT_DEFAULT_MODEL env if needed)
CHAT_DEFAULT_MODEL = os.environ.get(
    'CHAT_DEFAULT_MODEL',
    'openai/gpt-oss-20b',
).strip()

# BYOK fallback: when CF AI Gateway is enabled, any missing provider env key
# is substituted with the BYOK_PLACEHOLDER so the SmartKeyPool / provider list
# still builds (downstream callers send placeholder + cf-aig-byok-key header
# and the gateway substitutes the real key). This is what lets operators
# safely remove GROQ_API_KEY, GEMINI_API_KEY, CEREBRAS_API_KEY, etc. from
# production secrets once BYOK is verified in the CF dashboard.
if CF_GATEWAY_ENABLED:
    _GROQ_KEY     = _GROQ_KEY     or BYOK_PLACEHOLDER
    _GEMINI_KEY   = _GEMINI_KEY   or BYOK_PLACEHOLDER
    _CEREBRAS_KEY = _CEREBRAS_KEY or BYOK_PLACEHOLDER
    _OPENROUTER_KEY = _OPENROUTER_KEY or BYOK_PLACEHOLDER
    _SARVAM_LLM_KEY = _SARVAM_LLM_KEY or BYOK_PLACEHOLDER
    _XAI_KEY      = _XAI_KEY      or BYOK_PLACEHOLDER
    _OPENAI_KEY   = _OPENAI_KEY   or BYOK_PLACEHOLDER  # CF slug: openai/v1
    # New providers: BYOK allows the CF gateway to inject keys stored in
    # the CF dashboard, so the local env var is optional in production.
    _COHERE_KEY      = _COHERE_KEY      or BYOK_PLACEHOLDER
    _CARTESIA_KEY    = _CARTESIA_KEY    or BYOK_PLACEHOLDER
    _BASETEN_KEY     = _BASETEN_KEY     or BYOK_PLACEHOLDER
    _ASSEMBLYAI_KEY  = _ASSEMBLYAI_KEY  or BYOK_PLACEHOLDER
    _ELEVENLABS_KEY  = _ELEVENLABS_KEY  or BYOK_PLACEHOLDER
    # Secondary/tertiary keys (_GEMINI_KEY_2, _SARVAM_LLM_KEY_2/3)
    # stay empty if not set — CF Gateway manages rate limiting at the edge via
    # the single BYOK key per provider. Delete these from Railway to clean up.
# LLM_PRIMARY_PROVIDER is the canonical name (PR #36); LLM_PROVIDER kept as alias.
_EXPLICIT_PROVIDER = (
    os.environ.get('LLM_PRIMARY_PROVIDER', '').strip() or
    os.environ.get('LLM_PROVIDER', '').strip()
).lower()
_AWS_ACCESS_KEY = os.environ.get('AWS_ACCESS_KEY_ID', '').strip()
_AWS_SECRET_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '').strip()
_AWS_REGION = os.environ.get('AWS_REGION', os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')).strip()

# ── Azure Document Intelligence (OCR fallback) ─────────────────────────────────
# Set AZURE_DOCUMENT_INTELLIGENCE_KEY and AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
# in Replit Secrets / Railway env.  When unset the Textract↔Azure rotation
# silently skips Azure and AWS Textract handles every call.
AZURE_DOC_INTEL_KEY = os.environ.get(
    'AZURE_DOCUMENT_INTELLIGENCE_KEY',
    os.environ.get('AZURE_FORM_RECOGNIZER_KEY', '')
).strip()
AZURE_DOC_INTEL_ENDPOINT = os.environ.get(
    'AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT',
    os.environ.get('AZURE_FORM_RECOGNIZER_ENDPOINT', '')
).strip()

# ── Bedrock feature-service proxy (Task #256) ─────────────────────────────────
# The bedrock-proxy Cloudflare Worker signs AWS SigV4 requests for services
# not natively supported by the CF AI Gateway BYOK path (Polly, Transcribe,
# Translate). Deploy workers/bedrock-proxy and set BEDROCK_PROXY_URL to its URL.
# When unset, bedrock TTS/STT/Translate raise RuntimeError → excluded gracefully.
BEDROCK_PROXY_URL = os.environ.get('BEDROCK_PROXY_URL', '').strip()
# Shared secret for authenticating backend → bedrock-proxy Worker requests.
# Must match the BEDROCK_PROXY_AUTH_TOKEN wrangler secret on the Worker.
# When set, every Polly/Transcribe/Translate proxy call sends:
#   Authorization: Bearer <BEDROCK_PROXY_AUTH_TOKEN>
# When unset, no auth header is sent (acceptable for private/internal Workers).
BEDROCK_PROXY_AUTH_TOKEN = os.environ.get('BEDROCK_PROXY_AUTH_TOKEN', '').strip()
# Default Polly voice for bedrock TTS. "Raveena" is the Neural Indian English
# voice; swap to "Joanna", "Matthew", "Aditi", etc. via env var.
BEDROCK_POLLY_VOICE = os.environ.get('BEDROCK_POLLY_VOICE', 'Raveena').strip() or 'Raveena'

# ── Azure OpenAI model / deployment name (Task #256) ─────────────────────────
# Azure OpenAI deployment name for chat completions, embeddings, and Whisper STT.
# Azure uses a "deployment name" instead of a model name; the default maps to
# gpt-4o-mini (cost-efficient). Override via AZURE_OPENAI_MODEL in Railway Secrets.
# Used by providers/azure_openai.py as _MODEL. Vision and embed calls also inherit
# this unless they use a dedicated deployment (e.g. text-embedding-3-large).
AZURE_OPENAI_MODEL = (
    os.environ.get('AZURE_OPENAI_MODEL', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
)

# ── Azure Speech & Translator (Task #256) ────────────────────────────────────
# Azure Speech Services — used for Azure Neural TTS (call_tts in azure_openai.py)
# and Azure Whisper STT (call_stt via CF BYOK).
# Set in Railway/Replit Secrets; when unset, azure_openai TTS raises RuntimeError.
AZURE_SPEECH_KEY = os.environ.get('AZURE_SPEECH_KEY', '').strip()
AZURE_SPEECH_REGION = os.environ.get('AZURE_SPEECH_REGION', '').strip()
# Default Azure Neural TTS voice — Indian English expressive neural voice.
AZURE_TTS_VOICE = (
    os.environ.get('AZURE_TTS_VOICE', 'en-IN-NeerjaExpressiveNeural').strip()
    or 'en-IN-NeerjaExpressiveNeural'
)
# Azure Translator — used for azure_openai.call_translate().
# Separate from the Azure OpenAI key; set AZURE_TRANSLATOR_KEY in Railway Secrets.
AZURE_TRANSLATOR_KEY = os.environ.get('AZURE_TRANSLATOR_KEY', '').strip()
AZURE_TRANSLATOR_ENDPOINT = (
    os.environ.get('AZURE_TRANSLATOR_ENDPOINT', 'https://api.cognitive.microsofttranslator.com').strip()
    or 'https://api.cognitive.microsofttranslator.com'
)

# ── Google Cloud Platform credentials (Task #247) ────────────────────────────
# A single service account JSON (GOOGLE_APPLICATION_CREDENTIALS_JSON) is used
# for all GCP services: STT v2 (Chirp_2), TTS Neural2, Translation v3,
# Vision OCR, Gemini fallback, and Vertex AI Embeddings fallback.
# Paste the full service account JSON as a single line in Replit Secrets.
# All GCP providers check this at call time — no startup failure if missing.
GOOGLE_APPLICATION_CREDENTIALS_JSON = os.environ.get(
    'GOOGLE_APPLICATION_CREDENTIALS_JSON', ''
).strip()
# GCP project for STT, Translation, Vision, and Vertex AI.
# Falls back to VERTEX_PROJECT_ID if already set.
GOOGLE_CLOUD_PROJECT = (
    os.environ.get('GOOGLE_CLOUD_PROJECT', '').strip()
    or os.environ.get('GCP_PROJECT_ID', '').strip()
    or os.environ.get('VERTEX_PROJECT_ID', '').strip()
)
# Budget alert webhook flag — set GOOGLE_BILLING_ALERT=1 when the GCP
# budget webhook fires (at $1,800 / 90% or $1,900 / 95%) so the admin
# panel can surface a "credits low" warning row.
GOOGLE_BILLING_ALERT = os.environ.get('GOOGLE_BILLING_ALERT', '').strip() in ('1', 'true', 'yes', 'on')
# GCP grant total (fixed) and warning threshold for admin panel.
GCP_CREDIT_GRANT_USD = 2000.0
GCP_CREDIT_WARN_REMAINING_USD = 200.0
# Billing account ID for the Cloud Billing Budget API (Task #253).
# Format: "XXXXXX-XXXXXX-XXXXXX" (find in GCP Console → Billing → Account overview).
# When set, /api/admin/vertex/gcp-credits reads real budget thresholds and
# month-to-date spend from the Cloud Billing Budget API instead of using static
# estimates. The service account (GOOGLE_APPLICATION_CREDENTIALS_JSON) must have
# roles/billing.viewer (or billing.budgets.get) on the billing account.
GOOGLE_BILLING_ACCOUNT_ID = os.environ.get('GOOGLE_BILLING_ACCOUNT_ID', '').strip()
# BigQuery Billing Export config (Task #253) — optional, needed for per-service spend.
# Standard export table name: gcp_billing_export_v1_{ACCOUNT_ID_underscored}
# e.g. for account 12A3B4-C5D6E7-F8G9H0 → gcp_billing_export_v1_12A3B4_C5D6E7_F8G9H0
# Set these if you have enabled GCP Billing Export to BigQuery:
#   GCP Console → Billing → Billing export → BigQuery export → Enable
GOOGLE_BILLING_BIGQUERY_PROJECT = (
    os.environ.get('GOOGLE_BILLING_BIGQUERY_PROJECT', '').strip()
    or GOOGLE_CLOUD_PROJECT
)
GOOGLE_BILLING_BIGQUERY_DATASET = os.environ.get(
    'GOOGLE_BILLING_BIGQUERY_DATASET', 'billing_export'
).strip() or 'billing_export'
_bq_table_default = (
    'gcp_billing_export_v1_' + GOOGLE_BILLING_ACCOUNT_ID.replace('-', '_')
    if GOOGLE_BILLING_ACCOUNT_ID else ''
)
GOOGLE_BILLING_BIGQUERY_TABLE = (
    os.environ.get('GOOGLE_BILLING_BIGQUERY_TABLE', '').strip()
    or _bq_table_default
)
# BigQuery dataset location — must match where the billing export dataset lives.
# Common values: "US" (multi-region, GCP default), "EU", "us-central1", etc.
# Override if your billing export dataset is in a non-US region/multi-region.
GOOGLE_BILLING_BIGQUERY_LOCATION = (
    os.environ.get('GOOGLE_BILLING_BIGQUERY_LOCATION', 'US').strip().upper()
    or 'US'
)

_CF_API_TOKEN_FOR_LLM = os.environ.get('CLOUDFLARE_API_TOKEN', '').strip()
_CF_ACCOUNT_ID_FOR_LLM = os.environ.get('CF_AI_GATEWAY_ACCOUNT_ID', '').strip()

# Parallel LLM Race Configuration (Task: Fix sequential fallback latency)
# When ENABLE_PARALLEL_LLM_RACE=true, multiple providers are called concurrently
# and the first successful response wins. Remaining requests are cancelled.
ENABLE_PARALLEL_LLM_RACE = os.environ.get('ENABLE_PARALLEL_LLM_RACE', 'true').strip().lower() == 'true'
PARALLEL_RACE_TIMEOUT = float(os.environ.get('PARALLEL_RACE_TIMEOUT', '8.0') or '8.0')  # Max seconds to wait for first response
MIN_PROVIDERS_TO_RACE = int(os.environ.get('MIN_PROVIDERS_TO_RACE', '2') or '2')  # Min healthy providers to trigger race
MAX_CONCURRENT_RACE_PROVIDERS = int(os.environ.get('MAX_CONCURRENT_RACE_PROVIDERS', '3') or '3')  # Cap concurrent calls in race

if _EXPLICIT_PROVIDER == 'workers-ai' and _CF_API_TOKEN_FOR_LLM and _CF_ACCOUNT_ID_FOR_LLM:
    LLM_PROVIDER = 'workers-ai'
    LLM_API_KEY = _CF_API_TOKEN_FOR_LLM
    LLM_MODEL = os.environ.get('LLM_MODEL', '@cf/meta/llama-3.3-70b-instruct-fp8-fast')
elif _EXPLICIT_PROVIDER == 'groq' and _GROQ_KEY:
    LLM_PROVIDER = 'groq'
    LLM_API_KEY = _GROQ_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'meta-llama/llama-4-scout-17b-16e-instruct')
elif _EXPLICIT_PROVIDER == 'sarvam' and _SARVAM_LLM_KEY:
    LLM_PROVIDER = 'sarvam'
    LLM_API_KEY = _SARVAM_LLM_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'sarvam-m')
elif _EXPLICIT_PROVIDER == 'cerebras' and _CEREBRAS_KEY:
    LLM_PROVIDER = 'cerebras'
    LLM_API_KEY = _CEREBRAS_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'llama3.1-8b')
elif _EXPLICIT_PROVIDER == 'openai' and _OPENAI_KEY and _OPENAI_KEY != 'x':
    LLM_PROVIDER = 'openai'
    LLM_API_KEY = _OPENAI_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'gpt-4o-mini')
elif _GROQ_KEY:
    LLM_PROVIDER = 'groq'
    LLM_API_KEY = _GROQ_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'meta-llama/llama-4-scout-17b-16e-instruct')
elif _CEREBRAS_KEY:
    LLM_PROVIDER = 'cerebras'
    LLM_API_KEY = _CEREBRAS_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'llama3.1-8b')
elif _SARVAM_LLM_KEY:
    LLM_PROVIDER = 'sarvam'
    LLM_API_KEY = _SARVAM_LLM_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'sarvam-m')
elif _OPENAI_KEY and _OPENAI_KEY != 'x':
    LLM_PROVIDER = 'openai'
    LLM_API_KEY = _OPENAI_KEY
    LLM_MODEL = os.environ.get('LLM_MODEL', 'gpt-4o-mini')
else:
    LLM_PROVIDER = 'groq'
    LLM_API_KEY = ''
    LLM_MODEL = os.environ.get('LLM_MODEL', 'meta-llama/llama-4-scout-17b-16e-instruct')
OPENAI_API_KEY = LLM_API_KEY

# ── Sarvam AI Configuration ──────────────────────────────────────────────────
SARVAM_API_KEY = os.environ.get('SARVAM_API_KEY', '').strip()
SARVAM_API_KEY_2 = os.environ.get('SARVAM_API_KEY_2', '').strip()
SARVAM_TRANSLATE_KEY = SARVAM_API_KEY or SARVAM_API_KEY_2
SARVAM_BASE_URL = 'https://api.sarvam.ai'

# Alias: CLOUDFLARE_ACCOUNT_ID → CF_AI_GATEWAY_ACCOUNT_ID when not set.
# vectorize_client, wrangler scripts, and CF SDK all expect CLOUDFLARE_ACCOUNT_ID;
# CF_AI_GATEWAY_ACCOUNT_ID holds the same value in Railway/Replit deployments.
_cf_gw_account = os.environ.get('CF_AI_GATEWAY_ACCOUNT_ID', '').strip()
if not os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip() and _cf_gw_account:
    os.environ['CLOUDFLARE_ACCOUNT_ID'] = _cf_gw_account

# ── Distributed cache — Upstash Redis (REST-based, serverless) ────────────────
# Upstash is used for L2 cross-worker cache, anonymous chat history,
# atomic rate-limit credit deduction, and AI response caching.
# Set UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN in Replit Secrets.
# All call sites guard with `if redis_client:` so the app degrades gracefully
# to in-process L1 only when these env vars are absent.
REDIS_URL   = os.environ.get('UPSTASH_REDIS_REST_URL', '').strip()
REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '').strip()
# Upgraded Upstash tier (2026-04-30): longer TTLs — more capacity means more
# aggressive caching benefits chat speed and repeat-query hit rate.
REDIS_AI_CACHE_TTL     = int(os.environ.get('REDIS_AI_CACHE_TTL',     '7200') or '7200')   # 2h (was 1h)
REDIS_CASUAL_CACHE_TTL = int(os.environ.get('REDIS_CASUAL_CACHE_TTL', '600')  or '600')    # 10m (was 5m)
REDIS_CHAT_CACHE_TTL   = int(os.environ.get('REDIS_CHAT_CACHE_TTL',   '1200') or '1200')   # 20m (was 10m)
REDIS_SEARCH_CACHE_TTL = 600   # 10m (was 5m)
REDIS_SESSION_CACHE_TTL = 3600  # 1h (was 30m)
REDIS_RATE_WINDOW = 60

# ── Memorystore-backed AI response cache (Task #609) ────────────────────────
# Single configurable Redis URL — Google Memorystore preferred, any
# Redis-compatible endpoint (rediss:// for TLS, redis:// otherwise) accepted.
# When unset (current default), the AI cache uses per-worker L1 in-memory only.
# LLM upstream caching is handled by Cloudflare AI Gateway with 3600s TTL.
# All values can be tuned per environment without code changes.
def _extract_redis_url(raw: str) -> str:
    """Defensive parser for MEMORYSTORE_REDIS_URL.

    Two common copy-paste mistakes are corrected here so a single bad
    secret doesn't silently degrade the entire AI cache to memory_only:

    1. Operators paste the full Redis CLI command line,
       e.g. ``redis-cli --tls -u rediss://default:TOKEN@host:6379``.
       We extract the substring starting with ``redis://`` /
       ``rediss://`` / ``unix://``.
    2. Operators paste a URL with ``redis://`` (plain TCP)
       instead of ``rediss://`` (TLS). Managed Redis services typically
       require TLS and close plain connections immediately. We auto-upgrade
       any ``redis://*.redis.*`` URL to ``rediss://``.
    """
    raw = (raw or '').strip().strip('"').strip("'")
    if not raw:
        return ''
    import re as _re
    m = _re.search(r'\b(?:rediss?|unix)://\S+', raw)
    url = m.group(0) if m else raw
    # Auto-upgrade plain redis:// to rediss:// for managed Redis services
    if url.startswith('redis://') and any(domain in url for domain in ('upstash.io', 'redis.', 'memorystore.')):
        url = 'rediss://' + url[len('redis://'):]
    return url


# ── Upstash native-protocol L2 AI cache (enabled 2026-04-30) ────────────────
# Upstash exposes a native Redis endpoint at rediss://default:TOKEN@HOST:6379
# alongside the REST API. We derive it automatically from the REST credentials
# already in env — no extra secret needed. This enables aioredis-based L2 so
# the AI response cache is shared across all gunicorn workers (cross-worker
# dedupe). With the upgraded Upstash plan this is safe to turn on:
#   • Higher connection limits (native connections are separate from REST)
#   • Larger data limit — 1 KB average AI answer × 10 000 entries < plan max
#   • TLS is enforced automatically (rediss://)
# Operators can override by setting MEMORYSTORE_REDIS_URL explicitly.
def _build_upstash_native_url(rest_url: str, token: str) -> str:
    """Derive native Redis URL from Upstash REST credentials.
    REST URL example: https://eager-mouse-40471.upstash.io
    Native URL:       rediss://default:TOKEN@eager-mouse-40471.upstash.io:6379
    """
    import re as _re
    rest_url = (rest_url or '').strip()
    token    = (token or '').strip()
    if not rest_url or not token:
        return ''
    host = _re.sub(r'^https?://', '', rest_url).rstrip('/')
    if not host:
        return ''
    return f'rediss://default:{token}@{host}:6379'

_explicit_memstore = os.environ.get('MEMORYSTORE_REDIS_URL', '').strip()
if _explicit_memstore:
    MEMORYSTORE_REDIS_URL = _extract_redis_url(_explicit_memstore)
else:
    # Auto-derive from Upstash REST credentials (upgraded plan — safe to enable)
    _rest_url = os.environ.get('UPSTASH_REDIS_REST_URL', '').strip()
    _rest_tok = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '').strip()
    MEMORYSTORE_REDIS_URL = _build_upstash_native_url(_rest_url, _rest_tok)

REDIS_AI_CACHE_NAMESPACE = (os.environ.get('REDIS_AI_CACHE_NAMESPACE', 'ai_cache').strip() or 'ai_cache')
# Upgraded plan: allow larger cached entries (128 KB vs 64 KB).
REDIS_AI_CACHE_MAX_ENTRY_BYTES = int(os.environ.get('REDIS_AI_CACHE_MAX_ENTRY_BYTES', str(128 * 1024)) or 128 * 1024)
# Slightly more generous timeouts since Upstash upgraded tier has lower p99.
REDIS_AI_CACHE_CONNECT_TIMEOUT_MS = int(os.environ.get('REDIS_AI_CACHE_CONNECT_TIMEOUT_MS', '300') or '300')
REDIS_AI_CACHE_OP_TIMEOUT_MS = int(os.environ.get('REDIS_AI_CACHE_OP_TIMEOUT_MS', '200') or '200')

# ── Slow-query logging ────────────────────────────────────────────────────────
SLOW_QUERY_THRESHOLD_MS = float(os.environ.get("SLOW_QUERY_THRESHOLD_MS", "200"))

# ── Supabase ──────────────────────────────────────────────────────────────────
# `SUPABASE_URL` is the REST API URL (https://<ref>.supabase.co) used by the
# supabase-py client. If only `SUPABASE_DB_URL` is set (the Postgres DSN),
# we derive the REST URL from it to avoid forcing operators to set both.
#
# DSN format examples we handle:
#   postgresql://postgres.<ref>:pwd@aws-1-<region>.pooler.supabase.com:5432/postgres
#   postgresql://postgres:pwd@db.<ref>.supabase.co:5432/postgres
#
# The project ref `<ref>` (e.g. `czeznmqogtwecidhpysa`) lives either in the
# username after `postgres.` (pooler DSN) or in the hostname before
# `.supabase.co` (direct-connect DSN).
def _derive_supabase_url_from_dsn(dsn: str) -> str:
    if not dsn:
        return ''
    try:
        from urllib.parse import urlparse
        u = urlparse(dsn)
        # Pooler form: user is `postgres.<ref>`
        if u.username and '.' in u.username:
            ref = u.username.split('.', 1)[1]
            if ref:
                return f"https://{ref}.supabase.co"
        # Direct form: host is `db.<ref>.supabase.co`
        if u.hostname and u.hostname.endswith('.supabase.co'):
            host_parts = u.hostname.split('.')
            # ['db', '<ref>', 'supabase', 'co']
            if len(host_parts) >= 4 and host_parts[0] == 'db':
                return f"https://{host_parts[1]}.supabase.co"
    except Exception:
        pass
    return ''

SUPABASE_URL         = (
    os.environ.get('SUPABASE_URL', '').strip()
    or _derive_supabase_url_from_dsn(os.environ.get('SUPABASE_DB_URL', '').strip())
)
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '') or os.environ.get('SUPABASE_KEY', '')
SUPABASE_ANON_KEY    = os.environ.get('SUPABASE_ANON_KEY', '') or os.environ.get('SUPABASE_KEY', '')

# ── Cookie security (set SECURE_COOKIES=false in dev to allow HTTP) ───────────
SECURE_COOKIES  = os.environ.get('SECURE_COOKIES', 'true').lower() not in ('false', '0', 'no')
COOKIE_SAMESITE = "lax"
COOKIE_DOMAIN   = os.environ.get('COOKIE_DOMAIN', '').strip() or None

_cors_raw = os.environ.get('CORS_ORIGINS', '').strip().strip('"').strip("'")
if not _cors_raw or _cors_raw == '*':
    CORS_ORIGINS = ["http://localhost", "http://localhost:80", "http://localhost:25144"]
    for _rd in os.environ.get('REPLIT_DOMAINS', '').split(','):
        _rd = _rd.strip()
        if _rd:
            CORS_ORIGINS.append(f"https://{_rd}")
    _CORS_ALLOW_CREDENTIALS = True
else:
    CORS_ORIGINS = [o.strip() for o in _cors_raw.split(',') if o.strip()]
    for _rd in os.environ.get('REPLIT_DOMAINS', '').split(','):
        _rd = _rd.strip()
        if _rd and f"https://{_rd}" not in CORS_ORIGINS:
            CORS_ORIGINS.append(f"https://{_rd}")
    _CORS_ALLOW_CREDENTIALS = True

_HARDCODED_PROD_ORIGINS = [
    "https://syrabit.ai",
    "https://www.syrabit.ai",
    "https://api.syrabit.ai",
]
for _hpo in _HARDCODED_PROD_ORIGINS:
    if _hpo not in CORS_ORIGINS:
        CORS_ORIGINS.append(_hpo)

_prod_origins_raw = os.environ.get('PRODUCTION_ORIGINS', '').strip()
if _prod_origins_raw:
    for _po in _prod_origins_raw.split(','):
        _po = _po.strip()
        if _po and _po not in CORS_ORIGINS:
            CORS_ORIGINS.append(_po)

_default_prod_origins = [
    "https://syrabit.ai",
    "https://www.syrabit.ai",
    "https://api.syrabit.ai",
]
for _dpo in _default_prod_origins:
    if _dpo not in CORS_ORIGINS:
        CORS_ORIGINS.append(_dpo)

_apprunner_url = os.environ.get('APPRUNNER_SERVICE_URL', '').strip().rstrip('/')
if _apprunner_url:
    _ar_origin = _apprunner_url if _apprunner_url.startswith('https://') else f"https://{_apprunner_url}"
    if _ar_origin not in CORS_ORIGINS:
        CORS_ORIGINS.append(_ar_origin)

CORS_ORIGIN_REGEX = None

# ── Admin accounts ────────────────────────────────────────────────────────────
# Admin credentials are now managed entirely via Supabase Auth + the
# `users` table (is_admin=True flag). The old ADMIN_EMAILS / ADMIN_PASSWORDS /
# ADMIN_NAMES env vars have been removed. Set or update admin accounts
# directly in the Supabase dashboard; no Railway redeployment required.
#
# Staff credentials follow the same pattern: staff users sign in via the
# standard Supabase flow and are identified by role='staff' in the users
# table. The STAFF_PASSWORDS env var was only ever needed for the one-time
# seed script (scripts/seed_staff_users.py) and is not used at runtime.

_PG_DSN_RAW = os.environ.get("DATABASE_URL", "") or os.environ.get("SUPABASE_DB_URL", "")
_PG_DSN = _PG_DSN_RAW.strip().strip('"').strip("'").strip()
if _PG_DSN and not _PG_DSN.startswith(("postgresql://", "postgres://")):
    _cfg_log.warning(f"PG DSN invalid scheme — starts with: {_PG_DSN[:20]}...")
    _PG_DSN = ""
_pg_source = "DATABASE_URL" if os.environ.get("DATABASE_URL", "").strip() else ("SUPABASE_DB_URL" if os.environ.get("SUPABASE_DB_URL", "").strip() else "none")
if _PG_DSN:
    try:
        from urllib.parse import urlparse as _urlparse
        _pg_parsed = _urlparse(_PG_DSN)
        _cfg_log.info(f"PG DSN detected (from {_pg_source}) — host={_pg_parsed.hostname}, port={_pg_parsed.port}, user={_pg_parsed.username}, db={_pg_parsed.path}")
    except Exception:
        _cfg_log.info(f"PG DSN detected (from {_pg_source}) — length={len(_PG_DSN)} chars (parse failed)")
else:
    _cfg_log.warning("PG DSN empty — neither DATABASE_URL nor SUPABASE_DB_URL is set")


SARVAM_THINK_BUFFER = 512  # Sarvam-m thinks in ~385 English tokens; give headroom for answer

CONTENT_CACHE_SECONDS = 600
REDIS_CONTENT_PREFIX = "content:"

# ── Plan configuration ────────────────────────────────────────────────────────
# Credits reset daily at midnight UTC.
PLAN_LIMITS = {
    # `req_per_min` for free is the per-anon-IP cap. Bumped 5→15 because a
    # single classroom behind one NAT shares the same IP — 5/min throttled
    # legitimate students at peak usage. 15/min ≈ one chat every 4s, still
    # well below abuse thresholds.
    # ``max_tokens`` is the per-reply UPPER BOUND for the plan, not the
    # default budget. Bumped free 1024 → 10000 so a complex
    # "explain step by step" / "solve every PYQ from this chapter"
    # answer can complete without truncation. The actual per-request
    # budget is now computed dynamically by
    # ``prompts.compute_answer_budget(query, intent, plan_max)``:
    # short / casual queries still get a few hundred tokens, the
    # default factual question gets ~1024–1536 ("medium"), and only
    # long-form / multi-part / "in detail" questions are allowed to
    # scale up toward this ceiling. Daily credit accounting is
    # unaffected (1 reply = 1 credit regardless of length).
    # Only the free-plan ceiling was raised in this change (per request);
    # starter/pro keep their previous 1536/2048 ceilings — paid plans
    # already had headroom and bumping them would change cost/latency
    # behaviour for paying users without it being asked for.
    "free":    {"credits_per_day": 30,   "max_tokens": 10000,  "document_access": "zero",    "req_per_min": 15, "req_per_min_ip": 60},
    "starter": {"credits_per_day": 500,  "max_tokens": 1536,   "document_access": "limited", "req_per_min": 10, "req_per_min_ip": 90},
    "pro":     {"credits_per_day": 4000, "max_tokens": 2048,   "document_access": "full",    "req_per_min": 15, "req_per_min_ip": 120},
}

# Task #793 — coarse per-IP daily ceiling for the free-tier chat. The
# real free-tier 30/day budget is now device-keyed (signed HttpOnly
# cookie minted by ``device_token.mint_device_token``) so school WiFi
# / Jio CGNAT / hostel users no longer drain each other's quota. This
# IP-keyed counter is kept *only* as an abuse cap: a single host
# should not be able to script thousands of chat requests/day even if
# they rotate cookies. Set high enough that a classroom-sized NAT of
# students (say, 30 devices × 30 req/day = 900) running normally
# never trips it. Override via ``IP_COARSE_DAILY_CAP`` env var if a
# specific deployment sees legitimate traffic above the default.
IP_COARSE_DAILY_CAP = int(os.environ.get("IP_COARSE_DAILY_CAP", "1500"))

# Task #797 — cap how often a single IP can mint a fresh device cookie
# in a short window. The first-visit branch in
# ``auth_deps.rate_limit_chat_optional`` lets an anonymous request
# through without a valid cookie by minting one and charging 1 against
# the new token's 30/day budget. A scripted abuser can defeat the
# 30/device cap by simply discarding the cookie on every request, so
# every hit looks like a "first visit" and is only limited by the much
# higher per-IP coarse cap (1500/day default). This per-minute mint
# rate-limit closes that loophole: even if the script never persists
# the cookie, it still gets at most ``DEVICE_COOKIE_MINTS_PER_MIN``
# fresh sessions per minute from a single IP. Real browsers retain the
# cookie they're given and never re-trigger this code path. Override
# via the env var if a deployment terminates an unusually large NAT
# (e.g. a national carrier CGNAT pop) where many genuine first-visits
# legitimately co-occur.
DEVICE_COOKIE_MINTS_PER_MIN = int(os.environ.get("DEVICE_COOKIE_MINTS_PER_MIN", "5"))
PLAN_PRICES = {
    "free":    {"price": 0,   "label": "Free",    "description": "30 credits/day · zero document access"},
    "starter": {"price": 99,  "label": "Starter", "description": "500 credits/day · limited document access"},
    "pro":     {"price": 999, "label": "Pro",      "description": "4,000 credits/day · full document access"},
}

# ── Provider Priority & Credits (Task #250) ──────────────────────────────────
# PROVIDER_PRIORITY: ordered fallback sequence per feature. The rotation pool
# draws by *weight* (not by list position); list order only matters after all
# weighted providers are exhausted (last-resort fallback sequence).
#
# PROVIDER_CREDITS: startup-programme credit amounts (USD). These become the
# draw weights for weighted round-robin. weight=0 means "never in rotation
# pool — last-resort fallback only".
#
# Credit reference table (minimum confirmed startup-programme amounts):
#   vertex        Google Cloud for Startups          $2,000
#   bedrock       AWS Activate                       $1,000
#   azure_openai  Azure for Startups                 $2,500  → fixed weight 1 (second-to-last)
#   sarvam        Sarvam startup credits             $500
#   cartesia      Cartesia startup credits           $500
#   elevenlabs    ElevenLabs startup credits         $500
#   assemblyai    AssemblyAI startup credits         $1,000
#   cohere        Cohere startup credits             $1,000
#   pinecone_ai   Pinecone startup credits           $500
#   exa_ai        Exa startup credits                $1,000
#   tavily        Tavily startup credits             $500
#   mongodb_atlas MongoDB Atlas free tier            $0  (fallback only)
#   workers_ai    Cloudflare free tier               $0  (absolute last resort)
PROVIDER_PRIORITY: dict = {
    # English chat + RAG (Task #267):
    #   Azure OpenAI (gpt-4.1-mini, 2.5k) → Bedrock (nova-micro, 1k) → Workers AI (0, last resort).
    #   Vertex removed from this pool; Azure is now primary for English because gpt-4.1-mini
    #   has the highest production TPS on Azure and $2.5k credits are consumed first.
    "english_rag_chat":  ["azure_openai", "bedrock", "workers_ai"],
    # Assamese chat:
    #   Sarvam (sarvam-m, 500) → Workers AI IndicTrans2 (800) → Vertex / Gemini (2000) last resort.
    #   Sarvam is purpose-built for Indian languages and leads; IndicTrans2 second; Gemini last.
    "assamese_rag_chat": ["sarvam", "workers_ai_indic", "vertex"],
    # Long-form content generation (Task #267): mirrors english_rag_chat.
    "content":           ["azure_openai", "bedrock", "workers_ai"],
    # Assamese content generation:
    #   Workers AI IndicTrans2 (800, primary) → Sarvam (500) → Vertex / Gemini (2000, last resort).
    #   IndicTrans2 leads for Assamese content; Sarvam second; Gemini fallback.
    "assamese_content":  ["workers_ai_indic", "sarvam", "vertex"],
    # Text-to-speech: Cartesia (500) → ElevenLabs (500) → Vertex (2k, RuntimeError→skip) →
    # Bedrock (1k, RuntimeError→skip) → Azure OpenAI (1, RuntimeError→skip) → Workers AI.
    # vertex/bedrock TTS endpoints not wired — listed per authoritative matrix; excluded
    # gracefully when RuntimeError is raised in _synthesize_with_fallback.
    "tts":               ["cartesia", "elevenlabs", "vertex", "bedrock", "azure_openai", "workers_ai"],
    # Speech-to-text: AssemblyAI (1k) → Vertex (2k, RuntimeError→skip) →
    # Bedrock (1k, RuntimeError→skip) → Azure OpenAI (1, RuntimeError→skip) → Workers AI.
    "stt":               ["assemblyai", "vertex", "bedrock", "azure_openai", "workers_ai"],
    # Combined voice pipeline: full union of tts + stt providers per authoritative matrix.
    "voice":             ["assemblyai", "cartesia", "elevenlabs", "vertex", "bedrock", "azure_openai", "workers_ai"],
    # Embeddings: Vertex (2k) → Bedrock Titan (1k) → Cohere (1k) → Azure OpenAI (1, RuntimeError→skip) → Workers AI.
    # cohere.embed_query and bedrock titan embed wired; azure_openai embed endpoint not wired (Task #257).
    "embed":             ["vertex", "bedrock", "cohere", "azure_openai", "workers_ai"],
    # Reranking: Pinecone AI (500) → Cohere (1k, RuntimeError→skip, no /rerank in CF gateway) →
    # Azure OpenAI (1, RuntimeError→skip) → Workers AI.
    # pinecone_ai.rerank wired; cohere/azure rerank endpoint not wired (Task #257).
    "rerank":            ["pinecone_ai", "cohere", "azure_openai", "workers_ai"],
    # Vector search: Pinecone (500) → MongoDB Atlas (0, weight-0 fallback) → Vertex (2k) → Workers AI.
    # Dispatch wired in rag._fetch_chunks_semantic via select_provider("vector_search").
    "vector_search":     ["pinecone_ai", "mongodb_atlas", "vertex", "workers_ai"],
    # Translation: Assamese path.
    #   sarvam(500) → vertex(2k) → workers_ai_indic(POOL_WEIGHTS 3000, IndicTrans2 en→indic)
    #   → bedrock(1k, call_converse) → azure_openai(1, call_chat) → workers_ai(0, last resort).
    #   bedrock + azure_openai retained for contract compatibility (test + call_translate_with_dispatch wired).
    "translate":         ["sarvam", "vertex", "workers_ai_indic", "bedrock", "azure_openai", "workers_ai"],
    # Vision / OCR: Vertex (2k) → Bedrock (1k, Claude multimodal via call_converse_vision) →
    # Azure OpenAI (1, via call_chat with image_url) → Workers AI.
    "vision":            ["vertex", "bedrock", "azure_openai", "workers_ai"],
    # Safety checks: Bedrock (1k, Claude 3.5 Haiku via CF BYOK) → Workers AI.
    # Active by default when CF gateway is configured (ENABLE_LLM_SAFETY_CHECK).
    "safety":            ["bedrock", "workers_ai"],
    # RAG search with external web results: Exa neural search (1k) → Workers AI.
    "search_rag":        ["exa_ai", "workers_ai"],
    # Live / real-time search: Exa (1k) → Tavily (500) → Workers AI.
    # exa_ai and tavily both wired in call_search_rag_with_dispatch.
    "live_search":       ["exa_ai", "tavily", "workers_ai"],
}

PROVIDER_CREDITS: dict = {
    "vertex":           2000,   # Google Cloud for Startups — $2k
    "bedrock":          1000,   # AWS Activate — $1k
    "azure_openai":     2500,   # Azure for Startups — $2.5k; primary for english_rag_chat + content (Task #267)
    "sarvam":            500,   # Sarvam startup credits — $500
    "cartesia":          500,   # Cartesia startup credits — $500
    "elevenlabs":        500,   # ElevenLabs startup credits — $500
    "assemblyai":       1000,   # AssemblyAI startup credits — $1k
    "cohere":           1000,   # Cohere startup credits — $1k
    "pinecone_ai":       500,   # Pinecone startup credits — $500
    "exa_ai":           1000,   # Exa startup credits — $1k
    "tavily":            500,   # Tavily startup credits — $500
    "mongodb_atlas":       0,   # MongoDB Atlas free tier — weight 0 (fallback only)
    "workers_ai":          0,   # Cloudflare free tier — absolute last resort
    "workers_ai_indic":    0,   # CF Workers AI IndicTrans2 — weight comes from POOL_WEIGHTS per-pool overrides
}

# Per-pool weight overrides — take precedence over PROVIDER_CREDITS in select_provider.
# Use this when a provider should have a different priority in one pool vs. the global default.
# Assamese pools need fine-grained ordering that can't be expressed with a single global weight:
#   assamese_content:  IndicTrans2 (3000) → Sarvam (2000) → Vertex (100, true last resort)
#   assamese_rag_chat: Sarvam (3000) → IndicTrans2 (2000) → Vertex (100, true last resort)
#   translate:         IndicTrans2 (3000) → Sarvam (2000) → Vertex (100, true last resort)
POOL_WEIGHTS: dict[str, dict[str, int]] = {
    # assamese_content: IndicTrans2 primary (3000) → Sarvam second (2000) → Vertex last resort (100).
    "assamese_content": {
        "workers_ai_indic": 3000,   # primary — purpose-built Indic neural MT
        "sarvam":           2000,   # second
        "vertex":            100,   # last resort only
    },
    # assamese_rag_chat: Sarvam primary (3000) → IndicTrans2 second (2000) → Vertex last resort (100).
    "assamese_rag_chat": {
        "sarvam":           3000,   # primary — best for Assamese conversational
        "workers_ai_indic": 2000,   # second
        "vertex":            100,   # last resort only
    },
    # translate: IndicTrans2 primary (3000) → Sarvam second (2000) → Vertex (100) →
    #            bedrock/azure_openai at their global credits → workers_ai(0) absolute last.
    "translate": {
        "workers_ai_indic": 3000,   # primary — dedicated Indic MT model
        "sarvam":           2000,   # second
        "vertex":            100,   # third (low weight)
        # bedrock (1000) and azure_openai (2500) use global PROVIDER_CREDITS (no override needed)
        # workers_ai (0) uses global — stays at zero / absolute last resort
    },
}

SEED_DATA = {
    "boards": [
        {"id": "b1", "name": "AHSEC", "slug": "ahsec", "group_name": "AssamBoard", "description": "AssamBoard — AHSEC (Class 11–12)", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "b2", "name": "DEGREE", "slug": "degree", "group_name": "AssamBoard", "description": "AssamBoard — Degree (B.A / B.Com / B.Sc)", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "b3", "name": "SEBA", "slug": "seba", "group_name": "AssamBoard", "description": "AssamBoard — SEBA (Secondary Education)", "created_at": "2024-01-01T00:00:00Z"},
    ],
    "classes": [
        # AHSEC classes
        {"id": "c1", "board_id": "b1", "name": "HS 1st Year", "slug": "hs-1st-year", "description": "Class 11 — AHSEC", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "c2", "board_id": "b1", "name": "HS 2nd Year", "slug": "hs-2nd-year", "description": "Class 12 — AHSEC", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE legacy classes (kept for backward compat)
        {"id": "c3", "board_id": "b2", "name": "2nd Sem", "slug": "2nd-sem", "description": "Degree 2nd Semester", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "c4", "board_id": "b2", "name": "4th Sem", "slug": "4th-sem", "description": "Degree 4th Semester", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE — FYUGP (NEP) Semesters 1–4 (pre-built, linker-discoverable by slug)
        {"id": "c7",  "board_id": "b2", "name": "Semester 1", "slug": "semester-1", "description": "FYUGP 1st Semester — NEP", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "c8",  "board_id": "b2", "name": "Semester 2", "slug": "semester-2", "description": "FYUGP 2nd Semester — NEP", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "c9",  "board_id": "b2", "name": "Semester 3", "slug": "semester-3", "description": "FYUGP 3rd Semester — NEP", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "c10", "board_id": "b2", "name": "Semester 4", "slug": "semester-4", "description": "FYUGP 4th Semester — NEP", "created_at": "2024-01-01T00:00:00Z"},
        # SEBA classes
        {"id": "c5", "board_id": "b3", "name": "Class 9",  "slug": "class-9",  "description": "SEBA Class 9 — Secondary", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "c6", "board_id": "b3", "name": "Class 10", "slug": "class-10", "description": "SEBA Class 10 — Secondary", "created_at": "2024-01-01T00:00:00Z"},
    ],
    "streams": [
        # AHSEC HS 1st Year streams
        {"id": "s13", "class_id": "c1", "name": "Science (PCM)", "slug": "science-pcm", "description": "Physics, Chemistry, Mathematics", "icon": "⚗️", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s14", "class_id": "c1", "name": "Science (PCB)", "slug": "science-pcb", "description": "Physics, Chemistry, Biology",    "icon": "🧬", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s15", "class_id": "c1", "name": "Arts",          "slug": "arts",        "description": "Political Science, History, Economics, Geography", "icon": "📖", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s16", "class_id": "c1", "name": "Commerce",      "slug": "commerce",    "description": "Accountancy, Business Studies, Economics",          "icon": "💼", "created_at": "2024-01-01T00:00:00Z"},
        # AHSEC HS 2nd Year streams
        {"id": "s17", "class_id": "c2", "name": "Science (PCM)", "slug": "science-pcm", "description": "Physics, Chemistry, Mathematics", "icon": "⚗️", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s18", "class_id": "c2", "name": "Science (PCB)", "slug": "science-pcb", "description": "Physics, Chemistry, Biology",    "icon": "🧬", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s19", "class_id": "c2", "name": "Arts",          "slug": "arts",        "description": "Political Science, History, Economics, Geography", "icon": "📖", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s20", "class_id": "c2", "name": "Commerce",      "slug": "commerce",    "description": "Accountancy, Business Studies, Economics",          "icon": "💼", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE 2nd Sem legacy streams
        {"id": "s7",  "class_id": "c3", "name": "B.Com", "slug": "bcom", "description": "Bachelor of Commerce", "icon": "💼", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s8",  "class_id": "c3", "name": "B.A",   "slug": "ba",   "description": "Bachelor of Arts",     "icon": "📖", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s9",  "class_id": "c3", "name": "B.Sc",  "slug": "bsc",  "description": "Bachelor of Science",  "icon": "🔬", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE 4th Sem legacy streams
        {"id": "s10", "class_id": "c4", "name": "B.Com", "slug": "bcom", "description": "Bachelor of Commerce", "icon": "💼", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s11", "class_id": "c4", "name": "B.A",   "slug": "ba",   "description": "Bachelor of Arts",     "icon": "📖", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s12", "class_id": "c4", "name": "B.Sc",  "slug": "bsc",  "description": "Bachelor of Science",  "icon": "🔬", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE FYUGP Semester 1 — 6 NEP course-type streams
        {"id": "s30", "class_id": "c7",  "name": "Major", "slug": "major", "description": "Major Discipline Course",               "icon": "🎯", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s31", "class_id": "c7",  "name": "Minor", "slug": "minor", "description": "Minor Elective Course",                 "icon": "📘", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s32", "class_id": "c7",  "name": "MDC",   "slug": "mdc",   "description": "Multidisciplinary Course",              "icon": "🌐", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s33", "class_id": "c7",  "name": "VAC",   "slug": "vac",   "description": "Value-Added Course",                    "icon": "✨", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s34", "class_id": "c7",  "name": "AEC",   "slug": "aec",   "description": "Ability Enhancement Compulsory Course", "icon": "🧠", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s35", "class_id": "c7",  "name": "SEC",   "slug": "sec",   "description": "Skill Enhancement Course",              "icon": "⚡", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE FYUGP Semester 2 — 6 NEP course-type streams
        {"id": "s36", "class_id": "c8",  "name": "Major", "slug": "major", "description": "Major Discipline Course",               "icon": "🎯", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s37", "class_id": "c8",  "name": "Minor", "slug": "minor", "description": "Minor Elective Course",                 "icon": "📘", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s38", "class_id": "c8",  "name": "MDC",   "slug": "mdc",   "description": "Multidisciplinary Course",              "icon": "🌐", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s39", "class_id": "c8",  "name": "VAC",   "slug": "vac",   "description": "Value-Added Course",                    "icon": "✨", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s40", "class_id": "c8",  "name": "AEC",   "slug": "aec",   "description": "Ability Enhancement Compulsory Course", "icon": "🧠", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s41", "class_id": "c8",  "name": "SEC",   "slug": "sec",   "description": "Skill Enhancement Course",              "icon": "⚡", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE FYUGP Semester 3 — 6 NEP course-type streams
        {"id": "s42", "class_id": "c9",  "name": "Major", "slug": "major", "description": "Major Discipline Course",               "icon": "🎯", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s43", "class_id": "c9",  "name": "Minor", "slug": "minor", "description": "Minor Elective Course",                 "icon": "📘", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s44", "class_id": "c9",  "name": "MDC",   "slug": "mdc",   "description": "Multidisciplinary Course",              "icon": "🌐", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s45", "class_id": "c9",  "name": "VAC",   "slug": "vac",   "description": "Value-Added Course",                    "icon": "✨", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s46", "class_id": "c9",  "name": "AEC",   "slug": "aec",   "description": "Ability Enhancement Compulsory Course", "icon": "🧠", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s47", "class_id": "c9",  "name": "SEC",   "slug": "sec",   "description": "Skill Enhancement Course",              "icon": "⚡", "created_at": "2024-01-01T00:00:00Z"},
        # DEGREE FYUGP Semester 4 — 6 NEP course-type streams
        {"id": "s48", "class_id": "c10", "name": "Major", "slug": "major", "description": "Major Discipline Course",               "icon": "🎯", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s49", "class_id": "c10", "name": "Minor", "slug": "minor", "description": "Minor Elective Course",                 "icon": "📘", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s50", "class_id": "c10", "name": "MDC",   "slug": "mdc",   "description": "Multidisciplinary Course",              "icon": "🌐", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s51", "class_id": "c10", "name": "VAC",   "slug": "vac",   "description": "Value-Added Course",                    "icon": "✨", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s52", "class_id": "c10", "name": "AEC",   "slug": "aec",   "description": "Ability Enhancement Compulsory Course", "icon": "🧠", "created_at": "2024-01-01T00:00:00Z"},
        {"id": "s53", "class_id": "c10", "name": "SEC",   "slug": "sec",   "description": "Skill Enhancement Course",              "icon": "⚡", "created_at": "2024-01-01T00:00:00Z"},
        # SEBA Class 9 streams
        {"id": "s21", "class_id": "c5", "name": "General", "slug": "general", "description": "General stream — SEBA Class 9", "icon": "📚", "created_at": "2024-01-01T00:00:00Z"},
        # SEBA Class 10 streams
        {"id": "s22", "class_id": "c6", "name": "General", "slug": "general", "description": "General stream — SEBA Class 10", "icon": "📚", "created_at": "2024-01-01T00:00:00Z"},
    ],
    "subjects": [],
    "chapters": [],
}

def _generate_chapters():
    return []  # Chapters cleared — upload new syllabus via Admin panel

SEED_DATA["chapters"] = _generate_chapters()

def _fix_chapter_counts():
    ch_count = {}
    for ch in SEED_DATA["chapters"]:
        sid = ch["subject_id"]
        ch_count[sid] = ch_count.get(sid, 0) + 1
    for subj in SEED_DATA["subjects"]:
        subj["chapter_count"] = ch_count.get(subj["id"], 0)

_fix_chapter_counts()
