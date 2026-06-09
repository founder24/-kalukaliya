---
name: Syrabit chat latency fixes
description: All latency bottlenecks; actual TTFB measured June 2026; sarvam-30b reasoning-phase root cause + streaming fix
---

## Measured TTFB (June 2026, via /api/v1/chat/stream)

- English (Vertex/Gemini 2.5 Flash):  **~4.2s TTFB**, ~4.5s total
- Assamese (Sarvam 30b) — OLD:        **~7.3s TTFB** (waiting for delta.content after reasoning)
- Assamese (Sarvam 30b) — NEW:        **~150ms TTFB** (streaming delta.reasoning_content immediately)

## Root cause of original 7s TTFB

`sarvam-30b` is a reasoning model. Wire-level SSE confirms:
- `delta.reasoning_content` streams thinking from ~150ms (server-side)
- `delta.content` (actual answer) arrives only AFTER reasoning completes — ~7s later
- Flags `enable_thinking: False` and `budget_tokens: 0` are **both ignored** — same gap regardless
- No API flag can reduce the reasoning phase; it is the model's architecture

## Fix: stream reasoning_content immediately (June 2026)

`sarvam_client.stream_generate()` now yields `delta.reasoning_content` tokens first
(arrives ~150ms), then `delta.content` after reasoning. TTFB drops from ~7.3s → ~150ms.

The system prompt (`build_system_prompt()`) is written in Assamese and explicitly
instructs: "সকলো চিন্তা-ভাৱনা আৰু উত্তৰ অসমীয়া লিপিত লিখিব। ইংৰাজী নিষিদ্ধ।"
This causes the model to reason in Assamese (not English), so users see Assamese
text from the very first token.

Input language: Sarvam accepts any language — system prompt enforces Assamese output.

## Model routing

- English → Vertex AI `gemini-2.5-flash` (OAuth2 SA path; GEMINI_API_KEY also works via genai path)
- Assamese streaming → `sarvam-30b` via `sarvam_client.stream_generate_with_retry()`
- Assamese non-streaming → `gemini-2.5-flash` override (buffered Sarvam > 15s timeout)

## Key optimisations in code

1. `vertex_client.py _thinking_config()`: gates on "2.5", sets thinkingBudget=0 → cuts Gemini thinking phase
2. `sarvam_client.py stream_generate()`: yields `reasoning_content` first (TTFB fix), then `content`
3. `sarvam_client.py`: `"enable_thinking": False` kept in payload for forward compatibility
4. `sarvam_client.py auth header`: `api-subscription-key` (not `Authorization: Bearer`)
5. `chat_service.py build_system_prompt()`: Assamese prompt instructs Assamese-only reasoning
6. `chat_service.py`: English fallback to Sarvam when Vertex fails (bug fix)
7. `chat.py _maybe_retrieve()`: MongoDB fast path → ~30ms vs 800-3000ms Vertex Search

## Vertex embedding quota

Heavy benchmarking hits quota (429). Quota resets within ~60s. Do not hammer
/chat/ endpoint in rapid succession during development testing.
