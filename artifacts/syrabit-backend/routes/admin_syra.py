"""Task #276 — Syra: voice assistant for the admin panel.

Exposes ``POST /api/admin/syra/chat`` — accepts the admin's spoken
transcript plus the currently-active section, calls the
``english_rag_chat`` provider pool through ``call_llm_api_chat`` (Azure
GPT-4o mini → Workers AI → Gemini), and returns a structured action the
frontend can execute (navigate / scroll / fetch / answer).

Strictly admin-gated via ``get_admin_user`` — the orb never appears for
students and the endpoint is unreachable without a valid admin session.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from auth_deps import get_admin_user
from llm import call_llm_api_chat
from providers import deepgram as _deepgram

logger = logging.getLogger(__name__)
router = APIRouter()


_SECTION_IDS = [
    "dashboard", "contenthub", "seomanager", "vertex", "automation",
    "users", "conversations", "feedback", "analytics", "monetization",
    "ads", "plans", "intelligence", "notifications", "apiconfig",
    "googleauth", "settings", "edubrowser", "ratelimits", "activitylog",
    "botsecurity", "logsexplorer", "health",
]

_SYSTEM_PROMPT = """You are Syra, the voice assistant embedded in the
Syrabit.ai admin control panel. You speak ONLY to the admin operator,
never to students. Be concise, friendly, and professional — your reply
will be read aloud by the browser's text-to-speech engine.

# Admin sections you can navigate to (use the exact id):
- dashboard      — KPIs, traffic, alerts overview
- contenthub     — content editor (boards, classes, subjects, chapters, blog)
- seomanager     — SEO topics, pages, sitemaps, internal links
- vertex         — Vertex AI Studio (model probes, Gemini health)
- automation     — scheduled jobs and pipelines
- users          — user list, plans, churn risk, credits, quotas
- conversations  — student chat history, FAQ extractor, sentiment
- feedback       — chat feedback (thumbs up/down) review
- analytics      — Cloudflare + GA4 analytics, funnels, daily stats
- monetization   — premium revenue, Stripe payouts
- ads            — AdSense / ad-network revenue
- plans          — pricing plans, credit configuration
- intelligence   — predictive intelligence, growth signals
- notifications  — push channels, email/Slack templates
- apiconfig      — third-party API keys / provider config
- googleauth     — Google OAuth + GA4 service-account status
- settings       — site-wide settings, maintenance mode
- edubrowser     — educational browser allowlist + educator submissions
- ratelimits     — rate-limit buckets and overrides
- activitylog    — admin audit log
- botsecurity    — bot-traffic alerts, suppressed alerts, WAF
- logsexplorer   — unified log search (edge worker + backend + cron)
- health         — infrastructure health, latency, cron pills

# Architecture knowledge (answer questions about these):
- Provider pools (PROVIDER_PRIORITY): english_rag_chat
  (Azure GPT-4.1-mini → Vertex/Gemini → Workers AI Llama 3.3-70B),
  assamese_rag_chat (Sarvam-m → Vertex/Gemini → Workers AI IndicTrans2),
  content (Vertex/Gemini → Azure → Workers AI), assamese_content
  (Workers AI IndicTrans2 → Vertex), tts (ElevenLabs → Deepgram →
  Vertex → Workers AI), stt (Deepgram → AssemblyAI → Vertex → Workers
  AI), embed (Cohere → Voyage → Workers AI), rerank, live_search.
  Every call is weighted round-robin and routed through Cloudflare AI
  Gateway with BYOK keys.
- Quiz generation: Azure GPT-4.1-mini generates the master quiz, then
  Sarvam translates into Assamese + Hindi; all three language copies
  are stored in MongoDB (quizzes collection).
- Cloudflare AI Gateway: unified observability + caching for every LLM
  call; BYOK headers attach the per-provider key so usage is billed
  against startup credits, not the gateway account.
- Dual-database split: MongoDB Atlas holds content, conversations,
  notifications, alerts, locks, bot reports; PostgreSQL (Supabase) holds
  user profiles, plans, credits, billing audit trail.
- Deployment: backend on Railway (FastAPI + uvicorn), frontend on
  Cloudflare Pages, edge proxy as a Cloudflare Worker in front of
  api.syrabit.ai for KV-cached SEO pages and bot routing.

# Response format — ALWAYS reply with a single JSON object, no prose
outside the JSON, no markdown fences:
{
  "action": "navigate" | "scroll" | "fetch" | "answer",
  "target": "<section id, css selector, or data-syra landmark>" or null,
  "response": "<short spoken reply, <= 200 chars>",
  "data": null
}

# Action selection rules:
- "navigate" — admin asked to switch sections ("go to health",
  "open users"). target = exact section id from the list above.
- "scroll" — admin asked to find/scroll to a card or heading on the
  current section ("scroll to MongoDB latency", "show me the active
  users card"). target = a short landmark name; the frontend resolves
  it via [data-syra="<target>"], heading text, or a fuzzy class match.
- "fetch" — admin asked for a live data point ("how many active users
  today", "what's the current error rate"). target = a hint identifying
  which data card the frontend should refresh (e.g. "active-users",
  "error-rate"). Keep response short — the frontend will append the
  actual numbers.
- "answer" — architecture / how-does-X-work questions, or anything the
  other actions can't satisfy. target = null. Put the full answer in
  "response" (still keep it under ~3 sentences for TTS).
"""


class _ChatContext(BaseModel):
    active_section: str = "dashboard"


class SyraChatRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=2000)
    context: _ChatContext = Field(default_factory=_ChatContext)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_syra_json(raw: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction — handles plain JSON, fenced blocks,
    or a JSON object embedded in surrounding prose."""
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


@router.post("/admin/syra/chat")
async def syra_chat(req: SyraChatRequest, admin: dict = Depends(get_admin_user)):
    active = req.context.active_section if req.context else "dashboard"
    if active not in _SECTION_IDS:
        active = "dashboard"

    user_msg = (
        f"Active admin section: {active}.\n"
        f"Admin command (spoken): {req.transcript.strip()}\n\n"
        "Reply with the JSON object as specified."
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    raw = ""
    try:
        raw = await call_llm_api_chat(messages, max_tokens=512, lang="en")
    except Exception as exc:
        logger.warning(f"[syra] LLM call failed: {exc}")
        return {
            "action": "answer",
            "target": None,
            "response": "Sorry, I couldn't reach the AI service right now. Please try again in a moment.",
            "data": None,
        }

    parsed = _parse_syra_json(str(raw))
    if not isinstance(parsed, dict):
        return {
            "action": "answer",
            "target": None,
            "response": str(raw)[:400] if raw else "I didn't catch that. Could you repeat?",
            "data": None,
        }

    action = str(parsed.get("action") or "answer").lower().strip()
    if action not in ("navigate", "scroll", "fetch", "answer"):
        action = "answer"

    target = parsed.get("target")
    if target is not None:
        target = str(target).strip() or None
    if action == "navigate" and target not in _SECTION_IDS:
        # Hallucinated section — degrade gracefully to a spoken answer.
        action = "answer"
        target = None

    response = str(parsed.get("response") or "").strip()
    if not response:
        response = "Done." if action != "answer" else "I'm not sure how to help with that yet."

    return {
        "action": action,
        "target": target,
        "response": response[:600],
        "data": parsed.get("data") if isinstance(parsed.get("data"), dict) else None,
    }


# ── Deepgram-backed voice surface for the admin Syra orb ─────────────────────
# Task #voice-agent: SyraAssistant used to rely on the browser's Web Speech
# API for STT and `window.speechSynthesis` for TTS — Firefox/some mobile
# browsers don't ship those, and the quality / Indic support is poor. These
# two endpoints route the orb through Deepgram (Nova-3 STT + Aura-2 TTS) so
# every browser gets the same studio-quality voice loop, gated to admins
# only via `get_admin_user` so we never burn Deepgram credits on anonymous
# traffic.

_MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB — Syra prompts are short.
_MAX_TTS_CHARS = 1500


@router.post(
    "/admin/syra/stt",
    summary="Syra STT (Deepgram Nova-3, admin-gated)",
    description=(
        "Transcribe a short admin voice command with Deepgram Nova-3. "
        "Accepts multipart/form-data with an `audio` field (webm/ogg/mp3/wav). "
        "Returns `{transcript, language}`. Strictly admin-gated."
    ),
)
async def syra_stt(
    audio: UploadFile = File(..., description="Recorded voice command (webm/ogg/mp3/wav)"),
    language: str = Form("en", description="BCP-47 language code (en, hi, as, bn)"),
    admin: dict = Depends(get_admin_user),
):
    if not _deepgram.ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Deepgram STT is not configured (DEEPGRAM_API_KEY missing).",
        )
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 10 MB).")
    try:
        transcript = await _deepgram.transcribe(audio_bytes, language_code=language)
    except RuntimeError as exc:
        logger.error("[syra-stt] deepgram failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "transcript": (transcript or "").strip(),
        "language": language,
        "bytes_received": len(audio_bytes),
    }


class _SyraTtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=_MAX_TTS_CHARS)
    language: str = Field("en", min_length=2, max_length=8)
    voice: str | None = Field(default=None, max_length=64)


@router.post(
    "/admin/syra/tts",
    response_class=Response,
    summary="Syra TTS (Deepgram Aura-2, admin-gated)",
    description=(
        "Synthesize a short Syra reply with Deepgram Aura-2 and return mp3 "
        "audio bytes. Strictly admin-gated."
    ),
)
async def syra_tts(req: _SyraTtsRequest, admin: dict = Depends(get_admin_user)):
    if not _deepgram.ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Deepgram TTS is not configured (DEEPGRAM_API_KEY missing).",
        )
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text.")
    try:
        audio_bytes = await _deepgram.synthesize(
            text, voice=req.voice, language=req.language,
        )
    except RuntimeError as exc:
        logger.error("[syra-tts] deepgram failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    if not audio_bytes:
        raise HTTPException(status_code=502, detail="Deepgram returned empty audio.")
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="syra.mp3"',
            "Cache-Control": "no-store",
            "X-TTS-Provider": "deepgram_aura2",
            "X-TTS-Lang": req.language,
            "X-TTS-Chars": str(len(text)),
            "X-TTS-Bytes": str(len(audio_bytes)),
        },
    )
