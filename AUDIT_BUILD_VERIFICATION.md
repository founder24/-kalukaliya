# Build & Test Verification Audit

**Date:** 2025-07-01  
**Environment:** Python 3.11.15, Node v22.22.3, pnpm 10.26.1  
**Network mode:** OPEN_INTERNET

---

## Summary

| Layer | Dependency Install | Build/Type-check | Tests | Pass | Fail | Total |
|-------|-------------------|------------------|-------|------|------|-------|
| Backend (Python/FastAPI) | OK | N/A (no build step) | pytest | 244 | 0 | 244 |
| Edge (TypeScript/CF Worker) | OK | tsc --noEmit PASS | vitest | 75 | 2 | 77 |
| Frontend (React/Vite) | OK | vite build PASS | vitest | 579 | 5 | 584 |

**Overall verdict:** All dependency installs and builds succeed. 7 test failures across edge and frontend (pre-existing issues, not introduced by this audit).

---

## 1. Backend (apps/backend)

### Dependency Install

```
pip install -r requirements.txt  # Python 3.11.15
pip install pytest pytest-asyncio pytest-cov pytest-env
```

**Result:** SUCCESS. All packages installed. Note: `requirements.txt` is pinned for Python 3.11; will not install on Python 3.9/3.10 due to `aiohappyeyeballs==2.6.2` requiring Python >=3.10 and the exact version only existing for 3.11+.

### Tests

```
APP_ENV=test JWT_SECRET=test-secret-at-least-32-characters-long pytest tests/ -v --tb=short
```

**Result:** 244 passed, 0 failed (8.06s)

**Warnings (non-blocking):**
- `StarletteDeprecationWarning`: httpx usage deprecated in favor of httpx2
- `DeprecationWarning`: per-request cookies= pattern is deprecated in starlette/httpx

---

## 2. Edge Worker (apps/edge)

### Dependency Install

```
pnpm install  # from workspace root
```

**Result:** SUCCESS. 790 packages installed (8.2s). TypeScript 6.0.3 in use.

### TypeScript Check

```
npx tsc --noEmit
```

**Result:** PASS -- zero errors.

### Tests

```
npx vitest run
```

**Result:** 75 passed, 2 failed (1.49s)

**Failed tests:**

1. **`tests/api-proxy.test.ts` > Successful proxy: passes through response from backend**
   - Assertion: `expected null to be 'https://syrabit.ai'`
   - The test expects `Access-Control-Allow-Origin` header in proxy response, but `proxyRequest` does not set CORS headers (CORS is handled by separate middleware).

2. **`tests/edge-caching.test.ts` > stores redirect response in cache on cache miss**
   - Assertion: `expected "spy" to be called at least once` (ctx.waitUntil)
   - The caching logic path for redirect responses does not call `waitUntil` as the test expects.

**Note:** Both failures appear to be test-expectation drift rather than production bugs.

---

## 3. Frontend (apps/frontend)

### Dependency Install

Shared with edge via pnpm workspace (already installed above).

### Production Build

```
npx vite build
```

**Result:** PASS (32.53s). Output written to `dist/`.

**Warnings (non-blocking):**
- 3 circular chunk warnings (vfile-message, parse-entities, victory-vendor)
- 20 empty chunk warnings (tree-shaken dependencies)
- Custom plugin `syrabit-preload-headers-inject` wrote 5 Link preload headers to `dist/_headers`

**Largest bundles:**
- `charts-DGRhYVfR.js`: 317 KB
- `markdown-DZFnfD4H.js`: 260 KB
- `AdminHealth-BX-nwp_T.js`: 252 KB
- `AdminDashboard-BM37ytrd.js`: 210 KB

### Tests

```
npx vitest run
```

**Result:** 579 passed, 5 failed across 63 test files (26.75s)

**Failed tests (all in `src/components/admin/AdminHealth.credits.test.jsx`):**

1. **shows "not configured" text and setup instructions when API returns configured: false** (GCP section)
2. **shows "not configured" state (not error banner) when API responds with 404** (GCP section)
3. **renders grant total, spend MTD, remaining, and runway values when configured** (GCP section)
4. **shows "Credits Low" badge and applies red text when credits_low is true** (GCP section)
5. **shows red days-remaining text when expiry is within 60 days** (GCP section)

All 5 failures are `TestingLibraryElementError: Unable to find an element` -- the component renders an "AWS Infra" panel instead of expected GCP Credits content. This suggests the AdminHealth panel ordering or conditional rendering changed without test updates.

**stderr warnings (non-blocking):**
- `[Syrabit] VITE_BACKEND_URL is not set. API requests will use relative paths (/api/v1).` -- expected in test environment
- Mock error in `AdminPage.navigation.test.jsx`: `adminGetChatFeedback` not exported from `@/utils/api` mock -- does not cause test failure (test still passes)

---

## Observations

1. **All layers install and build cleanly.** The codebase compiles without type errors and the production frontend bundle is generated successfully.
2. **Backend tests are fully green (244/244).** The mock-based approach works well without needing live MongoDB/Redis/Vertex AI.
3. **7 test failures are pre-existing** (not introduced by this audit). They fall into two categories:
   - Edge: test expectations don't match current middleware/caching architecture (2 tests)
   - Frontend: AdminHealth credits panel tests expect GCP content but component renders AWS section first (5 tests)
4. **No security or critical build warnings** were observed.
5. **Python version requirement:** The backend `requirements.txt` is pinned for Python 3.11. Systems defaulting to Python 3.9 or 3.10 will fail during install.
