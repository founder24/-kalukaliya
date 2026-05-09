# Backend dead-code & provider purge — Task #6 (2026-05-09)

Driven by the Task #5 architecture lock (`infra/architecture-matrix.json`,
`infra/architecture-locked-2026.md`). Every deletion below is justified by
a RETIRED row in the matrix or a tombstone left over from an earlier
cutover. CI guard (`scripts/check_architecture_lock.py`) was re-run and
remained green after every batch.

## Deleted files / directories

| Path | Why | Driver |
| --- | --- | --- |
| `artifacts/syrabit-backend/emergentintegrations/` | Shim package wrapping Groq + OpenAI + Fireworks-AI. All three providers retired in Task #347 / V4 §0; the shim's only consumers in `llm.py` were two unreachable fall-through branches. | Matrix retired_providers: `groq`, `fireworks` (and `gpt-oss-20b`/openai SDK transport-only). |

## Stripped from `artifacts/syrabit-backend/llm.py`

- `from emergentintegrations.llm.chat import LlmChat, UserMessage` — import removed; replaced with a Task #6 explanatory comment.
- `_call_llm_raw` fall-through (was: `chat = LlmChat(...).send_message(...)`) — replaced with `raise HTTPException(500, ...)` per V4 §12 no-silent-fallbacks. The branch was unreachable from the active `PROVIDER_PRIORITY` for `english_rag_chat` (`vertex → vertex_flash_lite → workers_ai_llama32_3b`) and `assamese_rag_chat` (`sarvam → vertex_assamese → retrieval_only`); raising loudly preserves the invariant if a future caller mis-routes.
- `_stream_from_provider` else-branch (was: `LlmChat(...).stream_messages(...)`) — same treatment; an unknown streaming provider now logs `ERROR` and raises `HTTPException(500)`.

## Stripped from `artifacts/syrabit-backend/server.py`

- `_RAILWAY_AUDIT_BLOCK_REMOVED_PLACEHOLDER()` — 200-line tombstone retained from the Task #336 Railway → ACA cutover. The function body had been a no-op `return None` since #336; the trailing dead code (Categories 1–7 of the old Railway audit) was unreachable. Replaced with a 5-line Task #6 comment pointing at `infra/v4-locked-architecture.md` §6 and `docs/architecture/decisions.md` for the post-Railway secrets topology (Azure KV → AWS SM → CF Secrets).

## Stripped from `artifacts/syrabit-backend/tests/test_llm_cf_cache_headers.py`

- `_emergent_chat_module`, `test_emergent_real_key_does_not_clear_authorization`, `test_emergent_byok_placeholder_clears_authorization` — exercised `LlmChat._cf_cache_headers` from the deleted `emergentintegrations/` package. The five `test_cf_cache_headers_*` cases above continue to guard the same `_cf_cache_headers` contract directly on `llm.py`, plus `test_call_openai_compat_forwards_api_key_byok` covers the BYOK forwarding integration.

## Matrix updates

- `retired_provider_allowlist_paths` reduced from `["artifacts/syrabit-backend/llm.py"]` to `[]` — the allowlist entry was a temporary suppression for the `from emergentintegrations.llm.chat import LlmChat, UserMessage` line that has now been physically deleted. The strict retired-provider regression scan in `scripts/check_architecture_lock.py` now covers `llm.py` with no exceptions.

  Scope of the guard: the regression scan matches **import sites and `os.environ.get(...)` reads** for retired-provider names; it does **not** match arbitrary string literals or in-function provider-name dispatch branches. Concretely, a future `from emergentintegrations...` re-import or `os.environ.get('FCM_SERVER_KEY')` in `llm.py` would now be caught with no allowlist exception. Live provider-branch reintroductions (e.g. an `if provider == "openrouter":` block) are not auto-detected by the matrix guard — they are caught only at code review and via the regression tests in `tests/test_unsupported_provider_raises.py`.

## Round 2 — code-review follow-ups (architect rejection)

### `requirements.txt`

- Dropped `azure-monitor-opentelemetry-exporter==1.0.0b30` and
  `opentelemetry-exporter-otlp-proto-http==1.27.0` (the App Insights
  + Axiom OTLP/HTTP dual-export from Task #333). Task #558 made
  GCP Cloud Trace the sole OTEL destination; `tracing.py` contains
  no Azure Monitor refs (verified via `rg -n azure_monitor`), so the
  packages were dead weight on the ACA image.
- Retained `openai>=1.51.0,<3.0.0` — Task #347's comment in
  `requirements.txt` documents that the SDK class is kept as a
  generic OpenAI-compatible HTTP client (Workers AI / CF AI Gateway
  base-URL plumbing + typed exception classes); the OpenAI provider
  itself has zero `api.openai.com` callsites.

### Matrix schema extension (`FILE_DELETED`)

- `scripts/check_architecture_lock.py` now accepts `FILE_DELETED`
  alongside the existing `IMPLEMENTED | PARTIAL | MISSING | RETIRED`
  enum. `FILE_DELETED` is the stronger form of `RETIRED`: the
  source files have been physically removed (not just unrouted from
  `PROVIDER_PRIORITY` or env knobs). The `_check_source_paths` skip
  logic was extended to treat `FILE_DELETED` like `RETIRED` (empty
  `source_paths` by design).
- `infra/architecture-matrix.json` row 4.2 *Azure Monitor + App
  Insights* flipped from `RETIRED` → `FILE_DELETED` (the only row
  where the underlying pip dependency was actually purged in this
  task). Row 5.2 *Vertex Vision* kept as `RETIRED` —
  `providers/google_vision.py` is still on disk; future cleanup task.

### Retired-name leak audit

The umbrella guard (`scripts/ci/check_canonical_delegation.py`) is
the source of truth for "what counts as a retired-name violation."
It uses bare-token regex + comment-skip / removal-note heuristics so
that Task #347-style retirement *comments* do not regress to
violations. After this task it scans 1268 files green. Stray
mentions of retired provider names in `artifacts/syrabit-backend/`
(`grep -i cohere|cerebras|...`) all live in retirement comments,
the matrix's own `retired_providers` array, the umbrella guard's
banned-token regex, or test files exercising the dead-provider
guard itself — all intentionally preserved.

## Additional `llm.py` cleanup (architect follow-up)

- `if provider == "openrouter": ...` dispatch branch (was `_call_single_provider:1494-1495`) — removed. OpenRouter is in `infra/architecture-matrix.json` `retired_providers`; no `PROVIDER_PRIORITY` entry routes to it after #347. Unsupported-provider raise covers it.
- `elif p_name == "openrouter": ...` stream branch (was `_stream_from_provider:3482-3484`) — removed; same rationale.
- `_SLM_PROVIDER_MAX_INPUT_CHARS["openrouter"] = 200000` — removed; no consumer.
- `tests/test_unsupported_provider_raises.py` — new file pinning the V4 §12 contract: unknown providers in both the dispatch and streaming paths raise `HTTPException(500)` with the provider name in the detail.

## Considered and **kept** (false positives)

- `artifacts/syrabit-backend/providers/cloudflare_ai.py` — initially flagged as a removal candidate by Task #6's brief; in fact this is the live Workers AI / CF AI Gateway client used by `llm.py`, `content_formatter.py`, and the chat-stream paths (Workers AI Llama-3.2-3B is the locked English-chat tail and the OCR provider per matrix rows 5.1, 5.2). NOT retired.
- `artifacts/syrabit-backend/llm.py` — kept; PROVIDER_PRIORITY router, 429-burst tracker, paid-RPM shed, Sarvam pool, Workers-AI streaming wrappers all live here. The classes/comments mentioning `groq` / `cerebras` / `bedrock` / `xai` are historical context inside docstrings already explicitly marked "removed in #347" — they do not represent live import/env-read sites.
- `OPENAI_API_KEY` / `_OPENAI_KEY` / `openai>=1.51.0,<3.0.0` — the `openai` SDK is retained as a generic OpenAI-compatible HTTP transport for Workers AI / CF AI Gateway endpoints (no traffic to `api.openai.com`). The retained import is annotated with a `noqa` marker at `llm.py:3` that the umbrella canonical-delegation guard already honors. Removing the SDK would force a same-day rewrite of `_call_openai_compat` and is out of Task #6 scope.
- `EMAIL_FALLBACK_KEY` in `credit_burn_meter.py` — Redis key constant (`"email:fallback"`), not the retired `EMAIL_FALLBACK` env knob. The umbrella guard's pattern targets the env-var name and is satisfied.
- Legacy FCM / Firebase env knobs (`FCM_SERVER_KEY`, `FIREBASE_SERVICE_ACCOUNT`, `firebase_admin`) — already absent from the active scan roots; the only references live in `scripts/migrate_fcm_to_vapid.py` (tombstone migration script) and `scripts/ci/check_canonical_delegation.py` (the guard's own ban-list literals, exempt by design).
- `requirements.txt` — `emergentintegrations` was a vendored source-tree shim, not a pip package, so no requirements-line edit was needed. Other retired-provider pip lines (`firebase-admin`, `sendgrid`, `resend`, `cohere`, `voyage`, `cerebras-cloud-sdk`, `assemblyai`, `azure-cognitiveservices-speech`) were removed by their parent retirement tasks (#347, #552, #556, #557) and are confirmed absent.

## Verification

- `python -c "import server"` from `artifacts/syrabit-backend/` — passes.
- `python3 scripts/check_architecture_lock.py` — passes (29 sections, 105 rows, retired-provider regression scan green with empty `retired_provider_allowlist_paths`).
- Matrix RETIRED rows continue to point at `source_paths: []` (the rows were already `RETIRED` after the parent tasks deleted the providers; Task #6 only physically removed the leftover `emergentintegrations/` shim). No matrix row needed a `RETIRED → FILE_DELETED` transition because `FILE_DELETED` is not part of the schema enum and the existing `RETIRED` rows already accurately describe the post-purge state.
