---
name: Sarvam model rename
description: sarvam-m1 is no longer valid; only sarvam-30b and sarvam-105b exist; both ignore thinking-disable flags
---

The Sarvam chat-completions API `/v1/chat/completions` rejects `sarvam-m1` with a 400:
`Input 'sarvam-m1' should be one of sarvam-30b, sarvam-105b`

**Why:** Sarvam renamed/rebranded their models in mid-2025. The old `sarvam-m` / `sarvam-m1` names are gone.

**How to apply:**
- Use `sarvam-30b` (faster) or `sarvam-105b` (higher quality) — these are the ONLY two models
- `SARVAM_MODEL` env var in Replit shared environment is now set to `sarvam-30b`
- `config.py` default is also updated to `sarvam-30b`
- A 402 "No credits" error means the Sarvam account balance is exhausted — billing issue, not code

**Critical — reasoning model behaviour (sarvam-30b / sarvam-105b):**
- Both are reasoning models that ALWAYS reason first via `delta.reasoning_content` (English thinking).
  Actual answer arrives later in `delta.content`.
- `enable_thinking: False` and `budget_tokens: 0` are **both ignored** — confirmed via live wire test.
  Neither flag shortens the reasoning phase. Do NOT waste time trying other flag variants.
- Real Assamese TTFB from sarvam-30b: **~7 seconds** (inherent model architecture, unfixable via flags).
- With too few tokens (`max_tokens: 256`), reasoning consumes ALL the budget — `content` is null.
  Production uses `max_tokens: 2048` which gives enough room for both reasoning + Assamese answer.
- Starter tier cap: `max_tokens` ≤ 4096 per request.
- With ~1000-word English input: reasoning alone eats ~2000-3000 tokens, leaving nothing for output.
- Fix: chunk input into ~400-word segments. Each chunk fits: ~600 prompt + ~1500 reasoning + ~900 output ≈ 3000 tokens.
- Do NOT fall back to `reasoning_content` — it is English thinking, not the translation.
- HTTP read timeout must be ≥ 120 s (reasoning models take 30-90 s per request).
- The chunking logic lives in `content_generation.generate_assamese_only()`.

**Reducing Assamese TTFB:**
- No API flag works. Only option is UX: show a "thinking..." indicator immediately while the model reasons.
- There is no smaller/faster Sarvam model — only these two exist as of June 2026.
