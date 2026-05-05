"""Syrabit.ai — Educational domain allowlist.

Configurable, hot-editable allowlist of domains that the educational
browser/reader is permitted to fetch from. The allowlist is the union of:

  1. A baked-in default seed (BASE_ALLOWLIST) — covers ~30 well-known
     educational publishers so the system works out of the box.
  2. Operator overrides stored in MongoDB (`db.edu_allowlist`) — admins
     add/remove domains via the admin API without redeploying.

Lookups are O(1) (set membership) and cached for 60 seconds with an
explicit invalidate hook used by the admin write path.

Subdomain matching: a request for `cs.example.edu` is allowed when
`example.edu` is on the allowlist (RFC-3986 host suffix match).
"""
from __future__ import annotations

import time
import logging
from typing import Optional
from urllib.parse import urlparse

from deps import db, is_mongo_available

logger = logging.getLogger(__name__)

# Curated educational seed list. Operators can disable any of these via
# the admin API (a domain marked status="blocked" overrides the default).
BASE_ALLOWLIST: frozenset = frozenset({
    # Reference / encyclopaedic
    "wikipedia.org", "wikibooks.org", "simple.wikipedia.org",
    "britannica.com", "scholarpedia.org",
    # Indian curriculum + boards
    "ncert.nic.in", "cbse.gov.in", "cbseacademic.nic.in",
    "ahsec.assam.gov.in", "sebaonline.org",
    # Education publishers (kid-safe, English)
    "khanacademy.org", "byjus.com", "vedantu.com", "toppr.com",
    "learncbse.in", "shaalaa.com", "doubtnut.com", "askiitians.com",
    "meritnation.com", "studiestoday.com", "tutorialspoint.com",
    "geeksforgeeks.org", "javatpoint.com", "mathsisfun.com",
    "brilliant.org", "unacademy.com", "embibe.com",
    # Primary research / .edu / .ac.in
    "nature.com", "sciencedirect.com", "jstor.org",
    "iitb.ac.in", "iisc.ac.in", "iitm.ac.in", "iitd.ac.in",
    # Standards bodies / open courseware
    "ocw.mit.edu", "openstax.org", "ck12.org",
    # Government education portals
    "diksha.gov.in", "swayam.gov.in", "epathshala.nic.in",
})

# Domains we always reject regardless of operator config — adult content,
# anonymisers, file lockers, anything that would defeat the kid-safe
# guarantee promised by the educational browser.
HARD_DENYLIST: frozenset = frozenset({
    "pornhub.com", "xvideos.com", "redtube.com", "xnxx.com",
    "4chan.org", "8kun.top",
    "torproject.org",  # not adult, but not appropriate for the use case
})

EDU_ALLOWLIST_COLLECTION = "edu_allowlist"
EDU_BLOCKED_REQUESTS_COLLECTION = "edu_blocked_requests"
EDU_REQUESTED_SITES_COLLECTION = "edu_requested_sites"
EDU_USER_STATE_COLLECTION = "edu_user_state"

_OVERRIDES_CACHE: dict[str, set[str]] = {"allow": set(), "block": set()}
_OVERRIDES_CACHE_TS: float = 0.0
_OVERRIDES_TTL = 60.0


def _normalize_host(host: str) -> str:
    """Lowercase and strip leading `www.` / trailing dot."""
    if not host:
        return ""
    h = host.strip().lower().rstrip(".")
    if h.startswith("www."):
        h = h[4:]
    return h


import re as _re

# Canonical hostname / FQDN pattern: labels of [a-z0-9-], no leading/trailing
# hyphen, dot-separated. Max 253 chars. Rejects `@`, `:`, paths, spaces, etc.
_HOSTNAME_RE = _re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def _normalize_domain(domain: str) -> str:
    """Normalise a domain entered via an API.

    Strips scheme/path if a full URL was pasted, lower-cases the host,
    drops `www.`, and then **strictly validates** the result against a
    canonical FQDN shape. Anything containing userinfo (`user@host`),
    path (`/`), port (`:`), whitespace, or not matching the FQDN regex
    is rejected by returning an empty string. This is the choke point
    for SSRF-via-odd-URL attacks in every write path (admin allowlist,
    educator submit).
    """
    raw = (domain or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        try:
            raw = urlparse(raw).hostname or ""
        except Exception:
            return ""
    host = _normalize_host(raw)
    if not host:
        return ""
    # Reject anything that isn't a plain FQDN. We deliberately do not
    # accept IP literals here — callers that need to probe by IP can do
    # so through a separate path that runs explicit SSRF checks.
    if not _HOSTNAME_RE.match(host):
        return ""
    return host


def _host_matches(host: str, domain: str) -> bool:
    """True when `host` equals `domain` or is a subdomain of it."""
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


async def _refresh_overrides_cache(force: bool = False) -> None:
    global _OVERRIDES_CACHE_TS
    now = time.time()
    if not force and (now - _OVERRIDES_CACHE_TS) < _OVERRIDES_TTL:
        return
    try:
        if not await is_mongo_available():
            _OVERRIDES_CACHE_TS = now
            return
    except Exception:
        _OVERRIDES_CACHE_TS = now
        return
    try:
        cursor = db[EDU_ALLOWLIST_COLLECTION].find({}, {"_id": 0, "domain": 1, "status": 1})
        allow: set[str] = set()
        block: set[str] = set()
        async for doc in cursor:
            d = _normalize_domain(doc.get("domain", ""))
            status = (doc.get("status") or "").lower().strip()
            if not d:
                continue
            if status == "blocked":
                block.add(d)
            else:
                allow.add(d)
        _OVERRIDES_CACHE["allow"] = allow
        _OVERRIDES_CACHE["block"] = block
        _OVERRIDES_CACHE_TS = now
    except Exception as e:
        logger.warning(f"[edu_allowlist] cache refresh failed: {e}")
        _OVERRIDES_CACHE_TS = now  # avoid hammering the DB on failure


def invalidate_cache() -> None:
    """Force the next lookup to re-read MongoDB. Called by admin writes."""
    global _OVERRIDES_CACHE_TS
    _OVERRIDES_CACHE_TS = 0.0
    # Task #383 — also invalidate the KV+CacheReserve mirror so a
    # sibling pod doesn't keep serving the stale allowlist after an
    # admin add/remove. Best-effort; never raise.
    try:
        from kv_cache import default_cache as _kv
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_kv().invalidate("edu_allowlist/overrides"))
            else:
                loop.run_until_complete(_kv().invalidate("edu_allowlist/overrides"))
        except RuntimeError:
            pass
    except Exception:
        pass


async def is_allowed_url(url: str) -> tuple[bool, str]:
    """Decide if a URL may be fetched by the reader.

    Returns `(allowed, reason)`. `reason` is one of:
      "ok", "invalid_url", "scheme", "private_ip", "hard_denied",
      "operator_blocked", "not_allowlisted".
    """
    if not url or not isinstance(url, str):
        return False, "invalid_url"
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "invalid_url"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme"
    host = _normalize_host(parsed.hostname or "")
    if not host:
        return False, "invalid_url"

    # Reject loopback / RFC-1918 / link-local hosts so the reader cannot
    # be used as an SSRF primitive against the worker's own network.
    try:
        import ipaddress
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False, "private_ip"
    except ValueError:
        if host in {"localhost", "0.0.0.0"} or host.endswith(".local") or host.endswith(".internal"):
            return False, "private_ip"

    for bad in HARD_DENYLIST:
        if _host_matches(host, bad):
            return False, "hard_denied"

    await _refresh_overrides_cache()
    for bad in _OVERRIDES_CACHE["block"]:
        if _host_matches(host, bad):
            return False, "operator_blocked"

    # Common education-suffix shortcut: any *.edu / *.ac.in / *.gov.in is
    # treated as educational by default.
    edu_suffixes = (".edu", ".edu.in", ".ac.in", ".gov.in", ".gov", ".nic.in")
    if any(host.endswith(s) for s in edu_suffixes):
        return True, "ok"

    for good in BASE_ALLOWLIST:
        if _host_matches(host, good):
            return True, "ok"
    for good in _OVERRIDES_CACHE["allow"]:
        if _host_matches(host, good):
            return True, "ok"

    return False, "not_allowlisted"


async def list_overrides() -> list[dict]:
    """Return all admin-managed override entries (allow + block)."""
    if not await is_mongo_available():
        return []
    try:
        cursor = db[EDU_ALLOWLIST_COLLECTION].find({}, {"_id": 0})
        return [doc async for doc in cursor]
    except Exception as e:
        logger.warning(f"[edu_allowlist] list_overrides failed: {e}")
        return []


VALID_SOURCES = ("admin", "educator", "system")


async def upsert_override(
    domain: str,
    status: str = "allowed",
    note: str = "",
    actor: str = "",
    source: str = "admin",
    extra: Optional[dict] = None,
) -> dict:
    """Add or update a domain override. status ∈ {allowed, blocked}.

    ``source`` records *who* added the override so the admin UI can
    distinguish human admin decisions from educator self-approvals and
    future automation. ``extra`` is an optional dict of provenance
    metadata (e.g. kid-safe score, robots.txt outcome) persisted
    verbatim for audit.
    """
    d = _normalize_domain(domain)
    if not d:
        raise ValueError("invalid_domain")
    status = (status or "allowed").lower().strip()
    if status not in ("allowed", "blocked"):
        raise ValueError("invalid_status")
    src = (source or "admin").lower().strip()
    if src not in VALID_SOURCES:
        src = "admin"
    if not await is_mongo_available():
        raise RuntimeError("mongo_unavailable")
    doc = {
        "domain": d,
        "status": status,
        "note": note[:280] if note else "",
        "actor": actor[:120] if actor else "",
        "source": src,
        "updated_at": time.time(),
    }
    if extra and isinstance(extra, dict):
        # Clamp any stringy values and keep the dict small.
        safe_extra: dict = {}
        for k, v in list(extra.items())[:12]:
            if isinstance(v, str):
                safe_extra[str(k)[:40]] = v[:280]
            elif isinstance(v, (int, float, bool)) or v is None:
                safe_extra[str(k)[:40]] = v
        if safe_extra:
            doc["provenance"] = safe_extra
    await db[EDU_ALLOWLIST_COLLECTION].update_one(
        {"domain": d}, {"$set": doc, "$setOnInsert": {"created_at": time.time()}}, upsert=True,
    )
    invalidate_cache()
    return doc


async def remove_override(domain: str) -> bool:
    d = _normalize_domain(domain)
    if not d or not await is_mongo_available():
        return False
    res = await db[EDU_ALLOWLIST_COLLECTION].delete_one({"domain": d})
    invalidate_cache()
    return bool(res.deleted_count)


async def log_blocked_request(url: str, reason: str, actor: str = "", ip_hash: str = "") -> None:
    """Append a blocked-request entry for admin review.

    Best-effort; failures are swallowed so the reader endpoint never
    fails because of telemetry trouble.
    """
    try:
        if not await is_mongo_available():
            return
    except Exception:
        return
    try:
        host = ""
        try:
            host = _normalize_host(urlparse(url).hostname or "")
        except Exception:
            pass
        await db[EDU_BLOCKED_REQUESTS_COLLECTION].insert_one({
            "url": url[:500],
            "domain": host[:200],
            "reason": reason,
            "actor": actor[:120],
            "ip_hash": ip_hash[:64],
            "ts": time.time(),
        })
    except Exception as e:
        logger.debug(f"[edu_allowlist] log_blocked_request failed: {e}")


async def list_blocked_requests(limit: int = 200) -> list[dict]:
    if not await is_mongo_available():
        return []
    try:
        limit = max(1, min(1000, int(limit)))
        cursor = db[EDU_BLOCKED_REQUESTS_COLLECTION].find({}, {"_id": 0}).sort("ts", -1).limit(limit)
        return [doc async for doc in cursor]
    except Exception as e:
        logger.warning(f"[edu_allowlist] list_blocked_requests failed: {e}")
        return []


def effective_allowlist() -> dict:
    """Snapshot of the currently effective allow/block sets (for admin UI)."""
    return {
        "base": sorted(BASE_ALLOWLIST),
        "operator_allowed": sorted(_OVERRIDES_CACHE["allow"]),
        "operator_blocked": sorted(_OVERRIDES_CACHE["block"]),
        "hard_denied": sorted(HARD_DENYLIST),
        "edu_suffixes": [".edu", ".edu.in", ".ac.in", ".gov.in", ".gov", ".nic.in"],
    }


async def is_domain_hard_blocked(domain: str) -> tuple[bool, str]:
    """Check whether a domain is on the hard-deny list or has an operator
    block override. Used by the educator self-approval flow to refuse
    submissions that an admin has already rejected. Returns (blocked, reason)."""
    d = _normalize_domain(domain)
    if not d:
        return True, "invalid_domain"
    for bad in HARD_DENYLIST:
        if _host_matches(d, bad):
            return True, "hard_denied"
    await _refresh_overrides_cache()
    for bad in _OVERRIDES_CACHE["block"]:
        if _host_matches(d, bad):
            return True, "operator_blocked"
    return False, "ok"


__all__ = [
    "BASE_ALLOWLIST", "HARD_DENYLIST", "VALID_SOURCES",
    "EDU_ALLOWLIST_COLLECTION", "EDU_BLOCKED_REQUESTS_COLLECTION",
    "EDU_REQUESTED_SITES_COLLECTION", "EDU_USER_STATE_COLLECTION",
    "is_allowed_url", "is_domain_hard_blocked", "invalidate_cache",
    "list_overrides", "upsert_override", "remove_override",
    "log_blocked_request", "list_blocked_requests",
    "effective_allowlist", "_normalize_domain", "_host_matches",
]
