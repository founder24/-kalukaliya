---
name: Syrabit chat latency fixes
description: All latency bottlenecks; actual TTFB measured June 2026; sarvam-30b reasoning-phase root cause confirmed
---

## Measured TTFB (June 2026, via /api/v1/chat/stream)

- English (Vertex/Gemini 2.5 Flash):  **~4.2s TTFB**, ~4.5s total
- Assamese (Sarvam 30b):              **~7.3s TTFB**, ~7.5s total

## Root cause of Assamese 7s TTFB

`sarvam-30b` is a reasoning model. Wire-level SSE confirms:
- `delta.reasoning_content` streams English thinking from ~150ms (server-side)
- `delta.content` (actual Assamese answer) arrives only AFTER reasoning completes — ~7s later
- Flags `enable_thinking: False` and `budget_tokens: 0` are **both ignored** — same TTFB regardless
- No API flag can reduce this; it is the model's architecture
- Only two Sarvam models exist (sarvam-30b, sarvam-105b); sarvam-30b is already the faster one
- Only viable improvement: UX typing indicator fired immediately on request start

## Model routing

- English → Vertex AI `gemini-2.5-flash` (OAuth2 SA path; GEMINI_API_KEY also works via genai path)
- Assamese streaming → `sarvam-30b` via `sarvam_client.stream_generate_with_retry()`
- Assamese non-streaming → `gemini-2.5-flash` override (buffered Sarvam > 15s timeout)

## Key optimisations in code

1. `vertex_client.py _thinking_config()`: gates on "2.5", sets thinkingBudget=0 → cuts Gemini thinking phase
2. `sarvam_client.py`: `"enable_thinking": False` in generate() + stream_generate() (kept even though ignored, in case Sarvam honours it in future)
3. `sarvam_client.py auth header`: `api-subscription-key` (not `Authorization: Bearer`)
4. `chat_service.py`: English fallback to Sarvam when Vertex fails (bug fix)
5. `chat.py _maybe_retrieve()`: MongoDB fast path → ~30ms vs 800-3000ms Vertex Search

## Vertex embedding quota

Heavy benchmarking hits quota (429). Quota resets within ~60s. Do not hammer
/chat/ endpoint in rapid succession during development testing.
