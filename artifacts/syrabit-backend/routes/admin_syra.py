"""Task #276 + #298 — Syra: voice coworker for the admin panel.

Endpoints
---------
``POST /api/admin/syra/chat``           — conversational turn (memory + screen context aware)
``POST /api/admin/syra/execute-action`` — run a registered write action (Task #298)
``GET  /api/admin/syra/actions``        — list registered actions (UI introspection)
``GET  /api/admin/syra/briefing``       — daily briefing paragraph (Task #298)
``POST /api/admin/syra/stt``            — Deepgram Nova-3 transcription
``POST /api/admin/syra/tts``            — Deepgram Aura-2 synthesis

All routes are admin-gated via ``get_admin_user``.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from auth_deps import get_admin_user
from llm import call_llm_api_chat
from providers import deepgram as _deepgram
from providers import assemblyai as _assemblyai
from syra_actions import (
    SyraActionError,
    execute as execute_action,
    list_actions as list_registered_actions,
)

logger = logging.getLogger(__name__)
router = APIRouter()


_SECTION_IDS = [
    "dashboard", "contenthub", "seomanager", "vertex", "automation",
    "users", "conversations", "feedback", "analytics", "monetization",
    "ads", "plans", "intelligence", "notifications", "apiconfig",
    "googleauth", "settings", "edubrowser", "ratelimits", "activitylog",
    "botsecurity", "logsexplorer", "health",
]


def _build_system_prompt(actions_json: str) -> str:
    return f"""You are Syra, the JARVIS-style voice coworker embedded in
the Syrabit.ai admin control panel. You speak ONLY to admin operators.
Keep replies short (≤ 2 sentences, ≤ 200 chars unless quoting a
specific number) — Deepgram Aura-2 will read them aloud. Be calm,
direct, and proactive: if the operator's wording is ambiguous but
they've selected an entity on screen (see "selected_entity" in
context), assume that entity. Use the conversation history to resolve
pronouns ("ban him", "do it for that user").

# Admin sections (use the exact id when navigating)
- dashboard, contenthub, seomanager, vertex, automation, users,
  conversations, feedback, analytics, monetization, ads, plans,
  intelligence, notifications, apiconfig, googleauth, settings,
  edubrowser, ratelimits, activitylog, botsecurity, logsexplorer,
  health.

# Architecture knowledge (answer questions about these):
- Provider pools: english_rag_chat (Azure GPT-4.1-mini → Vertex/Gemini
  → Workers AI Llama 3.3-70B), assamese_rag_chat (Sarvam-m → Vertex
  → Workers AI IndicTrans2), content (Vertex → Azure → Workers AI),
  tts (ElevenLabs → Deepgram → Workers AI), stt (Deepgram → AssemblyAI
  → Workers AI). All routed through Cloudflare AI Gateway with BYOK.
- Quizzes: Azure generates English master, Sarvam translates to
  Assamese + Hindi, all stored in MongoDB.
- Dual database: MongoDB Atlas (content, conversations, alerts,
  notifications, locks); PostgreSQL via Supabase (users, plans,
  credits, billing).
- Hosting: backend on Railway, frontend on Cloudflare Pages, edge
  Worker fronts api.syrabit.ai for KV-cached SEO + bot routing.

# Registered write actions (only these may be invoked):
{actions_json}

# Response format — ALWAYS reply with one JSON object, no prose
outside it, no markdown fences:
{{
  "action": "navigate" | "scroll" | "fetch" | "answer" | "run_action",
  "target": "<section id, css selector, landmark, or null>",
  "response": "<short spoken reply, <= 200 chars>",
  "action_id": "<one of the registered ids when action=run_action>",
  "params": {{ ... }},
  "confirm": "<short confirmation phrase, only for destructive actions>",
  "data": null
}}

# Action selection
- "navigate" — switch sections.
- "scroll"   — focus a card on the current section (target = data-syra
  landmark or heading text).
- "fetch"    — request a fresh data point (target hint, frontend fills
  numbers).
- "run_action" — invoke a registered write action by id; include the
  required params object. If the action is destructive, also write a
  short ``confirm`` phrase (e.g. "Ban user Priya?") that the frontend
  will read aloud and ask the operator to confirm.
- "answer"   — fallback / architecture / how-does-X questions.

If the operator asks for something not in the registry, do NOT invent
an action_id — explain politely and suggest the closest registered
verb.
"""


class _ChatTurn(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=2000)


class _SelectedEntity(BaseModel):
    type: str | None = None
    id: str | None = None
    label: str | None = None


class _ScreenContext(BaseModel):
    active_section: str = "dashboard"
    selected_entity: _SelectedEntity | None = None
    filters: dict[str, Any] | None = None
    visible_error: str | None = None


class SyraChatRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=2000)
    history: list[_ChatTurn] = Field(default_factory=list, max_length=16)
    context: _ScreenContext = Field(default_factory=_ScreenContext)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_syra_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
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


def _normalise_action_response(parsed: dict[str, Any], registered_ids: set[str]) -> dict[str, Any]:
    action = str(parsed.get("action") or "answer").lower().strip()
    if action not in ("navigate", "scroll", "fetch", "answer", "run_action"):
        action = "answer"

    target = parsed.get("target")
    if target is not None:
        target = str(target).strip() or None

    if action == "navigate" and target not in _SECTION_IDS:
        action, target = "answer", None

    action_id = parsed.get("action_id")
    if action_id is not None:
        action_id = str(action_id).strip() or None

    if action == "run_action":
        if not action_id or action_id not in registered_ids:
            # Hallucinated action — degrade to a plain answer rather than
            # leaking the invalid id to the frontend.
            action, action_id = "answer", None

    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    confirm = parsed.get("confirm")
    if confirm is not None:
        confirm = str(confirm).strip() or None

    response = str(parsed.get("response") or "").strip()
    if not response:
        response = "Done." if action != "answer" else "I'm not sure how to help with that yet."

    return {
        "action": action,
        "target": target,
        "action_id": action_id,
        "params": params,
        "confirm": confirm,
        "response": response[:600],
        "data": parsed.get("data") if isinstance(parsed.get("data"), dict) else None,
    }


@router.get("/admin/syra/actions")
async def syra_actions(admin: dict = Depends(get_admin_user)):
    return {"actions": list_registered_actions()}


@router.post("/admin/syra/chat")
async def syra_chat(req: SyraChatRequest, admin: dict = Depends(get_admin_user)):
    ctx = req.context or _ScreenContext()
    active = ctx.active_section if ctx.active_section in _SECTION_IDS else "dashboard"

    actions = list_registered_actions()
    registered_ids = {a["id"] for a in actions}
    actions_json = json.dumps(actions, indent=2)

    selected = ctx.selected_entity.model_dump() if ctx.selected_entity else None
    screen_blob = {
        "active_section": active,
        "selected_entity": selected,
        "filters": ctx.filters or {},
        "visible_error": ctx.visible_error,
    }

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _build_system_prompt(actions_json)},
    ]
    # Conversation memory — last few turns trimmed by Pydantic max_length.
    for turn in (req.history or [])[-8:]:
        messages.append({"role": turn.role, "content": turn.content[:1200]})

    user_msg = (
        f"Screen context: {json.dumps(screen_blob, ensure_ascii=False)}\n"
        f"Operator said: {req.transcript.strip()}\n\n"
        "Reply with the JSON object as specified."
    )
    messages.append({"role": "user", "content": user_msg})

    try:
        raw = await call_llm_api_chat(messages, max_tokens=512, lang="en")
    except Exception as exc:
        logger.warning("[syra] LLM call failed: %s", exc)
        return {
            "action": "answer",
            "target": None,
            "action_id": None,
            "params": {},
            "confirm": None,
            "response": "Sorry, the AI service is unreachable right now.",
            "data": None,
        }

    parsed = _parse_syra_json(str(raw))
    if not isinstance(parsed, dict):
        return {
            "action": "answer",
            "target": None,
            "action_id": None,
            "params": {},
            "confirm": None,
            "response": str(raw)[:400] if raw else "I didn't catch that. Could you repeat?",
            "data": None,
        }
    return _normalise_action_response(parsed, registered_ids)


# ── Action execution ────────────────────────────────────────────────────────
class _ExecuteRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


@router.post("/admin/syra/execute-action")
async def syra_execute_action(req: _ExecuteRequest, admin: dict = Depends(get_admin_user)):
    try:
        result = await execute_action(
            req.action_id, req.params, admin, confirmed=req.confirmed,
        )
    except SyraActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover — last-ditch
        logger.exception("[syra] action %s failed: %s", req.action_id, exc)
        raise HTTPException(status_code=500, detail=f"Action failed: {exc}")
    return result


# ── Per-admin preferences ───────────────────────────────────────────────────
# Code review (#298) flagged that storing prefs only in localStorage is not
# per-admin: two operators sharing a workstation (or one operator across
# multiple devices) would clobber each other's mute lists / wake-word
# choice. We keep a tiny ``admin_syra_prefs`` collection keyed by admin
# email; the frontend still mirrors the response into a namespaced
# localStorage entry for offline reads.
_PREF_KEYS = {
    "wakeWord", "briefing", "voiceRate", "persona",
    "mutedCategories", "proactiveAlerts", "greeting",
}


def _admin_pref_key(admin: dict) -> str:
    return (admin.get("email") or admin.get("username") or "admin").lower()


def _sanitize_prefs(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k not in _PREF_KEYS:
            continue
        if k == "mutedCategories":
            if isinstance(v, list):
                out[k] = [str(x)[:32] for x in v if isinstance(x, (str, int))][:16]
        elif k == "voiceRate":
            try:
                out[k] = max(0.7, min(1.3, float(v)))
            except Exception:
                pass
        elif k in {"wakeWord", "briefing", "proactiveAlerts", "greeting"}:
            out[k] = bool(v)
        elif k == "persona":
            out[k] = str(v)[:32]
    return out


@router.get("/admin/syra/prefs")
async def syra_get_prefs(admin: dict = Depends(get_admin_user)):
    try:
        from deps import db  # type: ignore

        doc = await db.admin_syra_prefs.find_one({"admin_email": _admin_pref_key(admin)})
        prefs = (doc or {}).get("prefs") if isinstance(doc, dict) else None
    except Exception as exc:
        logger.debug("[syra-prefs] load failed: %s", exc)
        prefs = None
    return {"prefs": _sanitize_prefs(prefs or {})}


class _PrefsRequest(BaseModel):
    prefs: dict[str, Any] = Field(default_factory=dict)


@router.put("/admin/syra/prefs")
async def syra_save_prefs(req: _PrefsRequest, admin: dict = Depends(get_admin_user)):
    cleaned = _sanitize_prefs(req.prefs or {})
    try:
        from deps import db  # type: ignore

        await db.admin_syra_prefs.update_one(
            {"admin_email": _admin_pref_key(admin)},
            {"$set": {
                "admin_email": _admin_pref_key(admin),
                "prefs": cleaned,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[syra-prefs] save failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not save preferences right now.")
    return {"prefs": cleaned}


# ── Daily briefing ──────────────────────────────────────────────────────────
async def _gather_briefing_facts(admin: dict) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "open_alerts": 0,
        "active_users_today": None,
        "new_signups_today": None,
        "negative_feedback_24h": None,
        "failed_jobs_24h": None,
    }
    try:
        from deps import db  # type: ignore

        facts["open_alerts"] = await db.alerts.count_documents({"acknowledged": False})
    except Exception as exc:
        logger.debug("briefing alerts count failed: %s", exc)
    try:
        from deps import db  # type: ignore

        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        facts["negative_feedback_24h"] = await db.chat_feedback.count_documents({
            "feedback": "down",
            "created_at": {"$gte": since},
        })
    except Exception as exc:
        logger.debug("briefing feedback count failed: %s", exc)
    try:
        from deps import db  # type: ignore

        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        facts["failed_jobs_24h"] = await db.cron_runs.count_documents({
            "status": "failed",
            "finished_at": {"$gte": since},
        })
    except Exception as exc:
        logger.debug("briefing job count failed: %s", exc)
    try:
        from deps import supa  # type: ignore

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        res = supa.table("users").select("id", count="exact").gte("created_at", today).execute()
        facts["new_signups_today"] = getattr(res, "count", None)
    except Exception as exc:
        logger.debug("briefing signups failed: %s", exc)
    return facts


@router.get("/admin/syra/briefing")
async def syra_briefing(admin: dict = Depends(get_admin_user)):
    facts = await _gather_briefing_facts(admin)

    name = (admin.get("name") or "").split(" ")[0] or "boss"
    parts: list[str] = [f"Good morning {name}."]

    open_alerts = facts.get("open_alerts") or 0
    if open_alerts:
        parts.append(f"{open_alerts} alert{'s' if open_alerts != 1 else ''} still open.")
    else:
        parts.append("No open alerts.")

    if facts.get("failed_jobs_24h"):
        parts.append(f"{facts['failed_jobs_24h']} cron job{'s' if facts['failed_jobs_24h'] != 1 else ''} failed in the last 24 hours.")
    if facts.get("negative_feedback_24h"):
        parts.append(f"{facts['negative_feedback_24h']} thumbs-down on chat in the last 24 hours.")
    if facts.get("new_signups_today"):
        parts.append(f"{facts['new_signups_today']} new signup{'s' if facts['new_signups_today'] != 1 else ''} so far today.")

    if len(parts) == 2:  # only greeting + alerts line
        parts.append("Otherwise the panel is quiet.")

    text = " ".join(parts)
    return {
        "text": text,
        "facts": facts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Deepgram-backed voice surface ───────────────────────────────────────────
_MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_TTS_CHARS = 1500


@router.post(
    "/admin/syra/stt",
    summary="Syra STT (Deepgram Nova-3, admin-gated)",
)
async def syra_stt(
    audio: UploadFile = File(..., description="Recorded voice command"),
    language: str = Form("en"),
    admin: dict = Depends(get_admin_user),
):
    # STT requires at least one provider — Deepgram is preferred for
    # latency, AssemblyAI is used as a middleman fallback when
    # Deepgram errors OR returns an empty transcript (often happens
    # for short Indian-accented utterances on Nova-3).
    if not (_deepgram.ENABLED or _assemblyai.ENABLED):
        raise HTTPException(
            status_code=503,
            detail="No STT provider configured (Deepgram + AssemblyAI both off).",
        )
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 10 MB).")

    transcript = ""
    used_provider = ""
    primary_error: str | None = None

    if _deepgram.ENABLED:
        try:
            transcript = await _deepgram.transcribe(audio_bytes, language_code=language)
            used_provider = "deepgram_nova3"
        except RuntimeError as exc:
            primary_error = str(exc)
            logger.warning("[syra-stt] deepgram failed, will try fallback: %s", exc)

    # Fallback path: empty transcript or Deepgram errored out. Many
    # short admin commands ("acknowledge alerts", "show signups
    # today") are missed by Deepgram on accented English — AssemblyAI
    # universal-2 is more forgiving, just slower.
    if (not (transcript or "").strip()) and _assemblyai.ENABLED:
        try:
            fb = await _assemblyai.transcribe(audio_bytes, language_code=language)
            if (fb or "").strip():
                transcript = fb
                used_provider = "assemblyai"
        except RuntimeError as exc:
            logger.error("[syra-stt] assemblyai fallback failed: %s", exc)
            if primary_error is None:
                primary_error = str(exc)

    if not (transcript or "").strip():
        # Both providers ran but nothing came back — surface a clear
        # error rather than a silent empty so the orb shows
        # "Didn't catch that" instead of executing a phantom command.
        if primary_error and not _assemblyai.ENABLED:
            raise HTTPException(status_code=502, detail=primary_error)
        raise HTTPException(
            status_code=422,
            detail="Could not transcribe audio. Speak closer to the mic and try again.",
        )

    return {
        "transcript": transcript.strip(),
        "language": language,
        "bytes_received": len(audio_bytes),
        "provider": used_provider,
    }


class _SyraTtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=_MAX_TTS_CHARS)
    language: str = Field("en", min_length=2, max_length=8)
    voice: str | None = Field(default=None, max_length=64)


@router.post(
    "/admin/syra/tts",
    response_class=Response,
    summary="Syra TTS (Deepgram Aura-2, admin-gated)",
)
async def syra_tts(req: _SyraTtsRequest, admin: dict = Depends(get_admin_user)):
    if not _deepgram.ENABLED:
        raise HTTPException(
            status_code=503,
            detail="No TTS provider configured (Deepgram off).",
        )
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text.")

    try:
        audio_bytes = await _deepgram.synthesize(
            text, voice=req.voice, language=req.language,
        )
        used = "deepgram_aura2"
    except RuntimeError as exc:
        logger.error("[syra-tts] deepgram failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    if not audio_bytes:
        raise HTTPException(
            status_code=502,
            detail="TTS provider returned empty audio.",
        )

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="syra.mp3"',
            "Cache-Control": "no-store",
            "X-TTS-Provider": used,
            "X-TTS-Lang": req.language,
            "X-TTS-Chars": str(len(text)),
            "X-TTS-Bytes": str(len(audio_bytes)),
        },
    )
