# Task #40 — Smart Router QA Badge: e2e Test Plan

End-to-end verification that the dev-only router QA badge
(`[data-testid="chat-router-qa-badge"]`, rendered by
`MessageBubble.jsx` when `import.meta.env.DEV && !msg.streaming &&
msg.route_trace`) surfaces the per-turn router decision Task #37
introduced.

Run via the testing skill (`runTest({testPlan, relevantTechnicalDocumentation})`).
The skill spawns a Playwright subagent against the live dev workflows
(`artifacts/syrabit: web` on `:25144`, `artifacts/syrabit: api` on `:8080`).
No login is required — anonymous chat is supported.

## Selectors used (all `data-testid`)

| testid | element |
|---|---|
| `chat-input` | chat textarea container |
| `chat-send-button` | send button |
| `lang-selector` | language selector trigger button |
| `chat-router-qa-badge` | dev-only QA badge (one per finished assistant msg) |
| `chat-message-bubble` | each message bubble |
| `assamese-unavailable-card` | Assamese chain 503 card |
| `ai-unavailable-card` | generic chain-503 card |

## Backend dispatcher contract (`chat_router.py`)

| pre-condition | decision | ns | embed |
|---|---|---|---|
| `intent=casual` (greeting) | `direct` | `∅` | `∅` |
| `lang=en` + study Q + `topic_score >= 0.55` | `rag` | `en` | `workers_ai_custom` |
| `lang=as` + study Q + `topic_score >= 0.55` | `rag` | `as` | `cohere_multilingual_v3_bedrock` |
| weak topic match OR probe unavailable | `web` | `∅` | `<lang-profile embed_provider>` |

> Note: the `web` branch in `chat_router.py` (L285-289) keeps
> `embed_provider` set to the lang-profile value because it still
> drives the deterministic cache key — only `pinecone_namespace`
> goes empty. So a web-routed badge reads
> `route=web ns=∅ embed=workers_ai_custom` for `lang=en` and
> `route=web ns=∅ embed=cohere_multilingual_v3_bedrock` for `lang=as`.

V4 §12 (no silent fallbacks): if the router commits to `web` but
`web_results == 0`, OR commits to `rag` but Pinecone returns 0
chunks, the stream still fails loud — the user sees the AI-unavailable
card and no ungrounded LLM answer is produced. Task #41 kept the
HTTP 503 status but **enriched the detail body** so the router
decision survives the failure. The 503 body is now:

```json
{
  "detail": {
    "message": "Web search returned no results …",
    "error_kind": "web_empty" | "rag_empty",
    "route_trace": { "decision": "web|rag", "lang": "...", … }
  }
}
```

`ChatPage.jsx` recognises `error_kind in {web_empty, rag_empty}` in
its `!response.ok` branch, attaches `route_trace` to the failed
message, and renders the AI-unavailable card. The dev-only
`chat-router-qa-badge` then renders alongside that card so the
canonical 3-turn plan below runs green in dev even without seeded
Pinecone vectors: badge text on a fail-loud turn shows the router
decision (`route=web` / `route=rag`) even though the bubble carries
the "unavailable" UI instead of a real answer.

## Plan A — canonical 3-turn test (prod + data-rich envs)

Single test, one browser context, three turns from one page load.
Best run against an environment where the sentinel subject id has
real Pinecone vectors AND the web-search tool returns results.

```text
1. Navigate to /chat?subject=physics_class_11_ahsec
   - wait for [data-testid="chat-input"]
   - lang-selector text == "English"

2. TURN 1 — casual greeting
   - type "hello", click send
   - wait up to 40s for ≥1 chat-router-qa-badge
   - LAST badge contains: route=direct  lang=en  ns=∅  embed=∅

3. TURN 2 — English study question (same page, no nav)
   - record badge count N1
   - type "What is photosynthesis and how does it work?", click send
   - wait up to 75s for badge count ≥ N1+1
   - LAST badge contains: lang=en  AND  embed=workers_ai_custom  AND
       EITHER (route=rag AND ns=en)
       OR     (route=web AND ns=∅)
     (web branch keeps the lang-profile embed_provider for the
      deterministic cache key; only ns goes empty.)
     ANY OTHER lang/ns/embed combo == FAIL

4. TURN 3 — switch to Assamese, study question
   - record badge count N2
   - click lang-selector → click second popup item (অসমীয়া)
   - confirm lang-selector now reads "অসমীয়া"
   - type "What is photosynthesis?", click send
   - wait up to 90s for badge count ≥ N2+1
     (assamese-unavailable-card == FAIL)
   - LAST badge contains: lang=as  AND
       embed=cohere_multilingual_v3_bedrock  AND
       EITHER (route=rag AND ns=as)
       OR     (route=web AND ns=∅)
     workers_ai_custom embed on lang=as == Task #27 regression == FAIL
```

## Plan B — per-turn variants (dev-friendly)

The canonical 3-turn plan exhausts the testing-subagent wall budget
when each RAG turn drains its 90s timer (notebook eviction). For
ad-hoc dev verification, run these three smaller plans separately.

### B-1: English casual (proves `route=direct` + `lang=en`)

```text
1. Navigate to /chat
2. Type "hello", click send
3. Wait up to 40s for ≥1 chat-router-qa-badge
4. Assert LAST badge contains: route=direct, lang=en, ns=∅, embed=∅
```

### B-2: Assamese casual (proves language-flip SSOT + `lang=as`)

```text
1. Navigate to /chat
2. Click lang-selector, click second popup item (অসমীয়া)
3. Confirm lang-selector reads "অসমীয়া"
4. Type "hello", click send
5. Wait up to 60s for ≥1 chat-router-qa-badge
6. Assert LAST badge contains: route=direct, lang=as, ns=∅, embed=∅
```

### B-3: English study question (proves `route=web` OR `route=rag`)

```text
1. Navigate to /chat?subject=<seeded-subject-id>
2. Type "What is photosynthesis and how does it work?", click send
3. Wait up to 90s for ≥1 chat-router-qa-badge
4. Assert LAST badge contains: lang=en AND embed=workers_ai_custom AND
     EITHER (route=rag AND ns=en)
     OR     (route=web AND ns=∅)
   (web branch keeps the lang-profile embed_provider for the
    deterministic cache key; only ns goes empty.)
```

> In dev without seeded Pinecone vectors / working web search, B-3
> still exercises the V4 §12 fail-loud path — but as of Task #41 the
> error is delivered as an HTTP 503 with a structured JSON `detail`
> body that carries `route_trace` (no SSE error chunk; the stream
> never opens for these two branches because the raise happens
> before `StreamingResponse` is returned). The QA badge renders on
> the failed message bubble alongside the AI-unavailable card and
> reads `route=web ns=∅ …` (or `route=rag ns=en …`) even when the
> answer body is empty. Backend log line
> `[STREAM][ROUTER=web|rag] ... failing loud` remains the source of
> truth for cross-checking.

## How to invoke

From the testing skill notebook:

```js
const result = await runTest({
  testPlan: `<paste one of the plans above>`,
  relevantTechnicalDocumentation: `<paste the contract / selector
    sections above so the subagent doesn't have to re-discover them>`,
});
console.log(result.status, result.testOutput);
```

## Last verified

| variant | status | last badge text |
|---|---|---|
| B-1 (English casual) | PASS | `QA route=direct lang=en ns=∅ embed=∅ head=vertex th=0.55 · intent=casual → casual short-circuit` |
| B-2 (Assamese casual) | PASS | `QA route=direct lang=as ns=∅ embed=∅ head=sarvam · intent=casual → casual short-circuit` |
| B-3 (English study Q, no seeded subject) | PASS-VIA-503-DETAIL (Task #41) | badge alongside AI-unavailable card, e.g. `QA route=web lang=en ns=∅ embed=workers_ai_custom head=vertex th=0.55 · web_results=0 → fail-loud (no silent ungrounded fallback)` |

## Related code

- `artifacts/syrabit-backend/routes/ai_chat.py` — four early-return
  fast-paths now emit `route_trace` via `_build_route_trace(...)`
  (Task #40 fix — guardrail-blocked, instant Assamese, instant
  casual, early-cache). The two streaming fail-loud branches
  (`web_empty`, `rag_empty`) raise HTTP 503 with a structured
  detail body that carries `route_trace` (Task #41).
- `artifacts/syrabit-backend/chat_router.py` — `route()` +
  `probe_topic_score()` (0.55 threshold).
- `artifacts/syrabit/src/pages/chat/MessageBubble.jsx` — badge
  renderer (`data-testid="chat-router-qa-badge"`).
- `artifacts/syrabit/src/pages/ChatPage.jsx` — SSE handler stores
  `route_trace` from `syrabit_done` events onto the message object.
