"""Syrabit.ai — Web content fetching, extraction, caching & rate limiting."""
import asyncio, hashlib, io, logging, time
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse

import httpx
import cachetools

from deps import redis_client
from url_safety import safe_get_with_redirects, validate_host_for_ssrf

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_url_content", "fetch_pdf_from_url", "enrich_search_results",
]

_URL_CONTENT_CACHE: cachetools.TTLCache = cachetools.TTLCache(maxsize=512, ttl=3600)
REDIS_URL_CACHE_PREFIX = "url_content"
REDIS_URL_CACHE_TTL = 3600

_DOMAIN_LAST_FETCH: Dict[str, float] = {}
_DOMAIN_COOLDOWN = 2.0
_MAX_FETCHES_PER_QUERY = 2
_GLOBAL_TIMEOUT_BUDGET = 3.0
_MAX_CONTENT_CHARS = 3000

_MIN_USEFUL_CONTENT_LEN = 80

from internal_user_agents import web_content_headers as _web_content_headers

# Self-identifying SyrabitBot UA + the shared `X-Syrabit-Internal: 1`
# header so Cloudflare bot analytics can tag this as our own internal
# traffic instead of bucketing it as "unknown bot" (Task #820). Source
# of truth: `internal_user_agents.py`.
_FETCH_HEADERS = _web_content_headers()

_playwright_available: Optional[bool] = None


def _url_cache_key(url: str) -> str:
    return hashlib.md5(url.strip().lower().encode(), usedforsecurity=False).hexdigest()


def _get_cached_content(url: str) -> Optional[str]:
    key = _url_cache_key(url)
    cached = _URL_CONTENT_CACHE.get(key)
    if cached is not None:
        return cached
    if redis_client:
        try:
            val = redis_client.get(f"{REDIS_URL_CACHE_PREFIX}:{key}")
            if val is not None:
                text = val if isinstance(val, str) else val.decode()
                _URL_CONTENT_CACHE[key] = text
                return text
        except Exception:
            pass
    return None


def _set_cached_content(url: str, text: str):
    key = _url_cache_key(url)
    _URL_CONTENT_CACHE[key] = text
    if redis_client:
        try:
            redis_client.set(f"{REDIS_URL_CACHE_PREFIX}:{key}", text, ex=REDIS_URL_CACHE_TTL)
        except Exception:
            pass


def _is_safe_scheme(url: str) -> bool:
    """Cheap scheme/host shape check.

    The real SSRF rule set (private-IP, hard-deny, DNS-resolved-to-private,
    per-redirect-hop re-checks) lives in ``url_safety.validate_host_for_ssrf``
    and ``url_safety.safe_get_with_redirects``. Every fetch in this module
    routes through those helpers, so this function only filters obviously-
    malformed URLs to avoid pointless DNS/socket work.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        return bool(parsed.hostname)
    except Exception:
        return False


def _is_pdf_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def _check_playwright_available() -> bool:
    global _playwright_available
    if _playwright_available is not None:
        return _playwright_available
    try:
        import playwright  # noqa: F401
        _playwright_available = True
    except ImportError:
        _playwright_available = False
        logger.info("Playwright not available — JS rendering fallback disabled")
    return _playwright_available


async def _check_domain_cooldown(url: str) -> bool:
    domain = urlparse(url).netloc
    now = time.monotonic()
    last = _DOMAIN_LAST_FETCH.get(domain, 0.0)
    if now - last < _DOMAIN_COOLDOWN:
        return False
    _DOMAIN_LAST_FETCH[domain] = now
    return True


async def _render_js_page(url: str, timeout_ms: int = 6000) -> Optional[str]:
    if not _check_playwright_available():
        return None
    loop = asyncio.get_running_loop()
    def _run():
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.set_extra_http_headers({"User-Agent": _FETCH_HEADERS["User-Agent"]})
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    html = page.content()
                    return html
                finally:
                    browser.close()
        except Exception as e:
            logger.debug(f"JS rendering failed: {e} | {url[:80]}")
            return None
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=timeout_ms / 1000 + 2)
    except asyncio.TimeoutError:
        logger.debug(f"JS rendering timed out: {url[:80]}")
        return None


async def fetch_url_content(url: str, max_chars: int = _MAX_CONTENT_CHARS) -> Optional[str]:
    if not _is_safe_scheme(url):
        logger.debug(f"URL blocked by scheme check: {url[:80]}")
        return None

    cached = _get_cached_content(url)
    if cached is not None:
        logger.debug(f"URL content cache hit: {url[:80]}")
        return cached[:max_chars]

    if not await _check_domain_cooldown(url):
        logger.debug(f"Domain cooldown active, skipping: {url[:80]}")
        return None

    try:
        # Use the hardened reader helper so every redirect hop is
        # re-validated (private-IP, hard-deny, DNS-resolved-to-private)
        # rather than just the first one. Mirrors the protections in
        # `edu_reader.fetch_and_extract`.
        host = urlparse(url).hostname or ""
        host_ok, _why = await validate_host_for_ssrf(host.lower())
        if not host_ok:
            logger.debug(f"URL blocked by SSRF host check: {url[:80]} ({_why})")
            return None
        async with httpx.AsyncClient(
            headers=_FETCH_HEADERS,
            timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0),
            follow_redirects=False,
        ) as client:
            resp, final_url, redirect_reason = await safe_get_with_redirects(client, url)
            if redirect_reason != "ok" or resp is None:
                logger.debug(f"Redirect blocked: {url[:80]} ({redirect_reason})")
                return None
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "application/pdf" in content_type:
                return await _extract_pdf_text(resp.content, url, max_chars)

            html = resp.text
            if not html or len(html) < 100:
                return None

            text = await _extract_text_from_html(html, url)

            if not text or len(text.strip()) < _MIN_USEFUL_CONTENT_LEN:
                logger.debug(f"Static extraction thin ({len(text.strip()) if text else 0} chars), trying JS render: {url[:80]}")
                js_html = await _render_js_page(url)
                if js_html and len(js_html) > len(html):
                    js_text = await _extract_text_from_html(js_html, url)
                    if js_text and len(js_text.strip()) > len((text or "").strip()):
                        text = js_text
                        logger.info(f"JS rendering produced better content: {len(text.strip())} chars | {url[:80]}")

            if text and len(text.strip()) > 50:
                trimmed = text.strip()[:max_chars]
                _set_cached_content(url, trimmed)
                logger.info(f"Fetched URL content: {len(trimmed)} chars from {url[:80]}")
                return trimmed

    except httpx.TimeoutException:
        logger.debug(f"URL fetch timeout: {url[:80]}")
    except httpx.HTTPStatusError as e:
        logger.debug(f"URL fetch HTTP {e.response.status_code}: {url[:80]}")
    except Exception as e:
        logger.debug(f"URL fetch error: {e} | {url[:80]}")
    return None


async def _extract_text_from_html(html: str, url: str) -> Optional[str]:
    loop = asyncio.get_running_loop()
    def _run():
        try:
            import trafilatura
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_precision=True,
                url=url,
            )
            return text
        except Exception as e:
            logger.debug(f"trafilatura extraction failed: {e}")
            return None
    return await loop.run_in_executor(None, _run)


async def _extract_pdf_text(content: bytes, url: str, max_chars: int) -> Optional[str]:
    loop = asyncio.get_running_loop()
    def _run():
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            text_parts = []
            chars = 0
            for page in reader.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
                chars += len(page_text)
                if chars >= max_chars:
                    break
            full = "\n".join(text_parts).strip()
            return full[:max_chars] if full else None
        except Exception as e:
            logger.debug(f"PDF extraction failed: {e} | {url[:80]}")
            return None
    result = await loop.run_in_executor(None, _run)
    if result:
        _set_cached_content(url, result)
        logger.info(f"Extracted PDF content: {len(result)} chars from {url[:80]}")
    return result


async def fetch_pdf_from_url(url: str, max_chars: int = _MAX_CONTENT_CHARS) -> Optional[str]:
    if not _is_safe_scheme(url):
        logger.debug(f"PDF URL blocked by scheme check: {url[:80]}")
        return None

    cached = _get_cached_content(url)
    if cached is not None:
        return cached[:max_chars]

    if not await _check_domain_cooldown(url):
        return None

    try:
        host = urlparse(url).hostname or ""
        host_ok, _why = await validate_host_for_ssrf(host.lower())
        if not host_ok:
            return None
        async with httpx.AsyncClient(
            headers=_FETCH_HEADERS,
            timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0),
            follow_redirects=False,
        ) as client:
            resp, _final_url, redirect_reason = await safe_get_with_redirects(client, url)
            if redirect_reason != "ok" or resp is None:
                return None
            resp.raise_for_status()
            return await _extract_pdf_text(resp.content, url, max_chars)
    except Exception as e:
        logger.debug(f"PDF fetch error: {e} | {url[:80]}")
    return None


async def enrich_search_results(results: list, max_enrich: int = _MAX_FETCHES_PER_QUERY) -> list:
    if not results:
        return results

    seen_domains: Dict[str, int] = {}
    enrichable = []
    for r in results:
        url = r.get("url", "")
        if not (url and url.startswith("http") and _is_safe_scheme(url)):
            continue
        domain = urlparse(url).netloc
        if seen_domains.get(domain, 0) >= 2:
            continue
        seen_domains[domain] = seen_domains.get(domain, 0) + 1
        enrichable.append(r)
        if len(enrichable) >= max_enrich:
            break

    if not enrichable:
        return results

    deadline = time.monotonic() + _GLOBAL_TIMEOUT_BUDGET

    async def _fetch_one(result: dict) -> Tuple[dict, Optional[str]]:
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            return result, None
        url = result["url"]
        try:
            if _is_pdf_url(url):
                content = await asyncio.wait_for(
                    fetch_pdf_from_url(url), timeout=min(remaining, 2.0)
                )
            else:
                content = await asyncio.wait_for(
                    fetch_url_content(url), timeout=min(remaining, 2.0)
                )
            return result, content
        except asyncio.TimeoutError:
            return result, None
        except Exception:
            return result, None

    fetch_tasks = [_fetch_one(r) for r in enrichable]
    fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    enriched_count = 0
    for item in fetched:
        if isinstance(item, Exception):
            continue
        result, content = item
        if content and len(content.strip()) > 50:
            result["full_content"] = content
            result["_enriched"] = True
            enriched_count += 1

    logger.info(f"Web content enrichment: {enriched_count}/{len(enrichable)} URLs enriched")
    return results
