# Backend Dependency Audit — 2026-05-10

> **Scope:** `artifacts/syrabit-backend/requirements.txt` (runtime, 56 direct deps),
> `artifacts/syrabit-backend/requirements-test.txt` (4 direct deps),
> `artifacts/syrabit/services/cron-jobs/requirements.txt` (6 direct deps).
> Read-only audit per Task #55. **No `requirements*.txt` or `pyproject.toml` was modified.**
>
> **Tooling:** Python 3.11.14, `pip-audit` (OSV.dev backend), `pip list --outdated`.
> 170 distributions resolved in the installed environment.

---

## 1. Headline risks (read these first)

| # | Package | Current | Latest / Patch | Risk | Recommendation |
|---|---|---|---|---|---|
| 1 | **`mistune`** | `3.2.0` | `3.2.1` | **4 vulns** in 3.2.0 (CVE-2026-33079 ReDoS in `LINK_TITLE_RE`; CVE-2026-44708 + CVE-2026-44896 XSS in math/figure plugins; CVE-2026-44897 XSS in heading-id attribute). All severity not yet pinned by GH advisory DB but rated as exploitable. | **patch to 3.2.1** in next batch. **Math/figure XSS does NOT apply** — verified that both `seo_engine.py` and `routes/admin_monetization.py` instantiate mistune with `plugins=["table","strikethrough","footnotes","task_lists"]` + `escape=True`, *not* `math` or `figure`. ReDoS + heading-id XSS DO apply (heading-id is unconditional in 3.2.0); 3.2.1 closes both. |
| 2 | **`protobuf`** | `4.25.9` | `7.34.1` | **3 majors behind**. Pinned indirectly by `google-cloud-aiplatform` + `google-cloud-bigquery`; bumping in isolation will break those SDKs. | **NO-TOUCH** — leave at 4.x until the GCP SDKs catch up to 5+. They currently advertise `protobuf>=4.21.6,<5.0` peer constraints. Any forced bump means re-pinning the entire google-cloud-* tree. Track as ecosystem-blocked tech debt. |
| 3 | **`psutil`** | `5.9.8` | `7.2.2` | **2 majors behind**. Used by `gunicorn.conf.py` worker monitoring + admin ops endpoints. v6 dropped Python 2; v7 changed `Process.memory_info()` field names on Linux. | **major-bump-with-follow-up-task** — needs a grep for `Process.memory_info()` callers + a `gunicorn.conf.py` smoke. |
| 4 | **`uvicorn`** | `0.25.0` | `0.46.0` | **21 minor versions behind** (same major). H11 + WebSockets handling rewrites in 0.30+; HTTP/2 in 0.40+. Not flagged as vulnerable but the lag itself is operational risk (we miss h11 CVE patches). | **batch-bump to 0.46.0**, smoke `gunicorn server:app -c gunicorn.conf.py`. Same-major so no breaking-change task needed; but treat as a focused PR (not folded into the 22-pkg patch batch) so a regression bisect is easy. |
| 5 | **`sentry-sdk`** | `2.18.0` | `2.59.0` | **41 minor versions behind**, same major. Sentry has shipped multiple integration fixes + privacy improvements in this range. | **batch-bump to 2.59.0**, run synthetic error injection on `/api/health/sentry-test` after deploy. |
| 6 | **`opentelemetry-*` family** | `1.27.0` | `1.41.1` | **14 minor versions behind**, same major. The whole OTel exporter set must move in lock-step (api, sdk, exporter-otlp-proto-*, exporter-gcp-trace, proto). Spec-defined breaking changes in 1.30+ for resource attributes. | **batch-bump as one unit to 1.41.1**, dedicated PR with a Cloud Trace smoke. **Note:** `cron-jobs/requirements.txt` re-pins `opentelemetry-api/sdk/exporter-otlp-proto-http>=1.27.0` — must bump there in lock-step. |
| 7 | **`cryptography`** | `47.0.0` | `48.0.0` | **1 major behind**. Single-major bump but cryptography historically introduces silent OpenSSL behavior changes per major. | **single-package PR** with `pytest tests/test_jwt*.py tests/test_supabase_jwks*.py` after bump. |
| 8 | **`bcrypt`** | `4.0.1` | `5.0.0` | **1 major behind**. v5 dropped Python 3.7 (we're on 3.11, fine) but renamed two internal helpers used by `passlib`. | **single-package PR**, smoke `routes/auth.py` password verify path. |
| 9 | **`gunicorn`** | `25.3.0` | `26.0.0` | **1 major behind**. v26 changed default keepalive + sync-worker handling. | **single-package PR** with the existing `gunicorn.conf.py` smoke. |

> **Net advisory ladder:** 4 vulns in 1 direct package (`mistune`); 3 of 4 are
> patched by 3.2.1, 1 (math plugin XSS) is **not applicable** to this
> codebase per renderer-config audit. Effective post-patch CVE surface: **0**.

---

## 2. Vulnerability scan (`pip-audit`)

> **Note:** `pip-audit -r requirements.txt --no-deps` failed because
> `requirements.txt` contains 13 unpinned entries (e.g. `upstash-redis>=1.3.0`,
> `boto3>=1.35.0`, `openai>=1.51.0,<3.0.0` — see §5b). The environment-mode
> scan (`pip-audit` against the resolved venv, 170 packages) was used
> instead. This catches the actually-installed versions, which is what
> production ACA + Lambda images run.

| Severity | Package | Version | Vuln ID | Aliases | Fix Available | Direct/Transitive | Applies? |
|---|---|---|---|---|---|---|---|
| n/a | `mistune` | `3.2.0` | CVE-2026-33079 | GHSA-8mp2-v27r-99xp | ✅ `3.2.1` | DIRECT | **YES** — ReDoS in LINK_TITLE_RE, generic |
| n/a | `mistune` | `3.2.0` | CVE-2026-44708 | GHSA-8g87-j6q8-g93x | ❌ no fix | DIRECT | **NO** — math plugin not enabled in either renderer config |
| n/a | `mistune` | `3.2.0` | CVE-2026-44896 | GHSA-58cw-g322-p94v | ❌ no fix | DIRECT | **NO** — figure plugin not enabled in either renderer config |
| n/a | `mistune` | `3.2.0` | CVE-2026-44897 | GHSA-v87v-83h2-53w7 | ✅ `3.2.1` | DIRECT | **YES** — heading-id XSS, generic, applies to all renderers |

**Conclusion:** single fix — `mistune==3.2.1` — closes the entire applicable
attack surface for the backend.

---

## 3. Outdated scan (`pip list --outdated`)

38 distributions outdated in the installed env. Grouped by major-jump
(`!!` ≥2, `!` =1, blank = patch/minor).

### 3a. Two-or-more majors behind (`!!`)

| Package | Current | Latest | Workspace-direct? | Verdict |
|---|---|---|---|---|
| `protobuf` | `4.25.9` | `7.34.1` | transitive (under google-cloud-*) | **NO-TOUCH** — gated by GCP SDK pin range (see §5a) |
| `psutil` | `5.9.8` | `7.2.2` | direct | **major-bump-with-follow-up-task** (see headline #3) |

### 3b. One major behind (`!`)

| Package | Current | Latest | Direct? | Verdict |
|---|---|---|---|---|
| `bcrypt` | `4.0.1` | `5.0.0` | direct (via passlib peer) | single-package PR + auth smoke |
| `cachetools` | `6.2.6` | `7.1.1` | direct (used by `cache.py`) | single-package PR; v7 dropped Python 3.8, no API change for our usage |
| `cryptography` | `47.0.0` | `48.0.0` | direct | single-package PR + JWT smoke |
| `gunicorn` | `25.3.0` | `26.0.0` | direct (pinned `==25.3.0`) | single-package PR + worker smoke |
| `importlib_metadata` | `8.4.0` | `9.0.0` | transitive | bumps automatically when parent moves |
| `rich` | `14.3.4` | `15.0.0` | transitive (CLI/log pretty-printing) | bumps automatically |
| `websockets` | `15.0.1` | `16.0` | transitive (uvicorn extras) | gated on `uvicorn 0.46` bump |
| `wrapt` | `1.17.3` | `2.1.2` | transitive (sentry/otel) | bumps automatically |

### 3c. Patch / minor only — safe single-batch PR

22 entries; all same-major bumps with no breaking changes advertised:

| Package | Current → Latest |
|---|---|
| `dnspython` | `2.7.0 → 2.8.0` |
| `filelock` | `3.25.2 → 3.29.0` |
| `fixedint` | `0.1.6 → 0.2.0` |
| `google-api-core` | `2.30.0 → 2.30.3` |
| `google-auth` | `2.49.1 → 2.52.0` |
| `google-auth-oauthlib` | `1.3.0 → 1.4.0` |
| `google-cloud-bigquery` | `3.27.0 → 3.41.0` |
| `grpcio-status` | `1.62.3 → 1.80.0` |
| `mistune` | `3.2.0 → 3.2.1` ← **clears §2 vulns** |
| `opentelemetry-api/sdk/exporter-*/proto` | `1.27.0 → 1.41.1` (lock-step, treat as one) |
| `postgrest` | `2.28.0 → 2.30.0` (supabase peer set) |
| `pydantic` | `2.12.5 → 2.13.4` |
| `pydantic_core` | `2.41.5 → 2.46.4` |
| `PyJWT` | `2.12.0 → 2.12.1` |
| `pypdf` | `6.10.2 → 6.11.0` |
| `python-multipart` | `0.0.27 → 0.0.28` |
| `realtime` | `2.28.0 → 2.30.0` (supabase peer set) |
| `sentry-sdk` | `2.18.0 → 2.59.0` (consider its own PR — see headline #5) |
| `storage3` | `2.28.0 → 2.30.0` (supabase peer set) |
| `supabase` | `2.28.0 → 2.30.0` |
| `supabase-auth` | `2.28.0 → 2.30.0` (supabase peer set) |
| `supabase-functions` | `2.28.0 → 2.30.0` (supabase peer set) |
| `uvicorn` | `0.25.0 → 0.46.0` (consider its own PR — see headline #4) |

> **Lock-step groups inside the batch:**
> - **supabase family** (6 packages must move together: `supabase + supabase-auth + supabase-functions + postgrest + storage3 + realtime`).
> - **opentelemetry family** (6 packages: `api + sdk + proto + exporter-otlp-proto-common + exporter-otlp-proto-http + exporter-gcp-trace`).
> - **pydantic family** (`pydantic + pydantic_core` — pydantic-core is the rust kernel that pydantic pins exactly).

---

## 4. Vulnerable AND > 1 major behind (cross-reference)

**None.** The only vulnerable direct package is `mistune` and it's only a
patch behind (3.2.0 → 3.2.1).

---

## 5. No-touch list

### 5a. Pinned by transitive constraint — bumping in isolation breaks the parent

| Package | Pin | Pinned-by | Verdict |
|---|---|---|---|
| `protobuf` | `4.25.9` | `google-cloud-aiplatform`, `google-cloud-bigquery`, `grpcio-status` (all cap at `<5.0`) | **NO-TOUCH** until the GCP SDK family lifts the cap. |
| `httpx` | `0.28.1` (pinned `==`) | `supabase` peer set requires `>=0.27,<0.30` | safe at 0.28.1 |
| `pydantic-core` | `2.41.5` | exact-pin from `pydantic==2.12.5` | bumps with pydantic only |
| `grpcio-status` | `1.62.3` | `grpcio==1.62.x` exact pin from `google-cloud-aiplatform` | bumps with the GCP set |
| `websockets` | `15.0.1` | uvicorn `[standard]` extras peer | bumps with uvicorn |

### 5b. Unpinned direct deps in `requirements.txt` (use `>=` or `>=,<`)

These break `pip-audit -r requirements.txt --no-deps`. Not a security issue
on their own, but they make reproducibility weak — the resolved version on
ACA cold-start can drift from CI without warning.

| Package | Pin | Notes |
|---|---|---|
| `upstash-redis` | `>=1.3.0` | unpinned floor only |
| `redis` | `>=5.0.1` | unpinned floor |
| `fakeredis` | `>=2.20` | test-only but lives in runtime reqs |
| `blake3` | `>=1.0` | unpinned floor |
| `jinja2` | `>=3.1` | unpinned floor |
| `lupa` | `>=2.8` | test-only but lives in runtime reqs |
| `boto3` | `>=1.35.0` (line 32) **and** `>=1.34.0` (line 58) | **DUPLICATE pin** with conflicting floors — pip resolves to the higher; flag for cleanup |
| `openai` | `>=1.51.0,<3.0.0` | range pin (acceptable) |
| `azure-identity` | `>=1.17.0` | unpinned floor |
| `pypdf` | `>=6.10.2` | unpinned floor |
| `cloudflare` | `>=4.3.0` | unpinned floor |
| `pydantic-settings` | `>=2.0.0` | unpinned floor |

**Recommendation:** in a future hardening task, run `pip freeze` against the
ACA image and pin every direct dep to `==<resolved>`. This matches the
discipline of `pnpm-lock.yaml` on the frontend side.

### 5c. `cron-jobs/requirements.txt` overlaps

`services/cron-jobs/requirements.txt` re-pins
`opentelemetry-api/sdk/exporter-otlp-proto-http>=1.27.0`,
`azure-identity>=1.17.0`, and `azure-monitor-opentelemetry-exporter>=1.0.0b30`
on top of the legacy backend wheel installed in the same image. Any OTel
batch-bump (§3c lock-step) must bump **both** files in the same PR or the
cron image will silently downgrade OTel back to 1.27 at build time.

---

## 6. Per-finding recommendation summary

| Finding | Verdict |
|---|---|
| `mistune 3.2.0` — 4 vulns | **patch** to 3.2.1 (closes 2 applicable vulns; other 2 N/A per renderer config) |
| `protobuf 4→7` | **NO-TOUCH** (GCP SDK ecosystem lock) |
| `psutil 5→7` | **major-bump-with-follow-up-task** |
| `uvicorn 0.25→0.46` | **focused single-package PR** + worker smoke |
| `sentry-sdk 2.18→2.59` | **focused single-package PR** + sentry-test smoke |
| `opentelemetry-* family 1.27→1.41` | **lock-step group PR** spanning runtime + cron-jobs reqs |
| `supabase family 2.28→2.30` | **lock-step group PR** (6 packages) |
| `pydantic 2.12→2.13` | safe, in patch batch |
| `cryptography 47→48` | single-package PR + JWT smoke |
| `bcrypt 4→5` | single-package PR + auth smoke |
| `gunicorn 25→26` | single-package PR + worker smoke |
| `cachetools 6→7` | single-package PR (no API change for our usage) |
| Other patches (PyJWT, pypdf, python-multipart, dnspython, filelock, etc.) | **single batch PR** — `chore(deps): patch+minor backend batch` |
| 13 unpinned `>=` direct deps | **hardening task** — run `pip freeze` and pin all 13 to `==` |
| `boto3` duplicate pin (`>=1.35.0` line 32 + `>=1.34.0` line 58) | **cleanup** — collapse to one entry |

---

## 7. Methodology notes

- `pip-audit -r requirements.txt` requires every line to be exact-pinned
  (`==`) or the OSV resolver bails. Use `--no-deps` to bypass *resolution*
  but the file still needs no `>=` lines. The cleaner workaround for this
  codebase is to run `pip-audit` in environment mode against the
  installed venv (what we did).
- Lambda batch jobs do not ship per-job `requirements.txt` files — the
  shared `sqs_consumers` Lambda image bakes the full backend
  `requirements.txt` (per `infra/aws/lambda/manifest.json`). So the
  backend audit covers the Lambda surface transitively.
- `pip list --outdated` does not see test-only deps that aren't installed.
  `requirements-test.txt` is small (4 packages, all version floors); none
  surface as outdated in the installed env.
- The `cron-jobs/requirements.txt` re-pin pattern is intentional per the
  comment header but it does mean any OTel bump must be applied twice.

---

*Audit run: 2026-05-10, pip-audit (OSV.dev), Python 3.11.14, against origin/main HEAD `5982aab`.*
