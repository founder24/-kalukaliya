# Environment Variables Documentation

> **v3 SUPERSEDES (2026-05-04):** the canonical infra spec is
> [`infra/per-cloud-feature-delegation.md`](infra/per-cloud-feature-delegation.md)
> + [`infra/provider-priority-map.md`](infra/provider-priority-map.md)
> + [`infra/credit-burn-runbook.md`](infra/credit-burn-runbook.md).
> Provider removals (OpenAI, Anthropic, Bedrock-direct, Stripe, Quge5,
> Resend, Grok, Railway, DigitalOcean) are tracked in Task #347 — any
> env vars below for those providers are historical/no-op. If anything
> below disagrees with v3, the v3 docs win.

This document provides comprehensive documentation of all environment variables required and supported by the Syrabit.ai backend application.

## Configuration Management

The application uses **pydantic-settings** for centralized, validated configuration management. All environment variables are defined in `config.py` with proper validation, type coercion, and documentation.

### Accessing Configuration

```python
from config import env, Configurator

# Access validated settings (recommended)
mongo_uri = env.mongo_url
jwt_secret = env.jwt_secret

# Runtime overrides (for dynamic values like refresh tokens)
Configurator.set_runtime_env('SUPABASE_SERVICE_KEY', 'new_key')
value = Configurator.get('SUPABASE_SERVICE_KEY', 'default')
```

---

## Required Environment Variables

These variables **must** be set for the application to start in production:

### 🔐 Security & Authentication

| Variable | Description | Validation | Example |
|----------|-------------|------------|---------|
| `JWT_SECRET` | JWT signing secret for user tokens | Min 64 chars, high entropy required | `supersecretkey...` (64+ chars) |
| `ADMIN_JWT_SECRET` | JWT signing secret for admin tokens | Min 64 chars, must differ from JWT_SECRET | `adminsecretkey...` (64+ chars) |

> ⚠️ **Critical**: These secrets must be explicitly set. The application will refuse to start if they are missing in non-test environments. Never use deterministic fallbacks.

### 🗄️ Database

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `MONGO_URL` or `MONGODB_URI` | MongoDB connection URI | `mongodb://localhost:27017` | `mongodb+srv://user:pass@cluster.mongodb.net` |
| `DB_NAME` | Database name | `test_database` | `syrabit_prod` |

### 📧 Email

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `RESEND_API_KEY` | Resend API key for transactional emails | Required for email functionality | `re_...` |
| `EMAIL_FROM` | From address for outgoing emails | `noreply@syrabit.ai` | `support@syrabit.ai` |
| `FRONTEND_URL` | Frontend URL for redirects | `https://syrabit.ai` | `https://app.syrabit.ai` |

### 🔑 LLM Provider Keys (At least one required)

| Variable | Description | Provider |
|----------|-------------|----------|
| `GROQ_API_KEY` | Primary Groq API key | Groq |
| `GEMINI_API_KEY` | Primary Gemini API key | Google Vertex AI |
| `OPENAI_API_KEY` | OpenAI API key | OpenAI |
| `XAI_API_KEY` | xAI API key | xAI |
| `SARVAM_API_KEY` | Primary Sarvam API key | Sarvam |
| `CEREBRAS_API_KEY` | Cerebras API key | Cerebras |
| `OPENROUTER_API_KEY` | OpenRouter API key | OpenRouter |

---

## Optional Environment Variables

### 🔐 Additional Security

| Variable | Description | Default |
|----------|-------------|---------|
| `JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `JWT_ACCESS_EXPIRE_MINUTES` | Access token expiry (minutes) | `60` |
| `JWT_REFRESH_EXPIRE_MINUTES` | Refresh token expiry (minutes) | `43200` (30 days) |
| `SECURE_COOKIES` | Enable secure cookie flag | `True` |
| `COOKIE_DOMAIN` | Cookie domain | `""` |

### 🌐 OAuth & Authentication

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | `""` |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | `""` |

### ☁️ Cloudflare

#### API Tokens

| Variable | Description | Aliases/Fallbacks |
|----------|-------------|-------------------|
| `CLOUDFLARE_ANALYTICS_TOKEN` | Cloudflare analytics token (preferred) | `CF_ANALYTICS_API_TOKEN`, `CLOUDFLARE_API_TOKEN` |
| `CLOUDFLARE_PAGES_TOKEN` | Cloudflare Pages deployment token (preferred) | `CF_PAGES_API_TOKEN` |
| `CLOUDFLARE_API_TOKEN` | General Cloudflare API token (fallback) | - |
| `CF_ZONE_ID` | Cloudflare Zone ID | - |
| `CF_API_TOKEN` | Legacy CF API token alias | - |

#### AI Gateway

| Variable | Description | Default |
|----------|-------------|---------|
| `CF_AI_GATEWAY_ACCOUNT_ID` | Cloudflare AI Gateway account ID | `""` |
| `CF_AI_GATEWAY_ID` | Cloudflare AI Gateway ID | `""` |
| `CF_AI_GATEWAY_TOKEN` | Authenticated gateway bearer token | `""` |
| `CF_AI_GATEWAY_CACHE_TTL` | AI Gateway cache TTL (seconds) | `3600` |

#### Turnstile

| Variable | Description | Default |
|----------|-------------|---------|
| `CF_TURNSTILE_SECRET_KEY` | Cloudflare Turnstile secret key | `""` |

#### Access / Zero Trust

| Variable | Description | Default |
|----------|-------------|---------|
| `CF_ACCESS_TEAM_DOMAIN` | Cloudflare Access team domain | `""` |
| `CF_ACCESS_AUD_ADMIN` | Audience tag for admin routes | `""` |
| `CF_ACCESS_AUD_INTERNAL` | Audience tag for internal routes | `""` |
| `CF_ACCESS_ENFORCE` | Enable Access enforcement | `False` |

### 🤖 LLM Providers (Secondary Keys)

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY_2` | Secondary Groq API key for failover |
| `GEMINI_API_KEY_2` | Secondary Gemini API key for failover |
| `SARVAM_API_KEY_2` | Secondary Sarvam API key |
| `SARVAM_API_KEY_3` | Tertiary Sarvam API key |

### 🧠 Vertex AI

| Variable | Description | Default |
|----------|-------------|---------|
| `VERTEX_PROJECT_ID` | GCP Vertex AI project ID | `""` |
| `VERTEX_LOCATION` | Vertex AI location | `us-central1` |
| `VERTEX_GEMINI_MODEL` | Vertex Gemini model | `gemini-2.5-flash` |
| `VERTEX_SERVICE_ACCOUNT_JSON` | Service account JSON credentials | `""` |
| `CHAT_DEFAULT_MODEL` | Default chat model override | `""` |

### 🗃️ Redis / Cache

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis URL (legacy) | `""` |
| `REDIS_TOKEN` | Redis auth token (legacy) | `""` |
| `MEMORYSTORE_REDIS_URL` | Managed Redis URL (preferred) | `""` |
| `REDIS_AI_CACHE_NAMESPACE` | Redis namespace for AI cache | `ai_cache` |
| `REDIS_AI_CACHE_TTL` | AI cache TTL (seconds) | `3600` |
| `REDIS_CASUAL_CACHE_TTL` | Casual cache TTL (seconds) | `300` |
| `REDIS_AI_CACHE_MAX_ENTRY_BYTES` | Max cache entry size | `65536` |
| `REDIS_AI_CACHE_CONNECT_TIMEOUT_MS` | Redis connect timeout (ms) | `200` |
| `REDIS_AI_CACHE_OP_TIMEOUT_MS` | Redis operation timeout (ms) | `150` |

### 🏗️ Supabase

| Variable | Description | Aliases |
|----------|-------------|---------|
| `SUPABASE_URL` | Supabase REST API URL | - |
| `SUPABASE_SERVICE_KEY` | Supabase service role key | - |
| `SUPABASE_ANON_KEY` | Supabase anon/public key | - |
| `SUPABASE_KEY` | Legacy Supabase key alias | Fallback for SERVICE_KEY |

### 🌍 CORS & Network

| Variable | Description | Default |
|----------|-------------|---------|
| `CORS_ORIGINS` | Comma-separated CORS origins | `""` |
| `PRODUCTION_ORIGINS` | Additional production origins | `""` |
| `REPLIT_DOMAINS` | Replit deployment domains | `""` |
| `APPRUNNER_SERVICE_URL` | App Runner service URL | `""` |

### 👥 Admin Accounts

| Variable | Description | Format |
|----------|-------------|--------|
| `ADMIN_EMAILS` | Comma-separated admin emails | `admin1@example.com,admin2@example.com` |
| `ADMIN_PASSWORDS` | Comma-separated admin passwords | `pass1,pass2` |
| `ADMIN_NAMES` | Comma-separated admin names | `Admin One,Admin Two` |
| `ENABLE_E2E_ADMIN` | Enable E2E test admin account | `False` |

### ⚡ Rate Limiting & Performance

| Variable | Description | Default |
|----------|-------------|---------|
| `IP_COARSE_DAILY_CAP` | Per-IP daily rate cap | `1500` |
| `DEVICE_COOKIE_MINTS_PER_MIN` | Device cookie mint rate | `5` |
| `SLOW_QUERY_THRESHOLD_MS` | Slow query threshold (ms) | `200.0` |

### 🎯 LLM Routing

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | Explicit LLM provider override | `""` |
| `LLM_MODEL` | Explicit LLM model override | `""` |

### 🍪 Chat Features

| Variable | Description | Default |
|----------|-------------|---------|
| `CHAT_ENHANCE_ENABLED` | Enable cognitive anchors in chat | `True` |

### 🇮🇳 Sarvam Localization

| Variable | Description | Default |
|----------|-------------|---------|
| `SARVAM_BASE_URL` | Sarvam API base URL | `https://api.sarvam.ai` |
| `SARVAM_THINK_BUFFER` | Sarvam thinking buffer setting | `""` |
| `SARVAM_TRANSLATE_KEY` | Sarvam translation API key | `""` |

### 🛠️ AWS Credentials

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_ACCESS_KEY_ID` | AWS access key | `""` |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | `""` |
| `AWS_REGION` | AWS region | `us-east-1` |

### 📊 Database (PostgreSQL)

| Variable | Description | Aliases |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL DSN | - |
| `SUPABASE_DB_URL` | Supabase PostgreSQL DSN | - |

---

## Environment-Specific Configurations

### Development (.env)

```bash
# Database
MONGO_URL=mongodb://localhost:27017
DB_NAME=syrabit_dev

# JWT Secrets (generate strong random values)
JWT_SECRET=your-dev-jwt-secret-min-64-chars-long-random-string-here
ADMIN_JWT_SECRET=your-admin-jwt-secret-min-64-chars-different-from-above

# Email (optional in dev)
RESEND_API_KEY=re_test_xxx
EMAIL_FROM=noreply@localhost
FRONTEND_URL=http://localhost:3000

# CORS for local development
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# Disable secure cookies in dev
SECURE_COOKIES=false

# Optional: Add API keys for testing
GROQ_API_KEY=gsk_xxx
```

### Production

```bash
# Database
MONGO_URL=mongodb+srv://user:pass@cluster.mongodb.net/syrabit_prod
DB_NAME=syrabit_prod

# JWT Secrets (CRITICAL: Use strong, unique values)
JWT_SECRET=<64+ char random string from password manager>
ADMIN_JWT_SECRET=<different 64+ char random string>

# Email
RESEND_API_KEY=re_live_xxx
EMAIL_FROM=support@syrabit.ai
FRONTEND_URL=https://syrabit.ai

# CORS
CORS_ORIGINS=https://syrabit.ai,https://www.syrabit.ai

# Secure cookies in production
SECURE_COOKIES=true

# At least one LLM provider
GROQ_API_KEY=gsk_live_xxx
GEMINI_API_KEY=xxx

# Cloudflare (if using)
CF_AI_GATEWAY_ACCOUNT_ID=xxx
CF_AI_GATEWAY_ID=xxx
CF_AI_GATEWAY_TOKEN=Bearer xxx
CF_ZONE_ID=xxx
CLOUDFLARE_ANALYTICS_TOKEN=CFToken_xxx

# Redis (production cache)
MEMORYSTORE_REDIS_URL=redis://xxx

# Supabase (if using)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJxxx
SUPABASE_ANON_KEY=eyJxxx
```

### Testing (pytest)

```bash
# pytest automatically generates ephemeral secrets
# No need to set JWT_SECRET or ADMIN_JWT_SECRET
PYTEST_CURRENT_TEST=1

# Test database
MONGO_URL=mongodb://localhost:27017/test_db
DB_NAME=test_database
```

---

## Best Practices

### 1. Secret Management

- ✅ Use a secrets manager (e.g., AWS Secrets Manager, HashiCorp Vault)
- ✅ Generate secrets with at least 64 characters of entropy
- ✅ Rotate secrets regularly
- ❌ Never commit secrets to version control
- ❌ Never use deterministic fallbacks for security-critical values

### 2. Environment Files

```bash
# .env (development - safe to commit template without values)
cp .env.example .env
# Edit .env with your local values (add to .gitignore)

# .env.production (production - never commit)
# Store securely in secrets manager or CI/CD system
```

### 3. Validation

The application validates critical settings on startup:

```python
# config.py automatically validates:
# - JWT_SECRET: min 64 chars, high entropy
# - ADMIN_JWT_SECRET: min 64 chars, differs from JWT_SECRET
# - Required vars in production mode
```

### 4. Runtime Configuration Changes

For dynamic values (e.g., refresh tokens), use the Configurator:

```python
from config import Configurator

# Safe way to update env vars at runtime
Configurator.set_runtime_env('SUPABASE_SERVICE_KEY', new_key)

# Retrieve with fallback
value = Configurator.get('KEY', 'default')
```

### 5. Type Safety

All settings are typed and validated:

```python
from config import env

# IDE autocomplete and type checking work
ttl: int = env.redis_ai_cache_ttl  # Automatically coerced to int
enabled: bool = env.chat_enhance_enabled  # Automatically coerced to bool
```

---

## Troubleshooting

### Application Won't Start

**Error**: `ValueError: JWT_SECRET must be set`

**Solution**: Set both `JWT_SECRET` and `ADMIN_JWT_SECRET` with strong random values.

### Module Import Errors

**Error**: `ModuleNotFoundError: No module named 'pydantic_settings'`

**Solution**: Install dependencies:
```bash
pip install pydantic-settings>=2.0.0 python-dotenv
```

### Configuration Not Loading

**Check**:
1. `.env` file exists in project root
2. Environment variables are exported in shell
3. No typos in variable names (case-sensitive)

### Tests Failing

**Solution**: Ensure `PYTEST_CURRENT_TEST` is set or run via pytest:
```bash
pytest  # Automatically sets up test environment
```

---

## Migration from Direct os.environ Access

### Before (❌ Avoid)
```python
import os
key = os.environ['API_KEY']  # Raises KeyError if not set
os.environ['DYNAMIC_KEY'] = value  # Race condition risk
```

### After (✅ Recommended)
```python
from config import env, Configurator

# Read with validation
key = env.groq_api_key  # Type-safe, validated

# Or with fallback
key = Configurator.get('API_KEY', 'default')

# Write safely
Configurator.set_runtime_env('DYNAMIC_KEY', value)
```

---

## Complete Variable List (Alphabetical)

```
ADMIN_EMAILS
ADMIN_JWT_SECRET
ADMIN_NAMES
ADMIN_PASSWORDS
APPRUNNER_SERVICE_URL
AWS_ACCESS_KEY_ID
AWS_REGION
AWS_SECRET_ACCESS_KEY
CEREBRAS_API_KEY
CF_ACCESS_AUD_ADMIN
CF_ACCESS_AUD_INTERNAL
CF_ACCESS_ENFORCE
CF_ACCESS_TEAM_DOMAIN
CF_AI_GATEWAY_ACCOUNT_ID
CF_AI_GATEWAY_CACHE_TTL
CF_AI_GATEWAY_ID
CF_AI_GATEWAY_TOKEN
CF_ANALYTICS_API_TOKEN
CF_API_TOKEN
CF_PAGES_API_TOKEN
CF_TURNSTILE_SECRET_KEY
CF_ZONE_ID
CHAT_DEFAULT_MODEL
CHAT_ENHANCE_ENABLED
CLOUDFLARE_ANALYTICS_TOKEN
CLOUDFLARE_API_TOKEN
CLOUDFLARE_PAGES_TOKEN
COOKIE_DOMAIN
CORS_ORIGINS
DATABASE_URL
DB_NAME
DEVICE_COOKIE_MINTS_PER_MIN
EMAIL_FROM
ENABLE_E2E_ADMIN
FRONTEND_URL
GEMINI_API_KEY
GEMINI_API_KEY_2
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GROQ_API_KEY
GROQ_API_KEY_2
IP_COARSE_DAILY_CAP
JWT_ACCESS_EXPIRE_MINUTES
JWT_ALGORITHM
JWT_REFRESH_EXPIRE_MINUTES
JWT_SECRET
LLM_MODEL
LLM_PROVIDER
MEMORYSTORE_REDIS_URL
MONGODB_URI
MONGO_URL
OPENAI_API_KEY
OPENROUTER_API_KEY
PRODUCTION_ORIGINS
REDIS_AI_CACHE_CONNECT_TIMEOUT_MS
REDIS_AI_CACHE_MAX_ENTRY_BYTES
REDIS_AI_CACHE_NAMESPACE
REDIS_AI_CACHE_OP_TIMEOUT_MS
REDIS_AI_CACHE_TTL
REDIS_CASUAL_CACHE_TTL
REDIS_TOKEN
REDIS_URL
REPLIT_DOMAINS
RESEND_API_KEY
SARVAM_API_KEY
SARVAM_API_KEY_2
SARVAM_API_KEY_3
SARVAM_BASE_URL
SARVAM_THINK_BUFFER
SARVAM_TRANSLATE_KEY
SECURE_COOKIES
SLOW_QUERY_THRESHOLD_MS
SUPABASE_ANON_KEY
SUPABASE_DB_URL
SUPABASE_KEY
SUPABASE_SERVICE_KEY
SUPABASE_URL
VERTEX_GEMINI_MODEL
VERTEX_LOCATION
VERTEX_PROJECT_ID
VERTEX_SERVICE_ACCOUNT_JSON
XAI_API_KEY
```

---

## Support

For questions about configuration:
- Check `config.py` for field descriptions and validation rules
- Review this documentation for usage examples
- Contact the development team for production deployment assistance
