---
name: Sarvam model rename
description: sarvam-m1 and sarvam-30b are no longer valid; API now only accepts sarvam-105b
---

The Sarvam chat-completions API `/v1/chat/completions` rejects `sarvam-30b` (as of Aug 2026):
`Model 'sarvam-30b' has been deprecated. Please use one of the available models instead: sarvam-105b.`

**Why:** Sarvam deprecated sarvam-30b; only sarvam-105b remains. The old sarvam-m1/sarvam-30b names are gone.

**How to apply:**
- Use `sarvam-105b` exclusively — it is the only valid model as of Aug 2026
- `config.py` default is `sarvam-105b`; do not revert to sarvam-30b
- A 402 "No credits" error means the Sarvam account balance is exhausted — billing issue, not code
- A 400 with "deprecated" in the body means the model name is wrong

**Critical — reasoning model behaviour (sarvam-30b / sarvam-105b):**
- Both are reasoning models: they return `reasoning_content` (their thinking, in English)
  and `content` (the actual answer). With too few tokens, `content` is `null`.
- Starter tier cap: `max_tokens` ≤ 4096 per request.
- With ~1000-word English input: reasoning alone eats ~2000-3000 tokens, leaving nothing for output.
- Fix: chunk input into ~400-word segments. Each chunk fits: ~600 prompt + ~1500 reasoning + ~900 output ≈ 3000 tokens.
- Do NOT fall back to `reasoning_content` — it is English thinking, not the translation.
- HTTP read timeout must be ≥ 120 s (reasoning models take 30-90 s per request).
- The chunking logic lives in `content_generation.generate_assamese_only()`.
