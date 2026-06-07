---
name: Syrabit chat latency fix
description: Root cause and fix for 9-10s chat latency (gemini-2.5-flash thinking phase).
---

# Chat Latency Root Cause & Fix

## The Problem
`gemini-2.5-flash` has a mandatory internal reasoning/thinking phase that runs
before emitting the first output token.  This caused TTFB of 7-8 s on every
English chat request (9-10 s total).

## The Fix (three-part)
1. **`apps/backend/app/config.py`** — default `VERTEX_GEMINI_MODEL` changed from
   `"gemini-2.5-flash"` to `"gemini-2.0-flash"`.
2. **Replit shared env var** — `VERTEX_GEMINI_MODEL` updated to `gemini-2.0-flash`
   (the old value was silently overriding the code default).
3. **`apps/backend/app/services/ai/vertex_client.py`** — `_thinking_config(model)`
   helper added at module level; injected into all four generation config dicts
   (`_generate_via_genai`, `_generate_via_vertex`, `_stream_via_genai`,
   `_stream_via_vertex`).  Sets `{"thinkingConfig": {"thinkingBudget": 0}}` for
   any model whose name contains "2.5", otherwise returns `{}`.

## Why thinkingBudget guard matters
If Cloud Run env var is ever reverted to `gemini-2.5-flash` (e.g. by a gcloud
deploy that drops env overrides — see syrabit-cloudrun-envvars.md), the
`thinkingBudget: 0` guard still fires and keeps TTFB under 2 s.

## Measured results
| Metric          | Before      | After      |
|-----------------|-------------|------------|
| English TTFB    | 7.48 s      | 0.60 s     |
| English total   | 9.68 s      | 4.37 s     |
| Assamese TTFB   | 3.64 s      | 0.51 s     |

**Why:** `thinkingBudget: 0` was already active on first test (working even while
env var still said 2.5-flash); model switch to 2.0-flash confirmed TTFB < 1 s.

## How to apply
- Any future model change: keep `_thinking_config()` in place; extend the gate
  condition if Gemini 3.x adds a similar thinking phase.
- Do NOT set `thinkingBudget` for non-Gemini models (Sarvam does not accept it).
