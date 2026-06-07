---
name: Sarvam model rename
description: sarvam-m1 is no longer a valid model name; API now requires sarvam-30b or sarvam-105b
---

The Sarvam chat-completions API `/v1/chat/completions` rejects `sarvam-m1` with a 400:
`Input 'sarvam-m1' should be one of sarvam-30b, sarvam-105b`

**Why:** Sarvam renamed/rebranded their models in mid-2025. The old `sarvam-m` / `sarvam-m1` names are gone.

**How to apply:**
- Use `sarvam-30b` (faster, good for translation) or `sarvam-105b` (higher quality, slower)
- `SARVAM_MODEL` env var in Replit shared environment is now set to `sarvam-30b`
- `config.py` default is also updated to `sarvam-30b`
- A separate 402 "No credits" error means the Sarvam account balance is exhausted — this is a billing issue, not a code issue
