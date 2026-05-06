# Vertex AI Setup — content-formatter only

> **Scope (Task #490, V4 §15 amendment).** Vertex AI is **not** a chat
> hot-path provider, **not** a multilingual embedder, **not** a
> Vector-Search backend. The only sanctioned use is the
> NotebookLM-style content formatter
> [`vertex_format.format_with_vertex(text, *, style, lang)`](../vertex_format.py).
> Anything else (Vertex chat, Vertex embed, Vertex Vector Search) is
> deliberately removed from this codebase and CI blocks re-introduction.

## What this surface is

`vertex_format.format_with_vertex(text, *, style="notebook_lm",
lang="en", max_tokens=4000, timeout_s=15.0) -> str`

- **`style`** — one of `notebook_lm` (default), `study_notes`,
  `flashcard`. All three currently share a NotebookLM-style backend
  prompt; the public surface keeps them distinct so future formatter
  wiring (Tasks #494 / #519) can specialize each style without breaking
  callers.
- **`lang`** — `"en"` or `"as"`. The formatter is **forbidden from
  translating between languages**; it polishes in-place.
- **Failure mode** — raises `RuntimeError` on misconfiguration,
  `httpx.HTTPError` on transport, `RuntimeError` on empty/blocked
  response. Callers own the fall-through (V4 §12 — no silent fallbacks).

## Required environment

| Variable | Purpose | Default |
| --- | --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Service-account JSON blob (entire file content). | _required_ |
| `VERTEX_LOCATION` | GCP region. | `us-central1` |
| `VERTEX_GEMINI_MODEL` | Model name. | `gemini-2.5-flash` |
| `VERTEX_FORMAT_BREAKER_THRESHOLD` | Circuit-breaker open threshold. | `3` |
| `VERTEX_FORMAT_BREAKER_COOLDOWN_S` | Cooldown after breaker opens. | `180` |

The project ID is picked in this order:
`GCP_PROJECT_ID` → `GOOGLE_CLOUD_PROJECT` → `VERTEX_PROJECT_ID` →
`project_id` field of the SA blob.

## Wiring locations (current)

- `routes/ai_chat.py` — Assamese-mode polish step (
  `polish_notes_with_vertex` → `vertex_format.format_with_vertex`).
- `routes/admin_health.py` — exposes `vertex_format.auth_mode()` next to
  the legacy `vertex_services.health_check()` Workers-AI shim status.
- Sibling tasks `#494` / `#519` will route the SEO/notes/RAG render
  paths through this formatter; until they ship, raw-output paths
  remain unchanged.

## What was removed (Task #490)

- `vertex_chat.py`, `providers/vertex_embed.py`, `retrievers/vertex.py`
  (chat hot-path, multilingual embed, Vector Search retriever).
- `_call_vertex_chat`, `_stream_vertex_gemini`, the SA-OAuth chat
  helper archive stub (`llm.py`).
- All `("<pool>", "vertex")` rows from `config.POOL_WEIGHTS`.
- Vertex from every `PROVIDER_PRIORITY` list except `content_format`.
- The second Pinecone namespace `fallback_vertex_pending_reembed` (the
  Option-A failover) — replaced by **Option D** (cache-only degraded
  mode + AWS SQS deferred-embed; see
  [`infra/v4-locked-architecture.md`](../../../infra/v4-locked-architecture.md)
  §15).
- Obsolete env vars: `VERTEX_INDEX_ID`, `VERTEX_INDEX_ENDPOINT_ID`,
  `VERTEX_DEPLOYED_INDEX_ID`, `VERTEX_PUBLIC_DOMAIN_ENDPOINT`,
  `VERTEX_DIMENSIONS`, `VERTEX_SERVICE_ACCOUNT`.

## Acceptance gate

CI greps for the following symbols and fails the build if any are
re-introduced outside the V4 §15 changelog or the contract test files:

```
_call_vertex_chat | _stream_vertex_gemini | VertexVectorSearchRetriever
VERTEX_INDEX_ID | VERTEX_DEPLOYED_INDEX_ID | fallback_vertex_pending_reembed
```

Contract tests:

- `tests/test_vertex_format_contract.py` — pins the `format_with_vertex`
  signature and round-trips an httpx-mocked Vertex response.
- `tests/test_embed_failover_degraded_mode.py` — pins Option D
  (`EmbedDegradedMode` raised + AWS SQS `reembed` enqueue with
  deterministic `chunk_id`); also asserts `content_format` Vertex
  weight is `10000` and that Vertex appears in **no** other pool.
