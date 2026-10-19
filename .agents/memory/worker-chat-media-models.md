---
name: Worker chat media models
description: Current Cloudflare Workers AI contracts for chat image OCR and text-to-speech.
---

Keep chat OCR and TTS inside the API Worker through its Workers AI binding. Do not copy model fields from the legacy backend client without checking the current Workers AI catalog.

The current MeloTTS model ID is `@cf/myshell-ai/melotts` (not `@cf/myshell/melotts`); its input uses `prompt` (not `text`), and its output schema allows raw MP3 or JSON containing base64 audio. The older configured Uform vision model was absent from the current model catalog checked on 2026-10-19; use a currently listed image-to-text model and follow its own input/output schema.

**Why:** Cloudflare returned `5007: No such model ... or task` for the shorter, plausible-looking MeloTTS namespace. A production smoke test also showed why mock-only checks are insufficient for model identifiers.

**How to apply:** Before changing chat media models, verify the complete model ID and schemas, then keep response normalization, Worker routes, and contract tests aligned with those schemas. Include a live smoke test before declaring a model integration operational.
