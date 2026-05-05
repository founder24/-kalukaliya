"""Syrabit.ai — Educational browser routes.

Public endpoints:
  POST /api/edu/reader/fetch          → return clean article text for a URL
  POST /api/edu/grounded-answer       → SSE stream: RAG + web + LLM answer
  GET  /api/edu/health                → reader / pipeline / cache snapshot

Admin endpoints (require admin JWT):
  GET    /api/admin/edu/allowlist            → effective allowlist + overrides
  POST   /api/admin/edu/allowlist            → add/update an override
  DELETE /api/admin/edu/allowlist/{domain}   → remove an override
  GET    /api/admin/edu/blocked-log          → recent blocked-request log
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

import time

from auth_deps import (
    get_admin_user, check_rate_limit, get_current_user_optional,
    get_educator_user,
)
from edu_allowlist import (
    effective_allowlist, list_overrides, upsert_override,
    remove_override, list_blocked_requests, _refresh_overrides_cache,
    EDU_ALLOWLIST_COLLECTION, EDU_REQUESTED_SITES_COLLECTION,
    EDU_USER_STATE_COLLECTION,
    _normalize_domain, is_allowed_url, is_domain_hard_blocked,
)
from edu_reader import fetch_and_extract, get_reader_stats, probe_site_safety
from grounded_answer import stream_grounded_answer, get_grounded_pipeline_stats
from cache import _redis_hit_count, _redis_miss_count
from deps import db, is_mongo_available
# Task #383 — Turnstile gate for the public domain-submission paths.
# Dormant when TURNSTILE_ON is false, so we can wire it now without
# disrupting the existing rate-limit fallback.
from turnstile import require_turnstile

logger = logging.getLogger(__name__)
router = APIRouter()

_RATE_READER_PER_IP = 30  # req / 60s
_RATE_GROUNDED_PER_IP = 12  # req / 60s


# ── Appeal-proof contract (Task #624) ────────────────────────────────────
#
# To stop a motivated educator from calling /educator/appeal-rejection
# directly to spam the admin queue with domains they never tried, we
# require a short-lived per-(educator, domain) "recently rejected by
# probe" marker. The marker is written by /educator/submit-site when
# the safety probe fails and expires after an hour.
#
# Fail-open on Redis unavailability — if the proof store is down we
# degrade to the previous behaviour (rate-limit + educator-role auth)
# rather than trap legitimate educators behind a verification step
# they cannot clear.

_APPEAL_PROOF_TTL = 3600  # 1 hour


def _appeal_proof_key(educator_id: str, domain: str) -> str:
    return f"edu_appeal_proof:{(educator_id or 'unknown')[:80]}:{domain}"


def _record_appeal_proof(educator_id: str, domain: str, reason: str) -> None:
    """Remember that `educator_id` hit a real probe rejection on
    `domain` within the past hour, so a subsequent appeal can verify
    the contract."""
    if not educator_id or not domain:
        return
    try:
        from deps import redis_client
        if redis_client is None:
            return
        redis_client.set(
            _appeal_proof_key(educator_id, domain),
            (reason or "probe_failed")[:80],
            ex=_APPEAL_PROOF_TTL,
        )
    except Exception as e:
        logger.debug(f"[edu_browser] appeal-proof record failed: {e}")


def _has_appeal_proof(educator_id: str, domain: str) -> bool:
    """True if `educator_id` submitted `domain` and saw a probe
    rejection within the last hour. Fail-open when Redis is not
    configured / errors so outages don't block legitimate appeals."""
    if not educator_id or not domain:
        return False
    try:
        from deps import redis_client
        if redis_client is None:
            return True  # degraded mode — don't trap users
        return redis_client.get(_appeal_proof_key(educator_id, domain)) is not None
    except Exception as e:
        logger.debug(f"[edu_browser] appeal-proof check failed: {e}")
        return True


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


# ───────────────────────── Models ─────────────────────────

class ReaderFetchReq(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    bypass_cache: bool = False


class GroundedAnswerReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    page_url: str = ""
    subject_id: str = ""
    subject_name: str = ""
    chapter_name: str = ""
    board_name: str = ""
    class_name: str = ""
    response_lang: str = "en"
    model: str = ""
    max_tokens: int = 1024
    message_id: Optional[str] = None


class RequestSiteReq(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    reason: str = Field("", max_length=500)


class StateSaveReq(BaseModel):
    tabs: list = Field(default_factory=list)
    bookmarks: list = Field(default_factory=list)
    history: list = Field(default_factory=list)


class AllowlistUpsertReq(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    status: str = "allowed"  # allowed | blocked
    note: str = ""


class EducatorSubmitReq(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    note: str = Field("", max_length=280)


class EducatorAppealReq(BaseModel):
    """Educator appeal of a probe rejection.

    Sent from the rejection card of EducatorSubmitPanel when the
    educator believes the safety probe was wrong (e.g. transient
    robots.txt block, mis-classified kid-safe density). The probe
    outcome the educator saw is captured verbatim so the admin can
    triage without re-running the probe.
    """
    domain: str = Field(..., min_length=3, max_length=253)
    reason: str = Field("", max_length=500)
    probe: dict = Field(default_factory=dict)
    probe_error: str = Field("", max_length=80)


# ───────────────────────── Public: reader ─────────────────────────

@router.post("/edu/reader/fetch")
async def reader_fetch(req: ReaderFetchReq, request: Request):
    ip = _client_ip(request)
    if not check_rate_limit(f"edu_reader:{ip}", max_requests=_RATE_READER_PER_IP, window_seconds=60):
        raise HTTPException(status_code=429, detail="Reader rate limit exceeded — try again in a minute.",
                            headers={"Retry-After": "60"})
    payload = await fetch_and_extract(
        req.url, actor=ip, ip_hash=_ip_hash(ip), bypass_cache=req.bypass_cache,
    )
    if not payload.get("ok"):
        # Map internal error codes to HTTP statuses so callers can branch.
        err = payload.get("error", "fetch_failed")
        if err in ("not_allowed", "redirect_not_allowed"):
            return JSONResponse(status_code=403, content=payload)
        if err == "robots_disallow":
            return JSONResponse(status_code=451, content=payload)
        if err == "timeout":
            return JSONResponse(status_code=504, content=payload)
        if err.startswith("http_"):
            return JSONResponse(status_code=502, content=payload)
        return JSONResponse(status_code=400, content=payload)
    return payload


# ───────────────────────── Public: grounded answer ─────────────────────────

@router.post("/edu/grounded-answer")
async def grounded_answer(req: GroundedAnswerReq, request: Request):
    ip = _client_ip(request)
    if not check_rate_limit(f"edu_grounded:{ip}", max_requests=_RATE_GROUNDED_PER_IP, window_seconds=60):
        raise HTTPException(status_code=429, detail="Answer rate limit exceeded — try again in a minute.",
                            headers={"Retry-After": "60"})

    cancel_event = asyncio.Event()

    async def _stream_with_disconnect_watch():
        watcher = asyncio.create_task(_watch_disconnect(request, cancel_event))
        try:
            async for chunk in stream_grounded_answer(
                query=req.query,
                page_url=req.page_url,
                subject_id=req.subject_id,
                subject_name=req.subject_name,
                chapter_name=req.chapter_name,
                board_name=req.board_name,
                class_name=req.class_name,
                response_lang=req.response_lang or "en",
                model=req.model or "",
                max_tokens=int(req.max_tokens or 1024),
                actor=ip,
                ip_hash=_ip_hash(ip),
                message_id=req.message_id,
                cancel_event=cancel_event,
            ):
                yield chunk
        finally:
            watcher.cancel()

    return StreamingResponse(
        _stream_with_disconnect_watch(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _watch_disconnect(request: Request, cancel_event: asyncio.Event) -> None:
    """Set `cancel_event` if the client closes the connection mid-stream."""
    try:
        while not cancel_event.is_set():
            if await request.is_disconnected():
                cancel_event.set()
                return
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.debug(f"[edu_browser] disconnect watcher: {e}")


# ───────────────────────── Public: allowlist & site requests ─────────────────────────

@router.get("/edu/allowlist")
async def public_allowlist():
    """Public snapshot of allowed domains for the address-bar UI."""
    await _refresh_overrides_cache()
    snap = effective_allowlist()
    base = sorted(set(snap.get("base", [])) | set(snap.get("operator_allowed", [])))
    blocked = sorted(set(snap.get("operator_blocked", [])) | set(snap.get("hard_denied", [])))
    return {
        "ok": True,
        "domains": base,
        "blocked": blocked,
        "edu_suffixes": snap.get("edu_suffixes", []),
    }


@router.post("/edu/check-url")
async def public_check_url(payload: dict):
    """Lightweight allow check used by the browser shell before fetching."""
    url = (payload or {}).get("url", "")
    allowed, reason = await is_allowed_url(url)
    return {"ok": True, "allowed": allowed, "reason": reason}


@router.post("/edu/request-site",
             dependencies=[Depends(require_turnstile)])
async def request_site(req: RequestSiteReq, request: Request, user=Depends(get_current_user_optional)):
    ip = _client_ip(request)
    if not check_rate_limit(f"edu_req_site:{ip}", max_requests=10, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many requests; try again later.")
    domain = _normalize_domain(req.domain)
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="Invalid domain")
    if not await is_mongo_available():
        raise HTTPException(status_code=503, detail="storage_unavailable")
    actor = (user or {}).get("email", "") or (user or {}).get("id", "") or _ip_hash(ip)
    try:
        await db[EDU_REQUESTED_SITES_COLLECTION].update_one(
            {"domain": domain},
            {
                "$inc": {"count": 1},
                "$setOnInsert": {"domain": domain, "first_at": time.time()},
                "$set": {"last_at": time.time(), "last_actor": actor[:120], "last_reason": req.reason[:500]},
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"[edu_browser] request-site insert failed: {e}")
        raise HTTPException(status_code=500, detail="storage_failed")
    return {"ok": True, "domain": domain}


# ───────────────────────── Educator: self-serve allowlist ─────────────────────

_RATE_EDUCATOR_SUBMIT_PER_USER = 12  # req / hour


@router.post("/edu/educator/submit-site",
             dependencies=[Depends(require_turnstile)])
async def educator_submit_site(
    req: EducatorSubmitReq, request: Request, educator=Depends(get_educator_user),
):
    """Educator self-serve: submit a domain that auto-approves after a
    kid-safe + robots.txt safety probe. Admins can always revoke later
    via the existing allowlist management endpoints.
    """
    actor = (educator or {}).get("email", "") or (educator or {}).get("id", "")
    rl_key = f"edu_edu_submit:{(educator or {}).get('id', _client_ip(request))}"
    if not check_rate_limit(rl_key, max_requests=_RATE_EDUCATOR_SUBMIT_PER_USER, window_seconds=3600):
        raise HTTPException(
            status_code=429,
            detail=f"Educator submission limit reached ({_RATE_EDUCATOR_SUBMIT_PER_USER}/hour).",
            headers={"Retry-After": "300"},
        )

    domain = _normalize_domain(req.domain)
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="Invalid domain")

    # Cheap fast-path: if the domain is already allowed (base list, edu
    # suffix, or an operator_allowed override) we short-circuit without
    # burning a network fetch.
    allowed_already, reason_already = await is_allowed_url(f"https://{domain}/")
    if allowed_already:
        return {
            "ok": True,
            "domain": domain,
            "status": "already_allowed",
            "detail": reason_already,
        }

    # Refuse anything admin has hard-blocked / operator-blocked outright.
    blocked, why = await is_domain_hard_blocked(domain)
    if blocked:
        return JSONResponse(
            status_code=403,
            content={"ok": False, "domain": domain, "error": why,
                     "detail": "Domain is blocked and cannot be auto-approved."},
        )

    probe = await probe_site_safety(domain)
    if not probe.get("ok"):
        # Task #624 — stash proof of this rejection so the educator
        # can follow up with /educator/appeal-rejection within the
        # next hour. Without this marker the appeal endpoint refuses
        # the request, preventing API-level spam of un-probed domains.
        educator_id = (educator or {}).get("id", "") or (educator or {}).get("email", "") or actor
        _record_appeal_proof(educator_id, domain, probe.get("reason", "probe_failed"))
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "domain": domain,
                "error": probe.get("reason", "probe_failed"),
                "probe": probe,
                "detail": "Safety probe failed — site was not auto-approved.",
            },
        )

    note = req.note.strip()[:240] if req.note else ""
    auto_note = f"Auto-approved by educator {actor or 'unknown'}"
    if note:
        auto_note = f"{auto_note}: {note}"
    try:
        entry = await upsert_override(
            domain,
            status="allowed",
            note=auto_note,
            actor=actor[:120] or "educator",
            source="educator",
            extra={
                "kid_safe_density": probe.get("kid_safe_density"),
                "http_status": probe.get("http_status"),
                "robots_ok": probe.get("robots_ok"),
                "text_chars": probe.get("text_chars"),
            },
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=503, detail=str(re))

    return {
        "ok": True,
        "domain": domain,
        "status": "auto_approved",
        "entry": entry,
        "probe": {
            "kid_safe": probe.get("kid_safe"),
            "kid_safe_density": probe.get("kid_safe_density"),
            "robots_ok": probe.get("robots_ok"),
            "http_status": probe.get("http_status"),
        },
    }


# ── Appeal spike alerting (Task #625) ────────────────────────────────────
#
# If the safety probe starts mis-classifying a popular site, every
# educator will hit the rejection card and tap the appeal button. The
# admin queue silently grows but nobody notices until the Site requests
# tab is opened. To catch this fast we fire a Slack webhook (and expose
# an `alerted_at` field for a dashboard banner) once a single domain
# crosses _APPEAL_SPIKE_THRESHOLD unique appeals.
#
# Idempotent: `alerted_at` is set atomically with a conditional update
# so only one request wins the race. The field is re-armed (cleared)
# when an admin dismiss → re-appeal cycle brings a domain back into the
# queue, so a second spike after a dismissal re-alerts.

_APPEAL_SPIKE_THRESHOLD = 5
_APPEAL_ALERT_WEBHOOK_ENV = "EDU_APPEAL_ALERT_WEBHOOK"
_APPEAL_ALERT_ADMIN_URL_ENV = "EDU_APPEAL_ALERT_ADMIN_URL"


async def _post_slack_webhook(url: str, text: str) -> None:
    """Best-effort Slack webhook POST. Swallows all errors so alerting
    trouble never breaks the appeal write path."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"text": text})
    except Exception as e:
        logger.warning(f"[edu_browser] appeal-spike webhook failed: {e}")


def _spawn_appeal_spike_alert(domain: str, appeal_count: int) -> None:
    """Fire-and-forget Slack alert for a domain whose appeal count
    crossed the spike threshold. No-op when the webhook env is not set
    — the UI banner is still driven by the `alerted_at` field on the
    row, so admins who don't watch Slack still see the spike."""
    webhook = os.environ.get(_APPEAL_ALERT_WEBHOOK_ENV, "").strip()
    if not webhook:
        logger.info(
            "[edu_browser] appeal-spike detected domain=%s count=%s (no webhook configured)",
            domain, appeal_count,
        )
        return
    admin_link = os.environ.get(_APPEAL_ALERT_ADMIN_URL_ENV, "").strip()
    if admin_link:
        # Link to the requested-sites tab, filtered to the spiking
        # domain. The AdminEduBrowser component reads `tab` and
        # `domain` from the URL on mount and switches to the
        # requested-sites tab with the domain pre-filled in the
        # filter box. `quote` is belt-and-braces: _normalize_domain
        # already rejects anything that would need escaping.
        from urllib.parse import quote
        sep = "&" if "?" in admin_link else "?"
        admin_link = (
            f"{admin_link}{sep}tab=requested&domain={quote(domain, safe='')}"
        )
        body = (
            f":rotating_light: Educator appeals spiking on *{domain}* "
            f"({appeal_count} appeals). Review: {admin_link}"
        )
    else:
        body = (
            f":rotating_light: Educator appeals spiking on *{domain}* "
            f"({appeal_count} appeals). Open the admin Site requests tab."
        )
    try:
        asyncio.create_task(_post_slack_webhook(webhook, body))
    except RuntimeError:
        # No running loop (e.g. sync test harness) — log & move on.
        logger.info(
            "[edu_browser] appeal-spike alert queued but no loop: domain=%s count=%s",
            domain, appeal_count,
        )


async def _maybe_alert_appeal_spike(domain: str) -> None:
    """Atomically claim the right to alert for this spike and fire it.

    Runs after the appeal upsert. Only the request that crosses the
    threshold AND finds `alerted_at` unset will succeed the conditional
    update — everyone else sees modified_count==0 and no-ops. This
    keeps the alert exactly-once per spike cycle (dismiss → re-appeal
    resets the cycle via ``alerted_at=None`` in the upsert $set).
    """
    try:
        res = await db[EDU_REQUESTED_SITES_COLLECTION].update_one(
            {
                "domain": domain,
                "appeal_count": {"$gte": _APPEAL_SPIKE_THRESHOLD},
                "dismissed": {"$ne": True},
                "$or": [
                    {"alerted_at": None},
                    {"alerted_at": {"$exists": False}},
                ],
            },
            {"$set": {"alerted_at": time.time()}},
        )
    except Exception as e:
        logger.warning(f"[edu_browser] appeal-spike claim failed: {e}")
        return
    if not getattr(res, "modified_count", 0):
        return
    try:
        row = await db[EDU_REQUESTED_SITES_COLLECTION].find_one(
            {"domain": domain}, {"_id": 0, "appeal_count": 1},
        )
    except Exception:
        row = None
    count = int((row or {}).get("appeal_count") or _APPEAL_SPIKE_THRESHOLD)
    logger.warning(
        "[edu_browser] appeal-spike threshold crossed domain=%s count=%s",
        domain, count,
    )
    _spawn_appeal_spike_alert(domain, count)


_RATE_EDUCATOR_APPEAL_PER_USER = 8  # req / hour


@router.post("/edu/educator/appeal-rejection")
async def educator_appeal_rejection(
    req: EducatorAppealReq, request: Request, educator=Depends(get_educator_user),
):
    """Queue a probe-rejected domain for manual admin review.

    Used when an educator hits the rejection card in
    EducatorSubmitPanel and believes the safety probe was wrong (a
    transient robots.txt block, a mis-classified page, etc.). Writes
    to ``EDU_REQUESTED_SITES_COLLECTION`` with ``source=educator_appeal``
    and the captured probe outcome so an admin can triage without
    re-running the probe. Idempotent per domain — repeat appeals just
    bump the counter and refresh the latest probe snapshot.
    """
    actor = (educator or {}).get("email", "") or (educator or {}).get("id", "")
    rl_key = f"edu_edu_appeal:{(educator or {}).get('id', _client_ip(request))}"
    if not check_rate_limit(rl_key, max_requests=_RATE_EDUCATOR_APPEAL_PER_USER, window_seconds=3600):
        raise HTTPException(
            status_code=429,
            detail=f"Educator appeal limit reached ({_RATE_EDUCATOR_APPEAL_PER_USER}/hour).",
            headers={"Retry-After": "300"},
        )

    domain = _normalize_domain(req.domain)
    if not domain or "." not in domain:
        raise HTTPException(status_code=400, detail="Invalid domain")

    # Task #624 — require proof that this educator actually hit a
    # probe rejection on this domain within the last hour via
    # /educator/submit-site. Blocks API-level spam of un-probed
    # domains while the frontend-only "only show appeal after
    # rejection" contract remains a defence-in-depth layer. Fails
    # open if Redis is unavailable (see _has_appeal_proof).
    educator_id_proof = (educator or {}).get("id", "") or (educator or {}).get("email", "") or actor
    if not _has_appeal_proof(educator_id_proof, domain):
        raise HTTPException(
            status_code=400,
            detail=(
                "no_recent_rejection — submit this domain via "
                "/educator/submit-site first; we only accept appeals "
                "for a real probe rejection within the last hour."
            ),
        )

    # Hard-blocked / operator-blocked domains can't be reviewed in via
    # an appeal — they need an explicit admin decision through the
    # allowlist editor, not a queued request.
    blocked, why = await is_domain_hard_blocked(domain)
    if blocked:
        return JSONResponse(
            status_code=403,
            content={"ok": False, "domain": domain, "error": why,
                     "detail": "Domain is permanently blocked and cannot be appealed."},
        )

    if not await is_mongo_available():
        raise HTTPException(status_code=503, detail="storage_unavailable")

    # Snapshot only the fields we care about — never echo the full
    # request blob into mongo (could leak unexpected client-supplied
    # keys into the admin UI).
    probe_snapshot = {
        "reason": (req.probe_error or req.probe.get("reason") or "")[:80],
        "kid_safe": req.probe.get("kid_safe"),
        "kid_safe_density": req.probe.get("kid_safe_density"),
        "robots_ok": req.probe.get("robots_ok"),
        "http_status": req.probe.get("http_status"),
    }
    now = time.time()
    # Task #625 — if this row was previously soft-dismissed, clear the
    # prior spike-alert marker alongside `dismissed` so a second spike
    # after the dismiss re-alerts. We only clear `alerted_at` on this
    # specific transition (not on every appeal), otherwise every single
    # appeal past the threshold would re-fire the alert.
    try:
        prior = await db[EDU_REQUESTED_SITES_COLLECTION].find_one(
            {"domain": domain}, {"_id": 0, "dismissed": 1},
        )
    except Exception:
        prior = None
    set_fields = {
        "last_at": now,
        "last_actor": actor[:120] or "educator",
        "last_reason": (req.reason or "").strip()[:500]
            or "Educator vouched: appealed probe rejection",
        "source": "educator_appeal",
        "appeal": True,
        "last_probe": probe_snapshot,
        "last_appeal_at": now,
        # Task #623 — a fresh appeal must clear any prior
        # soft-dismiss so the row re-enters the admin
        # queue and /my-appeals shows `pending` again.
        "dismissed": False,
        "dismissed_at": None,
    }
    if prior and prior.get("dismissed"):
        set_fields["alerted_at"] = None
    try:
        await db[EDU_REQUESTED_SITES_COLLECTION].update_one(
            {"domain": domain},
            {
                "$inc": {"count": 1, "appeal_count": 1},
                "$setOnInsert": {"domain": domain, "first_at": now},
                "$set": set_fields,
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"[edu_browser] educator appeal insert failed: {e}")
        raise HTTPException(status_code=500, detail="storage_failed")
    # Task #625 — atomically claim the spike alert if this write
    # crossed the threshold. Best-effort; alerting failures never
    # surface to the educator.
    try:
        await _maybe_alert_appeal_spike(domain)
    except Exception as e:
        logger.debug(f"[edu_browser] appeal-spike check failed: {e}")
    logger.info(
        "[edu_browser] educator appeal queued domain=%s actor=%s probe_reason=%s",
        domain, actor[:60] or "educator", probe_snapshot["reason"],
    )
    return {
        "ok": True,
        "domain": domain,
        "status": "queued",
        "source": "educator_appeal",
    }


@router.delete("/edu/educator/my-submissions/{domain}")
async def educator_remove_my_submission(
    domain: str, educator=Depends(get_educator_user),
):
    """Educator self-undo: remove a domain the calling educator added.

    Guard: only entries with ``source=educator`` AND ``actor`` matching
    the caller's clamped identifier may be removed. Admin-added rows
    (or another educator's rows) return 404 — the educator never learns
    they exist via this endpoint, and admin overrides stay untouchable.
    """
    if not await is_mongo_available():
        raise HTTPException(status_code=503, detail="storage_unavailable")
    raw_actor = (educator or {}).get("email", "") or (educator or {}).get("id", "")
    actor = raw_actor[:120] if raw_actor else ""
    if not actor:
        raise HTTPException(status_code=403, detail="no_actor_identity")
    d = _normalize_domain(domain)
    if not d or "." not in d:
        raise HTTPException(status_code=400, detail="Invalid domain")
    # Atomic conditional delete — the {source, actor} guard is part of
    # the delete predicate so a concurrent admin update / promotion of
    # this domain cannot be raced into removing protected state.
    # ``deleted_count == 0`` means either (a) no such row, (b) admin
    # owns it, or (c) another educator owns it. All three collapse to
    # 404 so the educator never learns which case applied.
    try:
        res = await db[EDU_ALLOWLIST_COLLECTION].delete_one({
            "domain": d, "source": "educator", "actor": actor,
        })
    except Exception as e:
        logger.warning(f"[edu_browser] my-submission remove failed: {e}")
        raise HTTPException(status_code=500, detail="storage_failed")
    if not getattr(res, "deleted_count", 0):
        raise HTTPException(status_code=404, detail="not_found")
    # Bust the in-memory allowlist cache so the next request sees the
    # removal immediately (mirrors what `remove_override` does).
    try:
        from edu_allowlist import invalidate_cache as _invalidate
        _invalidate()
    except Exception:
        pass
    logger.info("[edu_browser] educator self-removed domain=%s actor=%s", d, actor[:60])
    return {"ok": True, "domain": d, "removed": True}


@router.get("/edu/educator/my-appeals")
async def educator_my_appeals(
    limit: int = 10, educator=Depends(get_educator_user),
):
    """Return the calling educator's most recent rejection appeals
    plus their current verdict.

    Cross-references ``EDU_REQUESTED_SITES_COLLECTION`` (where the
    appeal queue lives) with ``EDU_ALLOWLIST_COLLECTION`` (where the
    admin's verdict lands). For each appealed domain we report:

      * status='allowed'   — admin allowed the domain (override exists)
      * status='dismissed' — admin explicitly dismissed the appeal
                             (``dismissed=true`` soft-delete flag)
      * status='pending'   — still queued for admin review

    Soft-dismissed rows are surfaced here (rather than disappearing
    from the list) so educators see the actual outcome of their
    appeal instead of silently re-trying the same domain.
    """
    if not await is_mongo_available():
        return {"ok": True, "items": [], "count": 0}
    raw_actor = (educator or {}).get("email", "") or (educator or {}).get("id", "")
    actor = raw_actor[:120] if raw_actor else ""
    if not actor:
        return {"ok": True, "items": [], "count": 0}
    limit = max(1, min(50, int(limit or 10)))
    try:
        # Include both pending and soft-dismissed rows — the admin's
        # dismiss action only flips the `dismissed` flag, so both
        # statuses live on the same row.
        cur = db[EDU_REQUESTED_SITES_COLLECTION].find(
            {"source": "educator_appeal", "last_actor": actor}, {"_id": 0},
        ).sort("last_appeal_at", -1).limit(limit)
        appeals = [doc async for doc in cur]
    except Exception as e:
        logger.warning(f"[edu_browser] my-appeals list failed: {e}")
        appeals = []

    items: list[dict] = []
    for appeal in appeals:
        domain = appeal.get("domain") or ""
        status = "pending"
        verdict_at = None
        # Allow verdict wins over dismiss (admin can allow after a
        # prior dismiss), then dismiss, then pending.
        try:
            override = await db[EDU_ALLOWLIST_COLLECTION].find_one(
                {"domain": domain}, {"_id": 0, "status": 1, "updated_at": 1},
            )
        except Exception:
            override = None
        if override and (override.get("status") or "").lower() == "allowed":
            status = "allowed"
            verdict_at = override.get("updated_at")
        elif appeal.get("dismissed"):
            status = "dismissed"
            verdict_at = appeal.get("dismissed_at")
        items.append({
            "domain": domain,
            "status": status,
            "appealed_at": appeal.get("last_appeal_at"),
            "appeal_count": appeal.get("appeal_count", 1),
            "last_probe": appeal.get("last_probe") or {},
            "verdict_at": verdict_at,
        })
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/edu/educator/my-submissions")
async def educator_my_submissions(
    limit: int = 10, educator=Depends(get_educator_user),
):
    """Return the calling educator's most recent self-serve submissions.

    Filters the shared allowlist collection by ``source=educator`` and the
    educator's clamped actor identifier (email or id, the same value the
    submit endpoint records). The panel uses this to show educators a
    short history of what they've previously added — domain, current
    status, when it was approved, and the kid-safe density measured by
    the probe.
    """
    if not await is_mongo_available():
        return {"ok": True, "items": [], "count": 0}
    raw_actor = (educator or {}).get("email", "") or (educator or {}).get("id", "")
    actor = raw_actor[:120] if raw_actor else ""
    if not actor:
        return {"ok": True, "items": [], "count": 0}
    limit = max(1, min(50, int(limit or 10)))
    try:
        cur = db[EDU_ALLOWLIST_COLLECTION].find(
            {"source": "educator", "actor": actor}, {"_id": 0},
        ).sort("updated_at", -1).limit(limit)
        items = [doc async for doc in cur]
    except Exception as e:
        logger.warning(f"[edu_browser] my-submissions list failed: {e}")
        items = []
    return {"ok": True, "items": items, "count": len(items)}


# ───────────────────────── Public: per-user state (tabs/bookmarks/history) ────

_STATE_MAX_TABS = 30
_STATE_MAX_BOOKMARKS = 200
_STATE_MAX_HISTORY = 200


def _trim_state(payload: StateSaveReq) -> dict:
    return {
        "tabs": (payload.tabs or [])[:_STATE_MAX_TABS],
        "bookmarks": (payload.bookmarks or [])[:_STATE_MAX_BOOKMARKS],
        "history": (payload.history or [])[:_STATE_MAX_HISTORY],
    }


def _state_actor(request: Request, user) -> tuple[str, str]:
    if user and user.get("id"):
        return "user", user["id"]
    anon = request.headers.get("x-anon-id", "").strip()[:80]
    if anon:
        return "anon", anon
    return "ip", _ip_hash(_client_ip(request))


@router.get("/edu/state")
async def get_state(request: Request, user=Depends(get_current_user_optional)):
    if not await is_mongo_available():
        return {"ok": True, "state": None, "stored": False}
    kind, actor = _state_actor(request, user)
    try:
        doc = await db[EDU_USER_STATE_COLLECTION].find_one(
            {"actor_kind": kind, "actor": actor}, {"_id": 0},
        )
    except Exception as e:
        logger.warning(f"[edu_browser] state load failed: {e}")
        return {"ok": True, "state": None, "stored": False}
    return {
        "ok": True,
        "state": doc.get("state") if doc else None,
        "stored": bool(doc),
        "scope": kind,
    }


@router.post("/edu/state")
async def save_state(payload: StateSaveReq, request: Request, user=Depends(get_current_user_optional)):
    ip = _client_ip(request)
    if not check_rate_limit(f"edu_state:{ip}", max_requests=60, window_seconds=60):
        raise HTTPException(status_code=429, detail="Save rate limit exceeded.")
    if not await is_mongo_available():
        return {"ok": False, "error": "storage_unavailable"}
    kind, actor = _state_actor(request, user)
    state = _trim_state(payload)
    try:
        await db[EDU_USER_STATE_COLLECTION].update_one(
            {"actor_kind": kind, "actor": actor},
            {"$set": {"state": state, "updated_at": time.time(),
                      "actor_kind": kind, "actor": actor}},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"[edu_browser] state save failed: {e}")
        raise HTTPException(status_code=500, detail="storage_failed")
    return {"ok": True, "scope": kind, "saved": {
        "tabs": len(state["tabs"]), "bookmarks": len(state["bookmarks"]),
        "history": len(state["history"]),
    }}


# ───────────────────────── Public: health ─────────────────────────

@router.get("/edu/health")
async def edu_health():
    """Smoke endpoint: reader stats + pipeline stats + redis cache stats."""
    total = _redis_hit_count + _redis_miss_count
    redis_hit_rate = (_redis_hit_count / total * 100) if total else 0.0
    return {
        "ok": True,
        "reader": get_reader_stats(),
        "pipeline": get_grounded_pipeline_stats(),
        "redis_cache": {
            "hits": _redis_hit_count,
            "misses": _redis_miss_count,
            "hit_rate_pct": round(redis_hit_rate, 1),
        },
    }


# ───────────────────────── Admin: allowlist CRUD ─────────────────────────

@router.get("/admin/edu/allowlist")
async def admin_allowlist_list(_admin=Depends(get_admin_user)):
    await _refresh_overrides_cache(force=True)
    return {
        "effective": effective_allowlist(),
        "overrides": await list_overrides(),
    }


@router.post("/admin/edu/allowlist")
async def admin_allowlist_upsert(req: AllowlistUpsertReq, admin=Depends(get_admin_user)):
    try:
        doc = await upsert_override(
            req.domain, status=req.status, note=req.note,
            actor=admin.get("email", admin.get("id", "admin")),
        )
        return {"ok": True, "entry": doc}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=503, detail=str(re))


@router.delete("/admin/edu/allowlist/{domain}")
async def admin_allowlist_delete(domain: str, _admin=Depends(get_admin_user)):
    removed = await remove_override(domain)
    return {"ok": True, "removed": removed}


@router.get("/admin/edu/blocked-log")
async def admin_blocked_log(limit: int = 200, _admin=Depends(get_admin_user)):
    items = await list_blocked_requests(limit=limit)
    return {"ok": True, "items": items, "count": len(items)}


# ───────────────────────── Admin: grounded-recall bench ─────────────────────────

@router.get("/admin/grounded-recall/latest")
async def admin_grounded_recall_latest(
    language: str | None = None,
    _admin=Depends(get_admin_user),
):
    """Return the most recent grounded-answer recall benchmark run + baseline.

    The nightly job writes `bench/results/latest.json` (global) and
    `bench/results/latest_<lang>.json` (per-language subsets, e.g.
    Assamese — Task #599); this endpoint surfaces them (plus the
    matching committed baseline) so the admin UI can render a
    retrieval-quality tile without running the bench in-process.

    Pass ``?language=as`` to read the Assamese-only subset.
    """
    try:
        from bench.grounded_recall import find_latest_result, load_baseline
        lang = (language or "").strip().lower() or None
        latest = find_latest_result(language=lang)
        baseline = load_baseline(language=lang)
        return {
            "ok": True,
            "language": lang,
            "latest": latest,
            "baseline": baseline,
            "has_results": latest is not None,
        }
    except Exception as e:
        logger.warning(f"[admin] grounded-recall fetch failed: {e}")
        return {"ok": False, "error": str(e)[:200], "latest": None, "baseline": None}


# ───────────────────────── Admin: provider-speed bench (Task #279) ─────────────────────────

@router.get("/admin/bench/latest")
async def admin_bench_latest(_admin=Depends(get_admin_user)):
    """Return the most recent LLM provider speed benchmark run.

    The bench script (``python -m scripts.bench_llm_providers``) writes
    ``bench_results/latest.json`` after every run; this endpoint surfaces
    that file so the admin Health "Provider Latency" card can render
    p50 TTFT per provider without re-running the bench in-process.
    """
    try:
        from pathlib import Path as _Path
        import json as _json
        # Resolve relative to the syrabit-backend root (this file lives in routes/).
        latest = _Path(__file__).resolve().parent.parent / "bench_results" / "latest.json"
        if not latest.exists():
            return {"ok": True, "has_results": False, "latest": None}
        with open(latest, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return {"ok": True, "has_results": True, "latest": data}
    except Exception as e:
        logger.warning(f"[admin] bench/latest fetch failed: {e}")
        return {"ok": False, "error": str(e)[:200], "latest": None}


# ───────────────────────── Admin: requested-sites review queue ─────────────────────────

@router.get("/admin/edu/requested-sites")
async def admin_requested_sites(limit: int = 200, _admin=Depends(get_admin_user)):
    """Review queue for user-submitted "request this site" entries.

    Soft-dismissed rows (``dismissed=true``) are filtered out so the
    admin queue only shows items that still need action. Task #623
    keeps those rows in the collection so the educator who filed the
    appeal can see the ``dismissed`` verdict on /my-appeals.
    """
    if not await is_mongo_available():
        return {"ok": True, "items": [], "count": 0}
    try:
        cur = db[EDU_REQUESTED_SITES_COLLECTION].find(
            {"dismissed": {"$ne": True}}, {"_id": 0}
        ).sort("last_at", -1).limit(max(1, min(limit, 1000)))
        items = [doc async for doc in cur]
    except Exception as e:
        logger.warning(f"[edu_browser] requested-sites list failed: {e}")
        items = []
    return {"ok": True, "items": items, "count": len(items)}


@router.delete("/admin/edu/requested-sites/{domain}")
async def admin_dismiss_requested_site(domain: str, _admin=Depends(get_admin_user)):
    """Admin dismiss of a queued request / educator appeal.

    Task #623 — we now **soft-delete** educator appeals (mark
    ``dismissed=true`` + ``dismissed_at`` timestamp) instead of hard-
    deleting the row, so the originating educator can see an explicit
    "dismissed by admin" verdict on GET /edu/educator/my-appeals.
    Non-appeal rows (plain /request-site submissions) still hard-
    delete because no educator is waiting on a verdict for those.
    """
    if not await is_mongo_available():
        raise HTTPException(status_code=503, detail="storage_unavailable")
    d = _normalize_domain(domain)
    if not d:
        raise HTTPException(status_code=400, detail="Invalid domain")
    try:
        existing = await db[EDU_REQUESTED_SITES_COLLECTION].find_one(
            {"domain": d}, {"_id": 0, "source": 1},
        )
        if existing and (existing.get("source") or "") == "educator_appeal":
            await db[EDU_REQUESTED_SITES_COLLECTION].update_one(
                {"domain": d},
                {"$set": {"dismissed": True, "dismissed_at": time.time()}},
            )
        else:
            await db[EDU_REQUESTED_SITES_COLLECTION].delete_one({"domain": d})
    except Exception as e:
        logger.warning(f"[edu_browser] requested-sites delete failed: {e}")
        raise HTTPException(status_code=500, detail="storage_failed")
    return {"ok": True, "domain": d}


__all__ = ["router"]
