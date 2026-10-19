---
name: Worker chat media models
description: Current Cloudflare Workers AI contracts for chat image OCR and text-to-speech.
---

Keep chat OCR and TTS inside the API Worker through its Workers AI binding. Do not copy model fields from the legacy backend client without checking the current Workers AI catalog.

The supported MeloTTS contract uses `prompt` (not `text`) and returns MP3 binary. The older configured Uform vision model was absent from the current model catalog checked on 2026-10-19; use a currently listed image-to-text model and follow its own input/output schema.

**Why:** The legacy backend's request fields and model selection were stale relative to Cloudflare's current Workers AI contracts, which would make the new Worker routes fail despite using the right service.

**How to apply:** Before changing chat media models, verify the current model catalog and schemas, then keep the Worker route and its contract tests aligned with those schemas.
