"""Cloudflare Cache Rules — per-route-group TTL/Cache-Tag policy and
the ``apply_rules_via_api`` helper that pushes it to the Rulesets API.
Gated by ``CF_TIERED_CACHE_ON``; no-op when the flag is off."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheRule:
    family: str
    matcher: str
    ttl_s: int
    swr_s: int
    cache_tag_template: str
    purge_keys: tuple[str, ...]


def _flag_on() -> bool:
    try:
        from config import CF_TIERED_CACHE_ON
        return bool(CF_TIERED_CACHE_ON)
    except Exception:
        return False


# The contract — keep aligned with the Cache-Tag emit in seo_engine.py
# and the purge call in routes/admin_content._cache_tags_for_reason.
RULES: tuple[CacheRule, ...] = (
    CacheRule(
        family="ssr_html",
        matcher='(http.request.uri.path matches "^/(ahsec|seba|cbse|nios|icse)/[^/]+/[^/]+(/[^/]+){0,2}$") or (http.request.uri.path matches "^/as/(ahsec|seba|cbse|nios|icse)/")',
        ttl_s=300,
        swr_s=86_400,
        cache_tag_template="syrabit-html syrabit-subject-{subject} syrabit-chapter-{chapter} syrabit-topic-{topic}",
        purge_keys=("subject", "chapter", "topic"),
    ),
    CacheRule(
        family="static_assets",
        matcher='http.request.uri.path matches "^/(assets|icons|fonts)/"',
        ttl_s=31_536_000,
        swr_s=0,
        cache_tag_template="syrabit-static",
        purge_keys=("static",),
    ),
    CacheRule(
        family="images",
        matcher='http.request.uri.path matches "\\.(png|jpe?g|webp|gif|svg|ico|avif)$"',
        ttl_s=2_592_000,
        swr_s=86_400,
        cache_tag_template="syrabit-img",
        purge_keys=("img",),
    ),
    CacheRule(
        family="sitemap",
        matcher='http.request.uri.path matches "^/sitemap.*\\.xml$"',
        ttl_s=3_600,
        swr_s=86_400,
        cache_tag_template="syrabit-sitemap",
        purge_keys=("sitemap",),
    ),
    CacheRule(
        family="robots",
        matcher='http.request.uri.path eq "/robots.txt"',
        ttl_s=86_400,
        swr_s=0,
        cache_tag_template="syrabit-robots",
        purge_keys=("robots",),
    ),
    CacheRule(
        family="public_json",
        matcher='http.request.uri.path matches "^/api/seo/(routes|sitemap|breadcrumbs)$"',
        ttl_s=600,
        swr_s=3_600,
        cache_tag_template="syrabit-public-json",
        purge_keys=("public_json",),
    ),
)


def policy_payload() -> dict[str, Any]:
    """Return the policy contract as a dict — used by the admin
    health endpoint and the rules API push."""
    return {
        "enabled": _flag_on(),
        "rules": [asdict(r) for r in RULES],
        "rule_count": len(RULES),
    }


def rule_for_path(path: str) -> CacheRule | None:
    """Cheap classifier for tests / observability — returns the first
    rule whose family the path obviously belongs to. This is a string
    heuristic, not a CF expression evaluator (which lives in the edge)."""
    p = path or ""
    if p.startswith(("/assets/", "/icons/", "/fonts/")):
        return next(r for r in RULES if r.family == "static_assets")
    if p == "/robots.txt":
        return next(r for r in RULES if r.family == "robots")
    if p.startswith("/sitemap"):
        return next(r for r in RULES if r.family == "sitemap")
    if p.startswith("/api/seo/"):
        return next(r for r in RULES if r.family == "public_json")
    if any(p.endswith("." + ext) for ext in ("png", "jpg", "jpeg", "webp", "gif", "svg", "ico", "avif")):
        return next(r for r in RULES if r.family == "images")
    # Best-effort SSR HTML match — leading-slash paths with at least
    # 2 segments and a known board prefix.
    segs = [s for s in p.split("/") if s]
    if segs and segs[0] in {"ahsec", "seba", "cbse", "nios", "icse", "as"}:
        return next(r for r in RULES if r.family == "ssr_html")
    return None


async def apply_rules_via_api() -> dict[str, Any]:
    """Push the contract above to the Cloudflare Rulesets API.

    No-op when ``CF_TIERED_CACHE_ON`` is off so the flag-flip
    rollback works. Returns a structured result the admin endpoint
    surfaces verbatim.
    """
    if not _flag_on():
        return {"applied": False, "reason": "flag_off", "rule_count": len(RULES)}

    zone_id = os.environ.get("CF_ZONE_ID", "").strip()
    api_token = os.environ.get("CF_API_TOKEN", "").strip()
    if not zone_id or not api_token:
        return {
            "applied": False,
            "reason": "missing_credentials",
            "rule_count": len(RULES),
        }

    payload = {
        "rules": [
            {
                "expression": r.matcher,
                "action": "set_cache_settings",
                "action_parameters": {
                    "edge_ttl": {
                        "mode": "override_origin",
                        "default": r.ttl_s,
                    },
                    "browser_ttl": {"mode": "respect_origin"},
                    "cache": True,
                    "serve_stale": {"disable_stale_while_updating": False},
                },
                "description": f"Task #386 — {r.family}",
            }
            for r in RULES
        ],
    }

    try:
        import httpx
        url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/rulesets/phases/http_request_cache_settings/entrypoint"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.put(
                url,
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 400:
            return {
                "applied": False,
                "reason": f"http_{resp.status_code}",
                "body": resp.text[:500],
                "rule_count": len(RULES),
            }
        return {"applied": True, "rule_count": len(RULES)}
    except Exception as exc:
        logger.warning("cf_cache_rules.apply_rules_via_api failed: %s", exc)
        return {
            "applied": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "rule_count": len(RULES),
        }
