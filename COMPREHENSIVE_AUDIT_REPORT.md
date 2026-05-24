# Comprehensive Repository Audit Report

**Project:** Syrabit AI - Educational Assistant for Assamese Students  
**Repository:** `-kalukaliya`  
**Audit Date:** 2025-01-20  
**Auditor:** Automated Codebase Analysis  
**Report Version:** 1.0  

---

## Health Score: 6.5 / 10

| Category | Score | Status |
|----------|-------|--------|
| Git Hygiene | 5/10 | Needs improvement |
| Codebase Quality | 7.5/10 | Good |
| CI/CD Workflows | 5/10 | Critical issues |
| Infrastructure as Code | 5.5/10 | Significant gaps |
| Cloud-Native Practices | 7/10 | Good foundation |
| Security | 7/10 | Strong but has gaps |
| Configuration Management | 7/10 | Well-structured |
| Dependency Management | 6/10 | Supply chain good, versioning issues |
| Documentation | 5.5/10 | Outdated and cluttered |

---

## Table of Contents

- [1. Executive Summary](#1-executive-summary)
- [2. Git Hygiene](#2-git-hygiene)
- [3. Codebase Quality](#3-codebase-quality)
- [4. CI/CD Workflows](#4-cicd-workflows)
- [5. Infrastructure as Code (Azure Bicep)](#5-infrastructure-as-code-azure-bicep)
- [6. Cloud-Native Practices](#6-cloud-native-practices)
- [7. Security](#7-security)
- [8. Configuration Management](#8-configuration-management)
- [9. Dependency Management](#9-dependency-management)
- [10. Documentation](#10-documentation)
- [11. Recommendations (Prioritized)](#11-recommendations-prioritized)
- [12. Appendix: File Reference Index](#12-appendix-file-reference-index)

---

## 1. Executive Summary

### Overview

The Syrabit AI codebase implements a hybrid 9-pillar architecture designed to serve Assamese students with AI-powered educational assistance. The architecture spans:

- **Python FastAPI backend** (`apps/backend/`) - Core business logic, AI orchestration, authentication
- **Cloudflare Workers edge layer** (`apps/edge/`) - Request routing, rate limiting, bot detection, JWT validation
- **React/Vite frontend** (`apps/frontend/`) - Student-facing UI
- **Azure Container Apps** (`infra/azure/`) - Production compute infrastructure
- **GitHub Actions CI/CD** (`.github/workflows/`) - Build, test, and deploy automation

### Key Findings

The codebase demonstrates **strong architectural fundamentals** with a well-designed edge-first pattern, proper separation of concerns, and thoughtful security primitives. However, several **critical gaps** exist that could lead to production incidents:

| Severity | Count | Summary |
|----------|-------|---------|
| CRITICAL | 4 | CI deploys broken code, KeyVault inaccessible, JWT secret fallback, debugging workflow exposes secrets |
| HIGH | 7 | Missing health probes, no token revocation, duplicate deploy paths, dependency mismatches |
| MEDIUM | 13 | Missing network isolation, weak password policy, inconsistent CORS, stale documentation |
| LOW | 9 | Dead code, redundant deps, cosmetic issues |

### Verdict

> **Good foundation, needs hardening.** The architecture is sound and security-conscious, but CI/CD fragilities mean broken code can reach production, and infrastructure gaps mean production failures may go undetected. Address CRITICAL items before scaling beyond pilot users.

---

## 2. Git Hygiene

### Strengths

| Item | Detail | File |
|------|--------|------|
| Comprehensive `.gitignore` | Covers Python (`__pycache__`, `.venv`), Node (`node_modules`), IDE (`.vscode`, `.idea`), Docker, secrets (`*.pem`, `.env`) | `.gitignore` |
| `.env.shared` safely committed | Contains only template/placeholder values, no real secrets | `.env.shared` |
| `.gitattributes` configured | Ensures consistent line endings across platforms | `.gitattributes` |
| Main branch as default | Standard convention followed | - |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| MEDIUM | Only single branch (`main`) exists | No develop/staging branches for safe iteration; all changes go directly to production path | Git branch model |
| MEDIUM | Only 1 meaningful commit visible in history | Squash-merged PRs lose commit context; makes bisecting regressions impossible | `git log` |
| MEDIUM | 9+ stale audit/report files cluttering root | Creates confusion about what is current; makes repo look unmaintained | `FULL_STACK_AUDIT.md`, `BUILD_AUDIT_REPORT.md`, `AUDIT_EXECUTIVE_SUMMARY.txt`, `REMEDIATION_PLAN.md`, `FIXES_IMPLEMENTATION_REPORT.md`, `PHASE_1_COMPLETE.txt`, `PHASE_1_FIXES_COMPLETE.md`, `IMPLEMENTATION_PLAN.md`, `ISSUES_CHECKLIST.md` |
| LOW | `.gitremove` file exists | Unclear purpose; likely stale artifact from a cleanup script | `.gitremove` |
| LOW | `.ideavo/` directory committed | Project-specific IDE config that should be in `.gitignore` | `.ideavo/config`, `.ideavo/template` |
| LOW | `frontend/src/` exists alongside `apps/frontend/src/` | Potential dead code duplication from a migration; confuses contributors | `frontend/src/` |

### Recommendations

1. Clean up stale root-level report files (move to `docs/archive/` or delete)
2. Add `.ideavo/` to `.gitignore`
3. Investigate and remove `frontend/src/` if it duplicates `apps/frontend/src/`
4. Implement branch protection rules on `main` (require PR reviews, passing CI)
5. Consider a `develop` branch for integration testing before main

---

## 3. Codebase Quality

### Strengths

| Area | Detail | File |
|------|--------|------|
| Clean monorepo separation | Three distinct apps with clear boundaries | `apps/backend/`, `apps/edge/`, `apps/frontend/` |
| Pydantic BaseSettings | Config validation with type safety and automatic `.env` loading | `apps/backend/app/config.py` |
| Async MongoDB | Motor driver with Beanie ODM for document modeling | `apps/backend/app/db/mongo.py` |
| Circuit breaker pattern | AI provider resilience with automatic fallback | `apps/backend/app/core/circuit_breaker.py` |
| Input sanitization | Prompt injection protection with pattern matching | `apps/backend/app/core/security.py` |
| SSRF protection | Private IP detection blocks internal network access | `apps/backend/app/core/security.py` |
| Proper password hashing | bcrypt with salt rounds | `apps/backend/app/core/security.py` |
| OpenTelemetry integration | Graceful no-op when packages unavailable (doesn't crash in dev) | `apps/backend/app/core/telemetry.py` |
| Type-safe Cloudflare Worker | Full `env.d.ts` interface for worker bindings | `apps/edge/src/env.d.ts` |
| Multi-stage Docker build | Separate build and runtime stages; non-root user (UID 1000) | `apps/backend/Dockerfile` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| HIGH | `_check_rate_limit` uses `redis.incr()` without `await` | The Redis client is `upstash_redis.asyncio.Redis` - calling without await returns a coroutine object that evaluates as truthy, so rate limiting silently never works | `apps/backend/app/api/v1/auth.py` ~line 137 |
| HIGH | `refresh_token_endpoint` has inconsistent async/await patterns | Mix of awaited and non-awaited Redis calls within the same flow; rate limit enforcement is unreliable | `apps/backend/app/api/v1/auth.py` ~line 220 |
| MEDIUM | All config fields are `Optional` with `None` defaults | App can start with zero configuration and fail at runtime instead of at startup; fail-open pattern defeats config validation | `apps/backend/app/config.py` |
| MEDIUM | Python `__init__.py` in TypeScript project | `apps/edge/src/middleware/__init__.py` exists - likely an accidental creation from a Python-oriented tool | `apps/edge/src/middleware/__init__.py` |
| MEDIUM | No shared types/contracts between edge and backend | API contract between CF Worker and FastAPI could drift silently; no compile-time or schema validation across the boundary | Edge-Backend interface |
| LOW | `frontend/src/` directory duplicates `apps/frontend/src/` structure | Dead code from migration or legacy scaffold; confuses new contributors | `frontend/src/` |
| LOW | Dead letter queue service exists but unclear wiring | Service defined but may not be connected to any consumer or error handler | `apps/backend/app/services/dead_letter.py` |
| LOW | Audit model exists with unclear usage | Model defined but no clear API endpoint or scheduled job references it | `apps/backend/app/models/audit.py` |

### Code Pattern Assessment

```
Architecture Pattern: Edge-first with Backend API
Rating: Well-implemented

Request Flow:
  Client -> CF Worker (auth, rate limit, bot detect)
         -> Azure Container App (FastAPI)
         -> AI Providers (circuit breaker pattern)
         -> MongoDB (Beanie ODM)
```

The dual-layer architecture (edge + backend) provides defense-in-depth for rate limiting and authentication. The circuit breaker pattern ensures AI provider failures don't cascade. Overall code quality is above average for an early-stage project.

---

## 4. CI/CD Workflows

### Workflow Inventory

| Workflow | Trigger | Purpose | Status |
|----------|---------|---------|--------|
| `ci-backend.yml` | Push to main (backend paths), PR | Lint, test, deploy backend | Has issues |
| `ci-edge.yml` | Push to main (edge paths), PR | Lint, typecheck, test, deploy edge | CRITICAL issues |
| `ci-frontend.yml` | Push to main (frontend paths), PR | Lint, typecheck, test, deploy frontend | CRITICAL issues |
| `deploy-all.yml` | Push to main | Deploy all services | Conflicts with above |
| `pr-lockfile-refresh.yml` | PR events | Refresh lockfile on PR | OK |
| `regen-lockfile.yml` | Manual dispatch | Regenerate full lockfile | Pushes to main directly |
| `bump-deps.yml` | Manual dispatch | Bump dependency versions | Brittle implementation |
| `_tmp-read-cw-diag.yml` | Manual dispatch | Debug CF Worker diagnostics | Should be removed |

### Strengths

| Item | Detail | File |
|------|--------|------|
| Path-based triggering | Only runs CI for changed apps (efficient) | All `ci-*.yml` |
| Concurrency control | `cancel-in-progress: false` on deploys (safe for production) | `deploy-all.yml` |
| Environment gates | Backend CI uses staging -> production promotion | `ci-backend.yml` |
| Service containers | MongoDB container for integration testing | `ci-backend.yml` |
| Smoke tests | Health, chat endpoint, and frontend reachability verified post-deploy | `ci-backend.yml` |
| Dependabot configured | All ecosystems covered: pip, npm, github-actions | `.github/dependabot.yml` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| CRITICAL | `ci-edge.yml` deploy job has `needs: lint-and-typecheck` but NOT `needs: test` | Broken tests do not block deployment; untested code reaches production | `.github/workflows/ci-edge.yml` (deploy job `needs` array) |
| CRITICAL | `ci-frontend.yml` and `ci-edge.yml` use `continue-on-error: true` on test AND lint steps | Test and lint failures are swallowed silently; CI always reports green | `.github/workflows/ci-frontend.yml`, `.github/workflows/ci-edge.yml` |
| HIGH | `deploy-all.yml` deploys on push to main AND `ci-backend.yml` also deploys on push to main | Duplicate conflicting deploy paths; race conditions between concurrent deploys; unclear which takes precedence | `.github/workflows/deploy-all.yml` vs `.github/workflows/ci-backend.yml` |
| HIGH | Mixed action versions within single workflow files | `actions/checkout@v6` in one job, `@v4` in another within the same file; inconsistent behavior and security posture | `.github/workflows/ci-edge.yml`, `.github/workflows/ci-frontend.yml` |
| MEDIUM | `_tmp-read-cw-diag.yml` is a debugging workflow with AWS Lambda access | Reads environment variables from production Lambda; should not exist in main branch; potential secret exposure in workflow logs | `.github/workflows/_tmp-read-cw-diag.yml` |
| MEDIUM | `bump-deps.yml` uses hardcoded `sed` commands to bump specific versions | Extremely brittle; will silently fail if version patterns don't match; no verification of successful substitution | `.github/workflows/bump-deps.yml` |
| MEDIUM | `--no-frozen-lockfile` used in all `pnpm install` steps | CI should use frozen lockfile (`--frozen-lockfile`) for reproducible builds; current approach allows lockfile drift | All workflow `pnpm install` steps |
| LOW | No required status checks documented | PRs could merge without passing CI if branch protection is not configured in GitHub settings | Repository settings (not in code) |
| LOW | `regen-lockfile.yml` and `bump-deps.yml` push directly to main | Bypasses code review; could introduce breaking changes without PR | `.github/workflows/regen-lockfile.yml`, `.github/workflows/bump-deps.yml` |

### Workflow Dependency Graph (ci-edge.yml - Problematic)

```
lint-and-typecheck -----> deploy (DEPLOYS WITHOUT TEST GATE)
         |
test (runs in parallel but NOT in deploy's needs)
```

**Expected (correct) pattern:**
```
lint-and-typecheck ---+
                      |---> deploy
test ----------------+
```

---

## 5. Infrastructure as Code (Azure Bicep)

### Architecture Overview

```
infra/azure/
  main.bicep              -- Orchestrator (deploys all modules)
  container-app.bicep     -- Azure Container App for backend
  shared-resources.bicep  -- KeyVault, Storage, Log Analytics
  alerts.bicep            -- Azure Monitor alert rules
  search-index.bicep      -- Azure AI Search configuration
```

### Strengths

| Item | Detail | File |
|------|--------|------|
| Modular Bicep structure | `main.bicep` orchestrates child modules cleanly | `infra/azure/main.bicep` |
| HTTP-based autoscaling | Scales at 20 concurrent requests (appropriate for AI workloads) | `infra/azure/container-app.bicep` |
| Log Analytics workspace | 30-day retention for compliance | `infra/azure/shared-resources.bicep` |
| Azure Monitor alerts | 5xx rate, latency, restarts, memory alerts configured | `infra/azure/alerts.bicep` |
| Data residency compliance | India Central region (appropriate for Indian user base) | `infra/azure/main.bicep` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| CRITICAL | KeyVault has `accessPolicies: []` (empty array) | No identity can read secrets from the vault; application cannot access any secrets stored in KeyVault; effectively a locked vault with no keys | `infra/azure/shared-resources.bicep` (KeyVault resource) |
| HIGH | No health probes (liveness/readiness) defined | Container orchestrator cannot detect unhealthy instances; failed containers continue receiving traffic until they crash | `infra/azure/container-app.bicep` (container spec) |
| HIGH | No environment variables passed to container | Container starts with no configuration; secrets must be manually configured outside IaC, defeating the purpose of infrastructure-as-code | `infra/azure/container-app.bicep` (container env) |
| HIGH | Registry auth uses `identity: 'system'` but no managed identity resource defined | Container App cannot pull images from ACR; deployment will fail with auth errors | `infra/azure/container-app.bicep` (registry config) |
| MEDIUM | Storage uses `Standard_LRS` (locally redundant) | No geo-redundancy for production data; single datacenter failure loses all stored data | `infra/azure/shared-resources.bicep` (storage SKU) |
| MEDIUM | Memory alert uses `UsageNanoCores` metric name | This is a CPU metric, not memory; memory alerts will never fire, leaving memory exhaustion undetected | `infra/azure/alerts.bicep` (memory alert rule) |
| MEDIUM | No VNET integration defined | Container App is internet-facing without network isolation; no private endpoints for backing services (MongoDB, Redis, KeyVault) | `infra/azure/container-app.bicep` |
| LOW | Search index name `syrabit-search` may conflict with schema file | Naming should be validated against existing resources to prevent conflicts | `infra/azure/search-index.bicep` |

### Risk Matrix

```
                    HIGH IMPACT
                        |
  KeyVault empty -------+------- No health probes
  policies              |
                        |
  No env vars ----------+------- No managed identity
  in container          |        for registry
                        |
                    LOW IMPACT
                        |
  LRS storage ----------+------- Wrong metric name
                        |
         LOW LIKELIHOOD     HIGH LIKELIHOOD
```

---

## 6. Cloud-Native Practices

### Strengths

| Item | Detail | File |
|------|--------|------|
| Multi-stage Docker build | Reduces final image size; build dependencies not in runtime | `apps/backend/Dockerfile` |
| Non-root user | Container runs as UID 1000 (security best practice) | `apps/backend/Dockerfile` |
| Gunicorn + Uvicorn | CPU*2+1 worker formula; production-grade ASGI serving | `apps/backend/gunicorn_conf.py` |
| Max requests jitter | Workers recycle after N requests (prevents memory leaks) | `apps/backend/gunicorn_conf.py` |
| Docker Compose for local dev | MongoDB + Redis + serverless-redis-http for parity | `docker-compose.yml` |
| Edge-first architecture | CF Worker handles auth/rate-limit/bot-detection before backend sees traffic | `apps/edge/src/index.ts` |
| R2 for static assets | Immutable caching headers for frontend bundles | Edge worker config |
| Horizontal scaling | 1-3 replicas with HTTP-based autoscaling rules | `infra/azure/container-app.bicep` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| HIGH | No `HEALTHCHECK` instruction in Dockerfile | Orchestrator (Docker, Azure Container Apps) cannot detect unhealthy containers; failed processes continue receiving traffic | `apps/backend/Dockerfile` |
| MEDIUM | `docker-compose.yml` uses deprecated `version: '3.8'` key | Docker Compose V2 ignores it, but signals outdated practice; may confuse contributors checking compatibility | `docker-compose.yml` (line 1) |
| MEDIUM | Backend container mounts `./apps/backend/app:/app/app` as volume | Good for hot-reload in dev, but can mask Dockerfile COPY issues (works in dev, fails in production) | `docker-compose.yml` (backend service volumes) |
| MEDIUM | No resource limits in docker-compose | Local dev containers can consume all host CPU/memory; can crash developer machines | `docker-compose.yml` (all services) |
| LOW | No docker-compose healthcheck for mongo/redis services | Backend may start before dependencies are ready; `depends_on` only waits for container start, not service readiness | `docker-compose.yml` (depends_on) |

### Container Security Checklist

| Check | Status | Notes |
|-------|--------|-------|
| Non-root user | PASS | UID 1000 |
| Multi-stage build | PASS | Build deps excluded from runtime |
| No secrets in image | PASS | All config via env vars |
| Minimal base image | PASS | `python:3.11-slim` |
| HEALTHCHECK defined | FAIL | Not present in Dockerfile |
| Read-only filesystem | NOT CHECKED | Not configured in IaC |
| Security scanning | NOT CHECKED | No Trivy/Snyk in CI |

---

## 7. Security

### Strengths

| Area | Implementation | File |
|------|---------------|------|
| JWT with token separation | Proper access/refresh token split with type checking in claims | `apps/backend/app/core/security.py` |
| Edge JWT verification | Web Crypto API (no npm crypto dependency) | `apps/edge/src/middleware/jwt.ts` |
| Turnstile bot protection | Cloudflare Turnstile on sensitive endpoints | `apps/edge/src/index.ts` |
| CSRF origin validation | Middleware validates Origin header against allowlist | `apps/backend/app/core/security.py` |
| Security headers | HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy | `apps/backend/app/main.py` |
| Dual-layer rate limiting | Edge (per-language KV) + Backend (token bucket per user) | `apps/edge/src/middleware/rate-limit.ts`, `apps/backend/app/core/rate_limiter.py` |
| IP-based auth rate limiting | Prevents brute-force login attempts | `apps/backend/app/api/v1/auth.py` |
| Password reset tokens | 1-hour expiry with secure random generation | `apps/backend/app/api/v1/auth.py` |
| Timing-safe responses | Forgot-password doesn't reveal email existence | `apps/backend/app/api/v1/auth.py` |
| SSRF protection | Private IP blocking including AWS metadata endpoint (169.254.169.254) | `apps/backend/app/core/security.py` |
| Input sanitization | Prompt injection patterns detected and blocked | `apps/backend/app/core/security.py` |
| bcrypt password hashing | Industry-standard with proper salt rounds | `apps/backend/app/core/security.py` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| CRITICAL | JWT_SECRET defaults to `"CHANGE_ME_IN_PRODUCTION_AT_LEAST_32_CHARS_LONG"` | If `APP_ENV` is not explicitly set to `"production"`, the insecure default is used; any environment without explicit config (staging, dev deployed instances) uses a known secret | `apps/backend/app/config.py` (JWT_SECRET field) |
| HIGH | JWT uses HS256 (symmetric algorithm) | Same secret shared between edge worker and backend; compromise of either component compromises both; RS256 (asymmetric) preferred for distributed systems | `apps/backend/app/core/security.py`, `apps/edge/src/middleware/jwt.ts` |
| HIGH | No refresh token revocation mechanism | Stolen refresh tokens remain valid for 7 days with no way to invalidate them; no blocklist in Redis | `apps/backend/app/api/v1/auth.py` (refresh endpoint) |
| HIGH | Bot detection only tags requests but does not block | `X-Bot-Detected` header is set but request proceeds; bots still get full service | `apps/edge/src/index.ts` (bot detection middleware) |
| MEDIUM | Password validation only checks `length >= 8` | No uppercase, number, or special character requirements; weak passwords easily brute-forced | `apps/backend/app/api/v1/auth.py` (registration validation) |
| MEDIUM | No account lockout after repeated failed login attempts | Rate limit is per-minute and resets quickly; attacker can sustain slow brute-force indefinitely | `apps/backend/app/api/v1/auth.py` |
| MEDIUM | CORS ALLOWED_ORIGIN is single-value in edge worker | Edge env var supports one origin, but backend config supports comma-separated list; inconsistency may cause CORS errors in multi-domain setups | `apps/edge/src/index.ts` vs `apps/backend/app/config.py` |
| MEDIUM | Turnstile verification is optional | Only verified if token is present in header; attacker can simply omit the `CF-Turnstile-Token` header to bypass bot protection entirely | `apps/edge/src/index.ts` (Turnstile check) |
| LOW | `ip_address_first_seen` stored in user model | Potential GDPR/privacy concern if not disclosed in privacy policy; must be included in data subject access requests | `apps/backend/app/models/user.py` |
| LOW | No Content-Security-Policy header | Backend does not return CSP; leaves frontend vulnerable to XSS via injected scripts | `apps/backend/app/main.py` (security headers middleware) |

### Authentication Flow Diagram

```
[Client] --> [CF Worker Edge]
                |
                +--> Verify JWT (Web Crypto, HS256)
                +--> Rate limit check (KV store)
                +--> Bot detection (UA + behavior)
                +--> Turnstile check (if token present)  <-- WEAKNESS: optional
                |
            [Backend API]
                |
                +--> JWT validation (python-jose, HS256)
                +--> Token bucket rate limit (Redis)
                +--> IP-based auth rate limit
                |
            [Protected Resources]
```

### Threat Model Summary

| Threat | Mitigation | Gap |
|--------|-----------|-----|
| Credential stuffing | IP rate limiting | No account lockout |
| Token theft | Short access token expiry (15min) | No refresh token revocation |
| Bot abuse | Turnstile + UA detection | Optional verification; tags but doesn't block |
| Prompt injection | Input sanitization patterns | Pattern-based (bypassable with novel attacks) |
| SSRF | Private IP blocking | Comprehensive (includes cloud metadata) |
| XSS | Security headers | Missing CSP header |

---

## 8. Configuration Management

### Strengths

| Item | Detail | File |
|------|--------|------|
| 42 environment variables documented | Well-organized sections in shared template | `.env.shared` |
| Pydantic Settings with auto `.env` loading | Type validation, automatic casting, env file support | `apps/backend/app/config.py` |
| Empty string to None conversion | Prevents common misconfiguration where empty string != None | `apps/backend/app/config.py` |
| Production validator | Ensures critical secrets are changed from defaults before production start | `apps/backend/app/config.py` (production validator) |
| Separate `.env.example` for backend | Subset of vars relevant to backend development | `apps/backend/.env.example` |
| `.env.otel` for OpenTelemetry config | Clean separation of observability config | `apps/backend/.env.otel` |
| Wrangler secrets for edge | Sensitive values managed outside code via `wrangler secret` | `apps/edge/wrangler.toml` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| HIGH | `GOOGLE_APPLICATION_CREDENTIALS_JSON` stores full service account JSON in env var | Large JSON in env vars can hit OS/container size limits (~128KB); hard to rotate; secrets visible in process listing; should use mounted file or managed identity | `.env.shared`, `apps/backend/app/config.py` |
| MEDIUM | No validation that `AZURE_BACKEND_URL` in wrangler.toml production env is set | Currently relies on Wrangler secret; if missing, requests proxy to `localhost:8000` (silent failure in production) | `apps/edge/wrangler.toml` (production environment) |
| MEDIUM | Frontend uses `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` | Supabase is listed as P10 (outside 9-pillar architecture in README); architectural inconsistency between docs and implementation | `.env.shared`, `apps/frontend/` |
| LOW | `MONGODB_MAX_POOL_SIZE=50` may be too high | Single Container App with 0.5 CPU; each connection uses memory; 50 connections may cause OOM on small instances | `.env.shared` |
| LOW | `RATE_LIMIT_PRO_TIER=999999` is effectively unlimited | No real upper bound; a compromised pro account can DDoS the system with legitimate-looking traffic | `.env.shared` |

### Configuration Architecture

```
.env.shared (template, committed)
    |
    +-- apps/backend/app/config.py (Pydantic Settings)
    |       |
    |       +-- Validates types
    |       +-- Converts "" to None
    |       +-- Production validator (fail-fast for missing secrets)
    |
    +-- apps/edge/wrangler.toml (non-secret config)
    |       |
    |       +-- Wrangler secrets (sensitive values)
    |
    +-- apps/frontend/ (VITE_ prefixed vars)
            |
            +-- Build-time injection (public, client-visible)
```

---

## 9. Dependency Management

### Strengths

| Item | Detail | File |
|------|--------|------|
| `pip-compile --generate-hashes` | Supply chain security: every Python package verified against cryptographic hash | `apps/backend/requirements.txt` |
| pnpm overrides for known vulns | Patches `esbuild < 0.25.0` and `ws < 8.18.3` | `package.json` (pnpm.overrides) |
| Dependabot enabled | Covers pip, npm (root, frontend, edge), and github-actions ecosystems | `.github/dependabot.yml` |
| Locked pnpm version via corepack | `10.26.1` pinned for reproducible installs | `package.json` (packageManager field) |
| `.node-version` and `.nvmrc` | Node version pinned for all developers | `.node-version`, `.nvmrc` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| HIGH | `requirements.txt` compiled with Python 3.12 but Dockerfile uses `python:3.11-slim` | Hash mismatches possible; packages compiled for 3.12 may not install on 3.11; could cause silent behavior differences | `apps/backend/requirements.txt` (header), `apps/backend/Dockerfile` (FROM line) |
| HIGH | Dockerfile contains hacky `sed` command to remove pytest-asyncio 0.26 and install compatible version | Indicates broken dependency resolution in `requirements.in`; fragile workaround that will break when versions change | `apps/backend/Dockerfile` (pip install section) |
| HIGH | `requirements.in` file is not in the repository | Cannot regenerate `requirements.txt` reproducibly; no source of truth for intended dependencies | Missing from `apps/backend/` |
| MEDIUM | TypeScript 6.0.3 pinned in root `package.json` | Extremely new/bleeding-edge version; may have stability issues; limits contributor onboarding (must use latest TS) | `package.json` (devDependencies) |
| MEDIUM | `@cloudflare/workers-types` at `^4.20260523.1` | Version string format looks unusual (future-dated?); may indicate a nightly/canary build | `apps/edge/package.json` |
| LOW | Frontend has both `playwright` and `@playwright/test` as devDependencies | Redundant; `@playwright/test` includes the core `playwright` package | `apps/frontend/package.json` |
| LOW | `wrangler` at `^4.94.0` | Very high version number; verify this is correct and not a typo | `apps/edge/package.json` |

### Dependency Security Posture

| Layer | Pinning Strategy | Hash Verification | Auto-Update |
|-------|-----------------|-------------------|-------------|
| Python backend | `pip-compile` with exact versions | Yes (SHA256 hashes) | Dependabot |
| Node.js (root) | `pnpm-lock.yaml` | Integrity field in lockfile | Dependabot |
| Node.js (edge) | `pnpm-lock.yaml` | Integrity field in lockfile | Dependabot |
| Node.js (frontend) | `pnpm-lock.yaml` | Integrity field in lockfile | Dependabot |
| GitHub Actions | Major version tags (`@v4`, `@v6`) | No hash pinning | Dependabot |
| Docker base images | Tag-only (`python:3.11-slim`) | No digest pinning | Not automated |

### Recommendation: Pin Docker base images by digest

```dockerfile
# Current (vulnerable to tag mutation):
FROM python:3.11-slim

# Recommended (immutable):
FROM python:3.11-slim@sha256:abc123...
```

---

## 10. Documentation

### Strengths

| Item | Detail | File |
|------|--------|------|
| Comprehensive README | Architecture table, quick start, deployment instructions | `README.md` |
| Architecture documentation | Detailed system design documents | `docs/architecture.md`, `docs/ARCHITECTURE.md` |
| Key rotation procedures | Documented secret rotation steps | `docs/KEY_ROTATION.md` |
| Operations runbook | Incident response and operational procedures | `docs/RUNBOOK.md` |

### Issues

| Severity | Finding | Impact | File/Location |
|----------|---------|--------|---------------|
| HIGH | README says "Node.js 18+" but `package.json` requires `>=22.0.0` | Developers will install Node 18/20, run into cryptic failures, and waste time debugging | `README.md` vs `package.json` (engines field) |
| MEDIUM | README says `npm install` for edge setup but project uses pnpm workspace | Incorrect instructions will fail (`npm install` in a pnpm workspace creates conflicting lockfiles) | `README.md` (edge setup section) |
| MEDIUM | 9+ stale audit/report files at root | Creates confusion about what is current; new contributors don't know which to read | Root directory: `FULL_STACK_AUDIT.md`, `BUILD_AUDIT_REPORT.md`, `AUDIT_EXECUTIVE_SUMMARY.txt`, `REMEDIATION_PLAN.md`, `FIXES_IMPLEMENTATION_REPORT.md`, `PHASE_1_COMPLETE.txt`, `PHASE_1_FIXES_COMPLETE.md`, `IMPLEMENTATION_PLAN.md`, `ISSUES_CHECKLIST.md` |
| MEDIUM | `docs/architecture.md` and `docs/ARCHITECTURE.md` (case difference) | Potential duplication; on case-insensitive filesystems (macOS) these are the same file, on Linux they're different - causes confusion | `docs/` directory |
| LOW | `STACKBLITZ_SETUP.md` and `.replit` file exist | Suggest multiple abandoned development environments; adds confusion about supported dev setups | `STACKBLITZ_SETUP.md`, `.replit` |
| LOW | README references "100k DAU" target | No load testing results or capacity planning docs to support this claim; sets unrealistic expectations | `README.md` |

### Documentation Completeness Matrix

| Topic | Documented? | Location | Quality |
|-------|-------------|----------|---------|
| Architecture overview | Yes | `docs/architecture.md` | Good |
| API reference | No | - | Missing |
| Local development setup | Partial | `README.md` | Outdated commands |
| Deployment procedure | Yes | `README.md` | Good |
| Secret rotation | Yes | `docs/KEY_ROTATION.md` | Good |
| Incident response | Yes | `docs/RUNBOOK.md` | Good |
| Contributing guide | No | - | Missing |
| ADRs (Architecture Decision Records) | No | - | Missing |
| Load testing results | No | - | Missing |
| Data model documentation | No | - | Missing |

---

## 11. Recommendations (Prioritized)

### CRITICAL - Fix Immediately

These issues pose immediate risk to production availability, security, or data integrity.

| # | Issue | Remediation | Effort | Files |
|---|-------|-------------|--------|-------|
| C1 | CI workflows swallow test/lint failures | Remove `continue-on-error: true` from test and lint steps in `ci-edge.yml` and `ci-frontend.yml`; add `test` to deploy job's `needs` array in `ci-edge.yml` | 30 min | `.github/workflows/ci-edge.yml`, `.github/workflows/ci-frontend.yml` |
| C2 | KeyVault has empty access policies | Either add explicit access policies for the Container App's managed identity, or switch to Azure RBAC for KeyVault (recommended) | 2 hours | `infra/azure/shared-resources.bicep` |
| C3 | JWT_SECRET insecure default used outside production | Remove the default value entirely or make the production validator run in ALL non-test environments (not just when `APP_ENV == "production"`) | 1 hour | `apps/backend/app/config.py` |
| C4 | Debugging workflow exposes environment variables | Delete `_tmp-read-cw-diag.yml` entirely, or move to a separate private ops repository | 5 min | `.github/workflows/_tmp-read-cw-diag.yml` |

### HIGH - Fix Before Production Scale

These issues will cause problems at scale or create significant security vulnerabilities.

| # | Issue | Remediation | Effort | Files |
|---|-------|-------------|--------|-------|
| H1 | No health probes in container-app.bicep | Add liveness probe (HTTP GET `/health`) and readiness probe (HTTP GET `/health/ready`) to container template | 1 hour | `infra/azure/container-app.bicep` |
| H2 | No refresh token revocation mechanism | Implement a Redis-backed blocklist; on password change/logout, add refresh token JTI to blocklist; check blocklist on refresh | 4 hours | `apps/backend/app/api/v1/auth.py`, `apps/backend/app/core/security.py` |
| H3 | Duplicate deploy paths (ci-backend + deploy-all) | Choose one deployment strategy: either per-app CI deploys OR unified deploy-all; disable the other; recommend keeping per-app CI deploys and making deploy-all manual-only | 2 hours | `.github/workflows/deploy-all.yml`, `.github/workflows/ci-backend.yml` |
| H4 | `requirements.in` not committed | Create and commit `requirements.in` with direct dependencies; fix Python version to match Dockerfile (3.11); re-run `pip-compile` | 2 hours | `apps/backend/requirements.in` (new), `apps/backend/requirements.txt` |
| H5 | HS256 JWT shared secret across services | Migrate to RS256: backend holds private key (signs), edge worker holds public key (verifies); eliminates shared secret risk | 8 hours | `apps/backend/app/core/security.py`, `apps/edge/src/middleware/jwt.ts`, `apps/backend/app/config.py` |
| H6 | Async/await bug in auth.py rate limiting | Add `await` to all `redis.incr()`, `redis.expire()`, and `redis.get()` calls in `_check_rate_limit` function | 30 min | `apps/backend/app/api/v1/auth.py` (~line 137) |
| H7 | No HEALTHCHECK in Dockerfile | Add `HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -f http://localhost:8000/health \|\| exit 1` | 15 min | `apps/backend/Dockerfile` |

### MEDIUM - Address in Next Sprint

These issues represent technical debt or moderate security gaps.

| # | Issue | Remediation | Effort | Files |
|---|-------|-------------|--------|-------|
| M1 | No branch protection rules | Configure in GitHub: require PR reviews (1+), require status checks, prevent force push to main | 30 min | GitHub repository settings |
| M2 | Inconsistent CI action versions | Standardize all workflows to `actions/checkout@v6`, `actions/setup-node@v4`, `actions/setup-python@v5` | 1 hour | All `.github/workflows/*.yml` |
| M3 | alerts.bicep uses wrong metric for memory | Change `UsageNanoCores` to `WorkingSetBytes` for the memory alert rule | 15 min | `infra/azure/alerts.bicep` |
| M4 | Turnstile verification is optional | Make Turnstile token required on sensitive endpoints (login, register, password reset); return 403 if missing | 2 hours | `apps/edge/src/index.ts` |
| M5 | No VNET integration for container app | Add VNET with subnet delegation; configure private endpoints for MongoDB Atlas, Redis, KeyVault | 8 hours | `infra/azure/container-app.bicep`, `infra/azure/shared-resources.bicep` |
| M6 | Stale report files cluttering root | Move to `docs/archive/` or delete; update `.gitignore` to prevent future accumulation | 30 min | Root directory |
| M7 | README Node.js version requirement wrong | Update README to say "Node.js 22+" matching `package.json` engines field | 5 min | `README.md` |
| M8 | No Content-Security-Policy header | Add CSP header to backend middleware: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'` | 1 hour | `apps/backend/app/main.py` |
| M9 | Password policy too weak | Add validation: minimum 8 chars, at least 1 uppercase, 1 lowercase, 1 number, 1 special character | 1 hour | `apps/backend/app/api/v1/auth.py` |
| M10 | No account lockout mechanism | After 5 failed login attempts, lock account for 15 minutes; store attempt counter in Redis | 3 hours | `apps/backend/app/api/v1/auth.py` |
| M11 | `--no-frozen-lockfile` in CI | Replace with `--frozen-lockfile` in all CI workflow `pnpm install` steps | 30 min | All `.github/workflows/*.yml` |
| M12 | CORS inconsistency between edge and backend | Align ALLOWED_ORIGIN(S) handling: both should support comma-separated list | 1 hour | `apps/edge/src/index.ts`, `apps/backend/app/config.py` |
| M13 | Google credentials as JSON env var | Switch to mounted file (`/secrets/gcp-sa.json`) via volume mount, or use Azure Managed Identity with Workload Identity Federation | 4 hours | `apps/backend/app/config.py`, `infra/azure/container-app.bicep` |

### LOW - Backlog

These are minor issues that improve code quality but don't pose immediate risk.

| # | Issue | Remediation | Effort | Files |
|---|-------|-------------|--------|-------|
| L1 | `frontend/src/` dead code directory | Verify it's unused, then delete entirely | 30 min | `frontend/src/` |
| L2 | Password complexity requirements | (Covered by M9 above) | - | - |
| L3 | `.ideavo/` committed to git | Add to `.gitignore`; remove from tracking with `git rm --cached` | 5 min | `.gitignore`, `.ideavo/` |
| L4 | `RATE_LIMIT_PRO_TIER=999999` effectively unlimited | Set realistic cap (e.g., 1000 requests/minute) | 5 min | `.env.shared`, `apps/backend/app/config.py` |
| L5 | No docker-compose healthchecks | Add healthcheck to mongo and redis services; change backend `depends_on` to use `condition: service_healthy` | 30 min | `docker-compose.yml` |
| L6 | Redundant Playwright packages | Remove `playwright` from devDeps; keep only `@playwright/test` | 5 min | `apps/frontend/package.json` |
| L7 | Python `__init__.py` in TypeScript project | Delete `apps/edge/src/middleware/__init__.py` | 1 min | `apps/edge/src/middleware/__init__.py` |
| L8 | Docker base image not pinned by digest | Pin `python:3.11-slim` by SHA256 digest for immutable builds | 15 min | `apps/backend/Dockerfile` |
| L9 | `STACKBLITZ_SETUP.md` and `.replit` stale | Remove if no longer used; clutters repository | 5 min | `STACKBLITZ_SETUP.md`, `.replit` |

---

## 12. Appendix: File Reference Index

### Files Audited

| File | Section(s) Referenced | Key Findings |
|------|----------------------|--------------|
| `.gitignore` | 2 | Well-configured; comprehensive coverage |
| `.gitattributes` | 2 | Present and correct |
| `.gitremove` | 2 | Stale artifact |
| `.env.shared` | 8 | 42 vars documented; template-only (safe) |
| `.ideavo/config` | 2 | Should be gitignored |
| `.ideavo/template` | 2 | Should be gitignored |
| `.node-version` | 9 | Node version pinning |
| `.nvmrc` | 9 | Node version pinning |
| `.replit` | 10 | Stale dev environment config |
| `package.json` | 9, 10 | TS 6.0.3, engines >=22.0.0 |
| `pnpm-workspace.yaml` | 3 | Workspace configuration |
| `docker-compose.yml` | 6 | Deprecated version key; no resource limits |
| `README.md` | 10 | Node version mismatch; wrong package manager |
| `STACKBLITZ_SETUP.md` | 10 | Stale |
| `apps/backend/Dockerfile` | 6, 9 | Multi-stage good; no HEALTHCHECK; Python version mismatch |
| `apps/backend/app/config.py` | 3, 7, 8 | JWT default insecure; all fields Optional |
| `apps/backend/app/main.py` | 7 | Security headers present; missing CSP |
| `apps/backend/app/core/security.py` | 3, 7 | Strong: bcrypt, SSRF protection, sanitization |
| `apps/backend/app/core/circuit_breaker.py` | 3 | Good resilience pattern |
| `apps/backend/app/core/rate_limiter.py` | 7 | Token bucket implementation |
| `apps/backend/app/core/telemetry.py` | 3 | Graceful OTel integration |
| `apps/backend/app/api/v1/auth.py` | 3, 7 | Async/await bug; no token revocation |
| `apps/backend/app/api/v1/health.py` | 6 | Health endpoint exists |
| `apps/backend/app/db/mongo.py` | 3 | Async Motor + Beanie ODM |
| `apps/backend/app/db/redis.py` | 3 | Upstash async Redis |
| `apps/backend/app/models/user.py` | 7 | IP storage privacy concern |
| `apps/backend/app/models/audit.py` | 3 | Unclear usage |
| `apps/backend/app/services/dead_letter.py` | 3 | Unclear wiring |
| `apps/backend/gunicorn_conf.py` | 6 | Proper worker configuration |
| `apps/backend/requirements.txt` | 9 | Hash-pinned; compiled with wrong Python |
| `apps/backend/.env.example` | 8 | Backend-specific template |
| `apps/backend/.env.otel` | 8 | OTel-specific config |
| `apps/edge/src/index.ts` | 3, 7 | Bot detection doesn't block; optional Turnstile |
| `apps/edge/src/middleware/jwt.ts` | 7 | Web Crypto JWT verification |
| `apps/edge/src/middleware/rate-limit.ts` | 7 | KV-based rate limiting |
| `apps/edge/src/middleware/__init__.py` | 3 | Accidental Python file in TS project |
| `apps/edge/src/env.d.ts` | 3 | Type-safe bindings |
| `apps/edge/wrangler.toml` | 8 | Production URL validation gap |
| `apps/edge/package.json` | 9 | Unusual version strings |
| `apps/frontend/package.json` | 9 | Redundant Playwright deps |
| `infra/azure/main.bicep` | 5 | Orchestrator module; India Central |
| `infra/azure/container-app.bicep` | 5, 6 | No probes; no env vars; no VNET |
| `infra/azure/shared-resources.bicep` | 5 | Empty KeyVault policies; LRS storage |
| `infra/azure/alerts.bicep` | 5 | Wrong metric name for memory |
| `infra/azure/search-index.bicep` | 5 | Potential naming conflict |
| `.github/workflows/ci-backend.yml` | 4 | Staging promotion good; duplicates deploy-all |
| `.github/workflows/ci-edge.yml` | 4 | CRITICAL: tests don't block deploy |
| `.github/workflows/ci-frontend.yml` | 4 | CRITICAL: continue-on-error on tests |
| `.github/workflows/deploy-all.yml` | 4 | Conflicts with per-app CI deploys |
| `.github/workflows/bump-deps.yml` | 4 | Brittle sed-based version bumping |
| `.github/workflows/regen-lockfile.yml` | 4 | Pushes directly to main |
| `.github/workflows/pr-lockfile-refresh.yml` | 4 | OK |
| `.github/workflows/_tmp-read-cw-diag.yml` | 4 | CRITICAL: debugging workflow; remove |
| `.github/dependabot.yml` | 4, 9 | Well-configured |
| `.github/scripts/four_cloud_delegation_drift.sh` | - | Drift detection script |
| `scripts/verify-before-merge.sh` | - | Pre-merge verification |
| `docs/architecture.md` | 10 | Architecture documentation |
| `docs/ARCHITECTURE.md` | 10 | Potential duplicate (case difference) |
| `docs/KEY_ROTATION.md` | 10 | Secret rotation procedures |
| `docs/RUNBOOK.md` | 10 | Operations runbook |

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total files audited | 55+ |
| Critical findings | 4 |
| High findings | 7 |
| Medium findings | 13 |
| Low findings | 9 |
| Total findings | 33 |
| Estimated remediation effort (Critical + High) | ~20 hours |
| Estimated remediation effort (All) | ~45 hours |

---

## Methodology

This audit was conducted through static analysis of:
- Source code and configuration files
- CI/CD workflow definitions
- Infrastructure-as-Code templates
- Dependency manifests and lockfiles
- Documentation files
- Git history and repository structure

The audit does NOT include:
- Dynamic/runtime testing
- Penetration testing
- Load/performance testing
- Third-party dependency vulnerability scanning (beyond version analysis)
- Cloud resource configuration audit (only IaC templates reviewed)

---

*End of Report*
