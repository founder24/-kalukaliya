---
name: Syrabit chat latency fixes
description: All latency bottlenecks identified and fixed; streaming TTFB measured
---

## Current state (measured 2026-06-08)

### Streaming TTFB (user-facing — ChatPage.jsx uses /chat/stream)
- English: **~1.8s** (gemini-2.5-flash, thinkingBudget=0, no-RAG path)
- Assamese: **~1.7s** (sarvam-30b, enable_thinking=False, no-RAG path)

### Non-streaming total (fallback, not user-facing)
- English: ~6-7s (token generation floor ~100 tok/s × 600 tokens)
- Assamese: ~8s (Gemini with Assamese system prompt; Sarvam always 504s)

## Model routing
- English → Vertex AI `gemini-2.5-flash` (only working Gemini on this endpoint;
  gemini-2.0-flash returns HTTP 404)
- Assamese streaming → `sarvam-30b` with `enable_thinking: False`
- Assamese non-streaming → `gemini-2.5-flash` override (Sarvam takes >15s
  buffered, always hits 15s timeout; override added in chat.py `_process_chat()`)

## Key optimizations in code
1. `vertex_client.py _thinking_config()`: gates on "2.5", sets thinkingBudget=0
2. `sarvam_client.py`: `"enable_thinking": False` in generate() + stream_generate()
3. `chat.py _maybe_retrieve()`: MongoDB fast path via retrieve_context_from_chapter
   → ~30ms vs 800-3000ms Vertex Search; falls back to Vertex if chapter empty
4. Same MongoDB fast path applied to streaming endpoint RAG path
5. `vertex_search.py`: removed double-search (filter always None), timeout 10s→5s

## Why non-streaming Assamese → Gemini
Sarvam-30b buffered response: even with enable_thinking=False, full token
generation takes 15-30s (model generates ~50 tok/s for Assamese). 15s endpoint
timeout fires before any content is returned. Gemini generates Assamese correctly
when given an Assamese system prompt (build_system_prompt detects lang="as").

## Vertex embedding quota
Heavy benchmarking hits quota (429). Quota resets within ~60s. Do not hammer
/chat/ endpoint in rapid succession during development testing.
