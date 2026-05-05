"""Task #386 — SSR success-rate snapshot for ``/admin/cf-health``.

The Pages Functions middleware (`artifacts/syrabit/functions/_middleware.js`)
sets ``X-SSR-Rendered: pages-functions`` on every response it serves
from the SSR path. Crawlers / synthetic checks count those headers
to derive a success rate.

This module is a tiny in-process tally. Each backend response that
*could* have been SSR-served (i.e. the request matched a SEO route)
is recorded as either ``rendered`` (the middleware delivered HTML)
or ``fallback`` (it punted to the SPA shell). The cf-health row
divides the two to surface a "% of SEO requests served by SSR".

In practice the counters are populated by:

  1. The backend ``/html/<path>`` endpoint that the SSR middleware
     calls — every successful render bumps ``rendered``.
  2. Synthetic probes from `routes/admin_cf_health` that fetch a
     known SEO route and look at the ``X-SSR-Rendered`` header.

When ``SSR_ENABLED`` is off the snapshot still renders so the admin
UI shows the disabled state explicitly rather than 404-ing the row.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    from config import SSR_ENABLED
    return bool(SSR_ENABLED)


_lock = threading.Lock()
_counts: dict[str, int] = {"rendered": 0, "fallback": 0, "errors": 0}


def record_render(success: bool, *, error: bool = False) -> None:
    with _lock:
        if error:
            _counts["errors"] = _counts.get("errors", 0) + 1
        elif success:
            _counts["rendered"] = _counts.get("rendered", 0) + 1
        else:
            _counts["fallback"] = _counts.get("fallback", 0) + 1


def reset() -> None:
    with _lock:
        for k in list(_counts.keys()):
            _counts[k] = 0


# Synthetic probe URLs — keep them in env so a smoke check can target
# the public hostname without a code change. Defaults match the
# canonical SEO routes used by Googlebot.
# Task #408 — defaults cover one URL from every SEO family the Pages
# middleware proxies (homepage, about, subject, topic, typed-topic,
# board-scoped chapter, slug-only chapter / topic / subject, PYQ
# year+paper, PYQ shortcut, Assamese variant). The cf-health row
# reports ``probe_pass == probe_total`` only when every family is
# reachable end-to-end (Pages → backend → ``/api/seo/html/...``).
_PROBE_URLS = [
    u.strip() for u in os.environ.get(
        "SSR_PROBE_URLS",
        ",".join([
            "https://syrabit.ai/",
            "https://syrabit.ai/about",
            "https://syrabit.ai/seba/class-10/general/science",
            "https://syrabit.ai/seba/class-10/general/science/light-reflection",
            "https://syrabit.ai/seba/class-10/general/science/light-reflection/notes",
            "https://syrabit.ai/seba/class-10/general/science/chapter/light",
            "https://syrabit.ai/topic/light-reflection",
            "https://syrabit.ai/chapter/light",
            "https://syrabit.ai/subject/science",
            "https://syrabit.ai/pyq/2024/major",
            "https://syrabit.ai/pyq/seba/class-10/general/science",
            "https://syrabit.ai/as/seba/class-10/general/science",
        ]),
    ).split(",") if u.strip()
]


async def _probe_one(url: str, timeout_s: float = 6.0) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "Accept": "text/html",
                "User-Agent": "syrabit-cf-health/1.0 (ssr-probe)",
            })
        return {
            "url": url,
            "status": resp.status_code,
            "ssr": resp.headers.get("x-ssr-rendered") or "",
            "ok": resp.status_code < 400,
        }
    except Exception as exc:
        return {"url": url, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}


async def snapshot(probe: bool = False) -> dict[str, Any]:
    """Counter snapshot + (optional) live probe of the public SSR
    routes. ``probe=True`` is opt-in because each probe is a real
    HTTP round-trip — the cf-health route asks for it on demand.
    """
    with _lock:
        rendered = _counts.get("rendered", 0)
        fallback = _counts.get("fallback", 0)
        errors = _counts.get("errors", 0)
    total = rendered + fallback
    out: dict[str, Any] = {
        "enabled": is_enabled(),
        "rendered": rendered,
        "fallback": fallback,
        "errors": errors,
        "success_rate": (rendered / total) if total else None,
        "probes": None,
    }
    if probe and _PROBE_URLS:
        results = await asyncio.gather(
            *[_probe_one(u) for u in _PROBE_URLS], return_exceptions=True,
        )
        out["probes"] = [
            r if not isinstance(r, Exception) else {"ok": False, "reason": str(r)}
            for r in results
        ]
        # Roll probe outcomes into a simple pass/fail count for the row.
        good = sum(1 for r in out["probes"] if isinstance(r, dict) and r.get("ssr"))
        out["probe_pass"] = good
        out["probe_total"] = len(out["probes"])
    return out
