---
name: Sarvam model rename
description: sarvam-m1 and sarvam-30b are no longer valid; API now only accepts sarvam-105b
---

The Sarvam chat-completions API `/v1/chat/completions` rejects `sarvam-30b` (as of Aug 2026):
`Model 'sarvam-30b' has been deprecated. Please use one of the available models instead: sarvam-105b.`

**Why:** Sarvam deprecated sarvam-30b; only sarvam-105b remains.

**How to apply:**
- Use `sarvam-105b` exclusively — it is the only valid model as of Aug 2026
- `config.py` default is already `sarvam-105b`
- The Replit shared env var `SARVAM_MODEL` was set to `sarvam-30b` and overrode the default — it is now fixed to `sarvam-105b`
- A 402 "No credits" error means the Sarvam account balance is exhausted — billing issue, not code
- A 400 with empty body + "deprecated" in the error JSON means the model name is wrong — the body was empty because the 400 body was not being read from the streaming response context

**Critical — reasoning model behaviour (sarvam-105b):**
- Returns `reasoning_content` (thinking, in English) and `content` (the actual answer)
- With `enable_thinking=True` (English mode): clean answer in `content` field
- With `enable_thinking=False` (Assamese mode): extract from `reasoning_content`
- Starter tier cap: `max_tokens` ≤ 4096 per request
- HTTP read timeout must be ≥ 120 s (reasoning models take 30-90 s per request)
