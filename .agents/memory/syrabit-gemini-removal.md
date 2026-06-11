---
name: Syrabit Gemini removal
description: Gemini API and Vertex AI Search were removed; all AI generation now routes to Sarvam; vertex_client.py kept only for TTS and Vision
---

## Rule
Gemini (Vertex AI text generation) and Vertex AI Search have been removed from the project. Do NOT add them back or reference them for chat or content generation.

**Why:** The project no longer has Gemini API keys / Vertex AI credentials configured in production. All LLM calls now go through Sarvam AI (sarvam-30b / sarvam-105b).

## What was changed
- `router.py`: `detect_language_and_route()` now returns `settings.SARVAM_MODEL` for both `en` and `as`. The old Vertex/Gemini branch was removed.
- `chat_service.py`: Sarvam→Vertex fallback (both streaming and non-streaming) removed. On failure, goes straight to dead-letter.
- `content_generation.py`: All `vertex_client.generate()` replaced with `sarvam_client.generate()`.
- `seo_generator.py`: Same — all generation uses `sarvam_client`.
- `requirements.in`: `google-cloud-aiplatform` and `google-cloud-discoveryengine` removed.
- `requirements.txt`: Removed `google-cloud-aiplatform`, `google-cloud-bigquery`, `google-cloud-discoveryengine`, `google-cloud-resource-manager`, `google-genai`, `docstring-parser`.

## What is kept in vertex_client.py
- `text_to_speech()` — uses Google Cloud TTS REST API (OAuth2 via `google-auth`). Still valid.
- `vision_analyze()` — uses Gemini Vision API. Will return 502 gracefully if no credentials, but has NOT been removed from code yet. The `/api/v1/chat/analyze-image` endpoint returns 502 in production.
- The file is still imported by `chat.py` (for TTS at `/tts`) and `main.py` (startup token warmup for TTS + shutdown cleanup).

## How to apply
- Any future content generation work: use `sarvam_client.generate()` or `sarvam_client.stream_generate_with_retry()`.
- Do not reference `settings.VERTEX_GEMINI_MODEL` for actual model routing — it's stale config kept only in metadata/display fields.
- `google-cloud-aiplatform` and `google-cloud-discoveryengine` are NOT in requirements.in; do not add them back.
