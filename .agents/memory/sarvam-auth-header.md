---
name: Sarvam auth header + English fallback
description: Sarvam API requires api-subscription-key header (not Authorization Bearer); English chat must fall back to Sarvam when Vertex/Gemini fails.
---

## Rule 1: Sarvam auth header
Sarvam AI's `/v1/chat/completions` endpoint requires:
```
"api-subscription-key": <key>
```
NOT `Authorization: Bearer <key>`. Using Bearer causes silent 401s — every Sarvam call fails but the error is generic.

**Why:** Sarvam uses a non-standard subscription-key auth scheme, not OAuth Bearer tokens. Using Bearer looked valid at the HTTP level but was rejected at the Sarvam API gateway.

**How to apply:** Both the `generate()` and `stream_generate()` methods in `sarvam_client.py` must use `api-subscription-key`. Grep for `Authorization.*Bearer` in that file after any refactor.

## Rule 2: English LLM fallback (Vertex → Sarvam)
`chat_service.py` `call_llm()` and `stream_llm()` must have fallback for **both** language branches:
- `detected_lang == "as"`: Sarvam fails → fall back to Vertex AI (existing)
- `detected_lang == "en"`: Vertex/Gemini fails (e.g. 429 credits depleted) → fall back to Sarvam AI

**Why:** Gemini prepayment credits can be depleted. Without an English fallback, every English chat returned 503 with no recovery path. Sarvam handles English well enough as a fallback model.

**How to apply:** In `call_llm`, the `else: raise` branch should instead call `sarvam_client.generate()`. In `stream_llm`, the `else:` branch should stream via `sarvam_client.stream_generate_with_retry()` and store a dead letter on double failure.
