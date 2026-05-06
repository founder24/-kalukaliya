"""Syrabit.ai — Educational reader/fetch service.

Given an allowlisted educational URL, fetch the page, run a lightweight
Readability-style extraction, sanitise the resulting HTML/text, detect
the language, and return clean article content with metadata.

Design goals
------------
* **Safe** — every URL passes through `edu_allowlist.is_allowed_url`
  *and* an SSRF check (no private IPs, http(s) only). robots.txt is
  consulted with a per-host TTL cache before fetching.
* **Cheap on repeat hits** — successful extractions are cached in Redis
  (`edu_reader:<sha256(url)>`) for 24h so the second fetch is sub-100ms.
* **Self-contained** — no extra dependencies beyond what
  `requirements.txt` already pins (`lxml`, `httpx`). The Readability
  algorithm here is a stripped-down implementation: drop scripts/nav/
  footer/aside, score each `<p>` by text density, return the densest
  cluster wrapped in clean HTML.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import httpx

from edu_allowlist import is_allowed_url, log_blocked_request
from deps import redis_client
import web_risk_client
from url_safety import (
    resolves_to_public_ip as _resolves_to_public_ip,
    safe_get_with_redirects as _safe_get_with_redirects,
    validate_host_for_ssrf as _validate_host_for_ssrf,
)

logger = logging.getLogger(__name__)

USER_AGENT = "SyrabitEduReader/1.0 (+https://syrabit.ai/bot)"
READER_CACHE_PREFIX = "edu_reader:"
READER_CACHE_TTL = 24 * 3600
ROBOTS_CACHE_TTL = 6 * 3600

_FETCH_TIMEOUT = 6.0
_MAX_BYTES = 2_000_000  # 2 MB cap
_MAX_TEXT_CHARS = 60_000

_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}

# Per-host concurrency limit (be polite to upstream sites).
_host_locks: dict[str, asyncio.Semaphore] = {}

# In-process metrics — surfaced via get_reader_stats().
_reader_metrics = {
    "fetches_ok": 0,
    "fetches_failed": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "blocked_allowlist": 0,
    "blocked_robots": 0,
    "blocked_too_large": 0,
    "blocked_web_risk": 0,
    "web_risk_disabled_warnings": 0,
}


def _get_host_lock(host: str) -> asyncio.Semaphore:
    sem = _host_locks.get(host)
    if sem is None:
        sem = asyncio.Semaphore(2)
        _host_locks[host] = sem
    return sem


def _cache_key(url: str) -> str:
    return READER_CACHE_PREFIX + hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_get(url: str) -> Optional[dict]:
    if not redis_client:
        return None
    try:
        raw = redis_client.get(_cache_key(url))
        if raw:
            return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except Exception as e:
        logger.debug(f"[edu_reader] cache_get failed: {e}")
    return None


def _cache_set(url: str, payload: dict) -> None:
    if not redis_client:
        return
    try:
        redis_client.set(_cache_key(url), json.dumps(payload, default=str), ex=READER_CACHE_TTL)
    except Exception as e:
        logger.debug(f"[edu_reader] cache_set failed: {e}")


_LANG_TOKENS = {
    "en": {"the", "and", "of", "to", "is", "in", "that", "with", "for"},
    "as": set(),  # script-detected below
    "hi": set(),
}


def _detect_language(text: str) -> str:
    """Cheap Unicode-block based language detector.

    Returns 'as' for Assamese (Bengali script + ৰ ৱ), 'hi' for
    Devanagari, otherwise 'en'. Good enough for telemetry; we don't
    pull in a 10MB dictionary just to label the reader output.
    """
    if not text:
        return "en"
    sample = text[:4000]
    bn = sum(1 for c in sample if "\u0980" <= c <= "\u09FF")
    dev = sum(1 for c in sample if "\u0900" <= c <= "\u097F")
    total = max(1, sum(1 for c in sample if c.isalpha()))
    if bn / total > 0.25:
        # Distinguish Assamese from Bengali via the two letters that only
        # Assamese uses: ৰ (U+09F0) and ৱ (U+09F1).
        if any(c in sample for c in ("\u09F0", "\u09F1")):
            return "as"
        return "bn"
    if dev / total > 0.25:
        return "hi"
    return "en"


# ───────────────────────── robots.txt ─────────────────────────

async def _fetch_robots(host: str, scheme: str) -> RobotFileParser | None:
    """Fetch robots.txt with SSRF-safe redirect handling.

    httpx's `follow_redirects=True` is a common SSRF sink: a hostile
    site can point `/robots.txt` at an internal IP. We disable auto
    redirects and manually follow up to 3 hops, re-validating the
    host against private-IP + hard-deny checks each time.
    """
    from edu_allowlist import is_domain_hard_blocked

    cached = _robots_cache.get(host)
    now = time.time()
    if cached and (now - cached[0]) < ROBOTS_CACHE_TTL:
        return cached[1]
    rp: RobotFileParser | None = RobotFileParser()
    robots_url = f"{scheme}://{host}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            current = robots_url
            resp = None
            for _hop in range(3):
                resp = await client.get(current, headers={"User-Agent": USER_AGENT})
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                loc = resp.headers.get("location")
                if not loc:
                    break
                nxt = urljoin(current, loc)
                p = urlparse(nxt)
                if p.scheme not in ("http", "https"):
                    rp = None
                    break
                nh = (p.hostname or "").lower()
                is_ip_literal = False
                try:
                    import ipaddress as _ipa
                    _ip = _ipa.ip_address(nh)
                    is_ip_literal = True
                    if _ip.is_private or _ip.is_loopback or _ip.is_link_local or _ip.is_reserved:
                        rp = None
                        break
                except ValueError:
                    if nh in {"localhost", "0.0.0.0"} or nh.endswith(".local") or nh.endswith(".internal"):
                        rp = None
                        break
                blocked, _why = await is_domain_hard_blocked(nh)
                if blocked:
                    rp = None
                    break
                # Per-hop DNS guard: reject public-looking redirect hosts
                # that resolve into private/loopback/link-local space.
                if not is_ip_literal:
                    dns_ok, _dns_why = await _resolves_to_public_ip(nh)
                    if not dns_ok:
                        rp = None
                        break
                current = nxt
            if rp is not None and resp is not None and resp.status_code == 200 and resp.text:
                rp.parse(resp.text.splitlines())
            else:
                # Per RFC 9309: when robots is unreachable / 4xx, default
                # is to allow. We mirror that (rp = None → allow).
                rp = None
    except Exception as e:
        logger.debug(f"[edu_reader] robots fetch failed for {host}: {e}")
        rp = None
    _robots_cache[host] = (now, rp)
    return rp


async def _robots_allows(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    rp = await _fetch_robots(host, parsed.scheme or "https")
    if rp is None:
        return True  # treat missing/unreachable robots as allow
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


# ───────────────────────── Readability-lite ─────────────────────────

_BLOCK_TAGS = {"script", "style", "nav", "footer", "header", "aside",
               "form", "noscript", "iframe", "svg", "button"}
_KEEP_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "code", "td", "th"}


def _strip_tree(root) -> None:
    from lxml import etree
    for tag in _BLOCK_TAGS:
        for el in root.iter(tag):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
    # Drop comments
    for el in list(root.iter(etree.Comment)):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)


def _text_density(el) -> int:
    text = "".join(el.itertext()) or ""
    return len(re.sub(r"\s+", " ", text).strip())


def _readability_extract(html: str, base_url: str) -> dict:
    from lxml import html as lxml_html
    if not html or len(html) < 200:
        return {"title": "", "html": "", "text": "", "byline": "", "lead_image": ""}
    try:
        doc = lxml_html.fromstring(html)
    except Exception as e:
        logger.debug(f"[edu_reader] lxml parse failed: {e}")
        return {"title": "", "html": "", "text": "", "byline": "", "lead_image": ""}

    # Title
    title = ""
    t_el = doc.find(".//title")
    if t_el is not None and t_el.text:
        title = re.sub(r"\s+", " ", t_el.text).strip()
    og_title = doc.xpath('//meta[@property="og:title"]/@content')
    if og_title and og_title[0]:
        title = re.sub(r"\s+", " ", str(og_title[0])).strip() or title

    # Byline / author
    byline = ""
    for xp in (
        '//meta[@name="author"]/@content',
        '//meta[@property="article:author"]/@content',
        '//*[@rel="author"]/text()',
    ):
        vals = doc.xpath(xp)
        if vals:
            byline = re.sub(r"\s+", " ", str(vals[0])).strip()[:200]
            if byline:
                break

    # Lead image
    lead_image = ""
    og_img = doc.xpath('//meta[@property="og:image"]/@content')
    if og_img and og_img[0]:
        try:
            lead_image = urljoin(base_url, str(og_img[0]).strip())
        except Exception:
            lead_image = ""

    _strip_tree(doc)

    # Score every <p> by text length; pick the parent that aggregates the
    # highest combined density (classic Readability heuristic, simplified).
    candidates: dict = {}
    for p in doc.iter("p"):
        density = _text_density(p)
        if density < 25:
            continue
        parent = p.getparent()
        if parent is None:
            continue
        candidates[parent] = candidates.get(parent, 0) + density + 25

    if not candidates:
        # Fallback: use <body> directly.
        body = doc.find(".//body")
        winner = body if body is not None else doc
    else:
        winner = max(candidates, key=candidates.get)

    # Build sanitized HTML from winner: keep only safe block tags.
    parts_html: list[str] = []
    parts_text: list[str] = []
    for el in winner.iter():
        tag = (el.tag or "").lower() if isinstance(el.tag, str) else ""
        if tag not in _KEEP_TAGS:
            continue
        text = "".join(el.itertext()) or ""
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if tag in ("h1", "h2", "h3", "h4"):
            parts_html.append(f"<{tag}>{_html_escape(text)}</{tag}>")
            parts_text.append(text)
        elif tag == "li":
            parts_html.append(f"<li>{_html_escape(text)}</li>")
            parts_text.append(f"• {text}")
        elif tag == "pre" or tag == "code":
            parts_html.append(f"<pre><code>{_html_escape(text)}</code></pre>")
            parts_text.append(text)
        else:
            parts_html.append(f"<p>{_html_escape(text)}</p>")
            parts_text.append(text)

    clean_html = "\n".join(parts_html)[:_MAX_TEXT_CHARS * 2]
    plain_text = "\n\n".join(parts_text)[:_MAX_TEXT_CHARS]

    return {
        "title": title,
        "html": clean_html,
        "text": plain_text,
        "byline": byline,
        "lead_image": lead_image,
    }


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


# ───────────────────────── Public API ─────────────────────────

async def fetch_and_extract(
    url: str,
    *,
    actor: str = "",
    ip_hash: str = "",
    bypass_cache: bool = False,
) -> dict:
    """Fetch `url`, extract clean article content, return a structured payload.

    Result shape::

        {"ok": True,
         "url": <final url after redirects>,
         "domain": <host>,
         "title": str, "byline": str, "lead_image": str,
         "html": <sanitised html>, "text": <plain text>,
         "language": "en"|"as"|"hi"|"bn",
         "char_count": int, "word_count": int,
         "fetched_at": <unix ts>, "from_cache": bool,
         "elapsed_ms": int}

    On failure::

        {"ok": False, "error": <code>, "detail": <message>, "url": url}
    """
    t0 = time.perf_counter()

    # Policy checks run BEFORE the cache so that allowlist revocations and
    # robots.txt changes take effect immediately, even for previously cached URLs.
    allowed, reason = await is_allowed_url(url)
    if not allowed:
        _reader_metrics["blocked_allowlist"] += 1
        await log_blocked_request(url, reason, actor=actor, ip_hash=ip_hash)
        return {"ok": False, "error": "not_allowed", "detail": reason, "url": url}

    if not await _robots_allows(url):
        _reader_metrics["blocked_robots"] += 1
        await log_blocked_request(url, "robots_disallow", actor=actor, ip_hash=ip_hash)
        return {"ok": False, "error": "robots_disallow", "detail": "robots.txt forbids this path", "url": url}

    if not bypass_cache:
        cached = _cache_get(url)
        if cached:
            # Re-validate robots.txt for the actual destination when a
            # cached payload was produced by following a redirect. The
            # original URL already passed the robots check above, but the
            # destination path's robots.txt may have changed since caching.
            cached_final = cached.get("_final_url")
            if cached_final and cached_final != url:
                if not await _robots_allows(cached_final):
                    _reader_metrics["blocked_robots"] += 1
                    await log_blocked_request(
                        cached_final, "robots_disallow_cached_redirect",
                        actor=actor, ip_hash=ip_hash,
                    )
                    return {
                        "ok": False,
                        "error": "robots_disallow",
                        "detail": "robots.txt forbids the cached redirect destination",
                        "url": cached_final,
                    }
            _reader_metrics["cache_hits"] += 1
            cached["from_cache"] = True
            cached["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            cached.pop("_final_url", None)
            return cached
        _reader_metrics["cache_misses"] += 1

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    # SSRF: even after `is_allowed_url` accepts the host textually, a
    # public-looking FQDN can resolve to an RFC1918 / loopback / link-
    # local address (cloud metadata, internal services). Resolve here
    # and reject before opening any socket. Mirrors the educator probe.
    host_ok, host_why = await _validate_host_for_ssrf(host)
    if not host_ok:
        _reader_metrics["blocked_allowlist"] += 1
        await log_blocked_request(url, host_why, actor=actor, ip_hash=ip_hash)
        return {"ok": False, "error": "host_blocked", "detail": host_why, "url": url}

    sem = _get_host_lock(host)

    async with sem:
        try:
            # `follow_redirects=False` + `_safe_get_with_redirects` lets
            # us re-validate the host on every hop. httpx's built-in
            # redirect follower would happily walk us into a private IP.
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT,
                follow_redirects=False,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                    "Accept-Language": "en-IN,en;q=0.9",
                },
            ) as client:
                resp, final_url, redirect_reason = await _safe_get_with_redirects(
                    client, url, hop_validator=_robots_allows,
                )
        except httpx.TimeoutException:
            _reader_metrics["fetches_failed"] += 1
            return {"ok": False, "error": "timeout", "detail": f"upstream did not respond within {_FETCH_TIMEOUT}s", "url": url}
        except Exception as e:
            _reader_metrics["fetches_failed"] += 1
            return {"ok": False, "error": "fetch_failed", "detail": str(e)[:200], "url": url}

    if redirect_reason != "ok":
        if redirect_reason == "hop_policy_rejected":
            # A redirect destination was blocked by robots.txt before the
            # GET was issued (enforced inside safe_get_with_redirects via
            # hop_validator=_robots_allows). No outbound request was made.
            _reader_metrics["blocked_robots"] += 1
            await log_blocked_request(
                final_url or url, "robots_disallow_redirect_hop",
                actor=actor, ip_hash=ip_hash,
            )
            return {
                "ok": False,
                "error": "robots_disallow",
                "detail": "robots.txt forbids a redirect destination",
                "url": final_url or url,
            }
        _reader_metrics["blocked_allowlist"] += 1
        await log_blocked_request(url, redirect_reason, actor=actor, ip_hash=ip_hash)
        return {"ok": False, "error": "redirect_not_allowed", "detail": redirect_reason, "url": final_url or url}

    if resp is None:
        _reader_metrics["fetches_failed"] += 1
        return {"ok": False, "error": "fetch_failed", "detail": "no response", "url": url}

    # Re-check the post-redirect URL against the allowlist — defends
    # against open redirects that walk us off an allowlisted host onto
    # one we never approved.
    allowed_final, reason_final = await is_allowed_url(final_url)
    if not allowed_final:
        _reader_metrics["blocked_allowlist"] += 1
        await log_blocked_request(final_url, f"redirect_{reason_final}", actor=actor, ip_hash=ip_hash)
        return {"ok": False, "error": "redirect_not_allowed", "detail": reason_final, "url": final_url}

    # Re-check robots.txt for the final post-redirect URL (Task #34).
    # The pre-fetch robots check (above) only covered the original URL.
    # A redirect from an allowed URL to a path blocked by the destination
    # site's robots.txt would have bypassed policy if we skipped this.
    # Only skip the second check when the URL didn't change (no redirect
    # occurred) to avoid an extra network round-trip on the common path.
    if final_url != url and not await _robots_allows(final_url):
        _reader_metrics["blocked_robots"] += 1
        await log_blocked_request(final_url, "robots_disallow_redirect", actor=actor, ip_hash=ip_hash)
        return {"ok": False, "error": "robots_disallow", "detail": "robots.txt forbids the redirect destination", "url": final_url}

    # Google Web Risk check on the POST-REDIRECT final URL (Task #489).
    # Closes the threat_model.md Information Disclosure rule that
    # publisher-policy + URL safety must be enforced on the page actually
    # fetched, not just the caller-supplied URL. An open-redirect on an
    # allowlisted host that walks to MALWARE/SOCIAL_ENGINEERING/UNWANTED
    # is blocked here, even though the redirect already passed the
    # allowlist + robots re-checks above.
    #
    # Fail-policy:
    #   * "ok"+matched_threats   → BLOCK, log to log_blocked_request, fail-closed.
    #   * "ok"+safe              → continue.
    #   * "disabled"             → fail-OPEN (degraded — same posture as
    #                              before the integration), but bump a
    #                              metric so Sentry shows the missing
    #                              GOOGLE_WEB_RISK_API_KEY.
    #   * "error" / timeout      → fail-OPEN; we do not want a transient
    #                              Web Risk outage to deny educational
    #                              reads on otherwise-allowlisted sites.
    try:
        wr = await web_risk_client.check_uri(final_url)
    except Exception as e:
        wr = {"status": "error", "safe": True, "matched_threats": [], "error": str(e)[:200]}

    if wr.get("status") == "ok" and not wr.get("safe", True):
        threats = ",".join(wr.get("matched_threats") or []) or "unspecified"
        _reader_metrics["blocked_web_risk"] += 1
        await log_blocked_request(
            final_url, f"web_risk_{threats}", actor=actor, ip_hash=ip_hash,
        )
        return {
            "ok": False,
            "error": "web_risk_blocked",
            "detail": threats,
            "url": final_url,
        }
    if wr.get("status") == "disabled":
        _reader_metrics["web_risk_disabled_warnings"] += 1
        logger.warning(
            "[edu_reader] Web Risk check skipped — GOOGLE_WEB_RISK_API_KEY "
            "not configured. Reader is fail-open for url=%s. Threat-model "
            "compliance requires this key in production.", final_url,
        )

    if resp.status_code != 200:
        _reader_metrics["fetches_failed"] += 1
        return {"ok": False, "error": f"http_{resp.status_code}", "detail": f"upstream returned {resp.status_code}", "url": final_url}

    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype and "xml" not in ctype and ctype:
        _reader_metrics["fetches_failed"] += 1
        return {"ok": False, "error": "unsupported_content_type", "detail": ctype[:80], "url": final_url}

    body = resp.content
    if len(body) > _MAX_BYTES:
        _reader_metrics["blocked_too_large"] += 1
        return {"ok": False, "error": "too_large", "detail": f"{len(body)} bytes > {_MAX_BYTES}", "url": final_url}

    try:
        html_text = body.decode(resp.encoding or "utf-8", errors="replace")
    except Exception:
        html_text = body.decode("utf-8", errors="replace")

    extracted = _readability_extract(html_text, base_url=final_url)
    if not extracted["text"] or len(extracted["text"]) < 80:
        _reader_metrics["fetches_failed"] += 1
        return {"ok": False, "error": "extraction_failed", "detail": "no readable content", "url": final_url}

    final_host = (urlparse(final_url).hostname or "").lower()
    payload = {
        "ok": True,
        "url": final_url,
        "domain": final_host,
        "title": extracted["title"],
        "byline": extracted["byline"],
        "lead_image": extracted["lead_image"],
        "html": extracted["html"],
        "text": extracted["text"],
        "language": _detect_language(extracted["text"]),
        "char_count": len(extracted["text"]),
        "word_count": len(extracted["text"].split()),
        "fetched_at": time.time(),
        "from_cache": False,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "_final_url": final_url if final_url != url else None,
    }
    _reader_metrics["fetches_ok"] += 1
    _cache_set(url, payload)
    if final_url != url:
        _cache_set(final_url, payload)
    payload.pop("_final_url", None)
    return payload


def get_reader_stats() -> dict:
    total = _reader_metrics["cache_hits"] + _reader_metrics["cache_misses"]
    hit_rate = (_reader_metrics["cache_hits"] / total * 100) if total else 0.0
    return {**_reader_metrics, "hit_rate_pct": round(hit_rate, 1)}


# ───────────────────────── Educator site-safety probe ─────────────────────────
#
# `probe_site_safety` is used by the educator self-approval flow. It
# performs the same SSRF/robots/fetch guards as `fetch_and_extract`
# but **bypasses the allowlist** (because we are *deciding* whether to
# admit the site) and additionally runs a kid-safe lexical score on
# the extracted text. Hard-deny and private-IP checks still apply.

async def probe_site_safety(domain: str) -> dict:
    """Run an admission safety probe on a candidate domain.

    Returns::

        {"ok": True/False, "url": <probed>, "reason": <code>,
         "robots_ok": bool, "http_status": int|None,
         "kid_safe": bool, "kid_safe_density": float,
         "kid_safe_hits": list[str], "text_chars": int}

    Failure ``reason`` codes mirror the allowlist vocabulary plus
    ``unsafe_content`` when the kid-safe lexicon trips.
    """
    from edu_allowlist import _normalize_domain, is_domain_hard_blocked
    from guardrails.web_safety import score_text_kid_safety

    d = _normalize_domain(domain)
    result: dict = {
        "ok": False, "url": "", "reason": "invalid_domain",
        "robots_ok": False, "http_status": None,
        "kid_safe": False, "kid_safe_density": 0.0,
        "kid_safe_hits": [], "text_chars": 0,
    }
    if not d or "." not in d:
        return result

    # SSRF / private-IP guard — mirror is_allowed_url's checks.
    try:
        import ipaddress
        ip = ipaddress.ip_address(d)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            result["reason"] = "private_ip"
            return result
    except ValueError:
        if d in {"localhost", "0.0.0.0"} or d.endswith(".local") or d.endswith(".internal"):
            result["reason"] = "private_ip"
            return result

    blocked, why = await is_domain_hard_blocked(d)
    if blocked:
        result["reason"] = why
        return result

    # DNS-resolution SSRF guard: a public-looking FQDN could resolve
    # to an RFC1918 / loopback / link-local address (e.g. 169.254.x
    # metadata endpoints) and bypass the textual host checks. Resolve
    # here and reject if any returned IP is non-public.
    dns_ok, dns_reason = await _resolves_to_public_ip(d)
    if not dns_ok:
        result["reason"] = dns_reason
        return result

    url = f"https://{d}/"
    result["url"] = url

    robots_ok = await _robots_allows(url)
    result["robots_ok"] = robots_ok
    if not robots_ok:
        result["reason"] = "robots_disallow"
        return result

    # Manual redirect handling so we can re-validate each hop against
    # SSRF + hard-deny checks. Without this, `follow_redirects=True`
    # would silently carry us to a private IP or denied host.
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9",
                "Accept-Language": "en-IN,en;q=0.9",
            },
        ) as client:
            current_url = url
            resp = None
            for _hop in range(5):
                resp = await client.get(current_url)
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                loc = resp.headers.get("location")
                if not loc:
                    break
                next_url = urljoin(current_url, loc)
                parsed_next = urlparse(next_url)
                if parsed_next.scheme not in ("http", "https"):
                    result["reason"] = "bad_redirect_scheme"
                    return result
                next_host = (parsed_next.hostname or "").lower()
                # Re-run SSRF + hard-deny guards on the new host.
                next_is_ip_literal = False
                try:
                    import ipaddress as _ipa
                    _ip = _ipa.ip_address(next_host)
                    next_is_ip_literal = True
                    if _ip.is_private or _ip.is_loopback or _ip.is_link_local or _ip.is_reserved:
                        result["reason"] = "redirect_private_ip"
                        return result
                except ValueError:
                    if next_host in {"localhost", "0.0.0.0"} or next_host.endswith(".local") or next_host.endswith(".internal"):
                        result["reason"] = "redirect_private_ip"
                        return result
                r_blocked, r_why = await is_domain_hard_blocked(next_host)
                if r_blocked:
                    result["reason"] = f"redirect_{r_why}"
                    return result
                # Per-hop DNS-resolution SSRF guard for public-looking hosts.
                if not next_is_ip_literal:
                    dns_ok_hop, dns_why_hop = await _resolves_to_public_ip(next_host)
                    if not dns_ok_hop:
                        result["reason"] = f"redirect_{dns_why_hop}"
                        return result
                current_url = next_url
            else:
                result["reason"] = "too_many_redirects"
                return result
            result["url"] = current_url
    except httpx.TimeoutException:
        result["reason"] = "timeout"
        return result
    except Exception as e:
        result["reason"] = f"fetch_failed:{str(e)[:80]}"
        return result

    if resp is None:
        result["reason"] = "fetch_failed:no_response"
        return result

    result["http_status"] = resp.status_code
    if resp.status_code != 200:
        result["reason"] = f"http_{resp.status_code}"
        return result

    ctype = (resp.headers.get("content-type") or "").lower()
    if ctype and "html" not in ctype and "xml" not in ctype:
        result["reason"] = "unsupported_content_type"
        return result

    body = resp.content
    if len(body) > _MAX_BYTES:
        result["reason"] = "too_large"
        return result
    try:
        html_text = body.decode(resp.encoding or "utf-8", errors="replace")
    except Exception:
        html_text = body.decode("utf-8", errors="replace")

    extracted = _readability_extract(html_text, base_url=url)
    text = extracted.get("text") or ""
    result["text_chars"] = len(text)
    if len(text) < 80:
        result["reason"] = "extraction_failed"
        return result

    safe, density, hits = score_text_kid_safety(text)
    result["kid_safe"] = bool(safe)
    result["kid_safe_density"] = round(float(density), 3)
    result["kid_safe_hits"] = list(hits)[:10]
    if not safe:
        result["reason"] = "unsafe_content"
        return result

    result["ok"] = True
    result["reason"] = "ok"
    return result


__all__ = [
    "USER_AGENT", "READER_CACHE_PREFIX", "READER_CACHE_TTL",
    "fetch_and_extract", "get_reader_stats", "probe_site_safety",
]
