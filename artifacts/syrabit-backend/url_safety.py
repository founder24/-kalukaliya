"""Syrabit.ai — shared URL / host safety primitives.

Every outbound HTTP fetch in the backend (the educational reader, the
grounded-answer RAG pipeline, the generic web-content scraper) must run
through the same SSRF defences:

* Reject IP literals that land in private / loopback / link-local /
  reserved / multicast / unspecified space.
* Reject hostnames on the operator / hard-deny allowlist.
* Re-resolve every FQDN so a public-looking name that A/AAAA-records
  into private space is rejected *before* the first TCP connect.
* Follow redirects manually and re-run the full check on every hop —
  httpx's ``follow_redirects=True`` is a known SSRF sink because a
  hostile upstream can 302 us into an internal IP.

Prior to Task #616 these helpers lived as private (`_`-prefixed)
functions on ``edu_reader`` and were imported across module boundaries
by reaching into another module's "private" namespace. That made the
coupling invisible and the helpers awkward to unit-test in isolation.
This module is the single, public home for the rules; ``edu_reader``,
``rag``, and ``web_content`` all import from here.

Public API
----------
* ``validate_host_for_ssrf(host)`` → ``(ok, reason)``
* ``safe_get_with_redirects(client, url, *, max_hops=5, headers=None)``
  → ``(response, final_url, reason)``
* ``resolves_to_public_ip(host)`` → ``(ok, reason)`` (exposed so the
  reader's robots-fetch path can reuse the DNS guard without
  re-implementing it).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Awaitable, Callable
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx


__all__ = [
    "validate_host_for_ssrf",
    "safe_get_with_redirects",
    "resolves_to_public_ip",
]


async def resolves_to_public_ip(host: str) -> tuple[bool, str]:
    """Resolve ``host`` and ensure no A/AAAA record points at private space.

    Returns ``(ok, reason)``. ``reason`` is ``"ok"`` on success or one
    of ``"dns_failed"`` / ``"private_ip"``. Callers should fail closed
    on anything other than ``"ok"`` to defend against DNS-based SSRF
    where a public-looking FQDN resolves to a private IP.
    """
    import ipaddress as _ipa
    import socket as _socket
    try:
        infos = await asyncio.get_event_loop().getaddrinfo(
            host, None, type=_socket.SOCK_STREAM,
        )
    except Exception:
        return False, "dns_failed"
    if not infos:
        return False, "dns_failed"
    for _fam, _type, _proto, _canon, sockaddr in infos:
        addr = sockaddr[0]
        try:
            ip = _ipa.ip_address(addr)
        except ValueError:
            return False, "dns_failed"
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, "private_ip"
    return True, "ok"


async def validate_host_for_ssrf(host: str) -> tuple[bool, str]:
    """Run the full SSRF-safety check on a single hostname / IP literal.

    Mirrors the per-hop checks used by ``probe_site_safety`` so that
    the public reader and grounded-answer fetches are protected by the
    same rules the educator self-approval probe enforces.

    Returns ``(ok, reason)``. ``reason`` is ``"ok"`` on success, or one
    of ``no_host`` / ``private_ip`` / ``hard_denied`` /
    ``operator_blocked`` / ``dns_failed`` on rejection.
    """
    # Imported lazily because edu_allowlist reaches back into backend
    # modules; keeping the import at call-time avoids bootstrap cycles.
    from edu_allowlist import is_domain_hard_blocked
    h = (host or "").lower().strip()
    if not h:
        return False, "no_host"
    is_ip_literal = False
    try:
        import ipaddress as _ipa
        ip = _ipa.ip_address(h)
        is_ip_literal = True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, "private_ip"
    except ValueError:
        if h in {"localhost", "0.0.0.0"} or h.endswith(".local") or h.endswith(".internal"):
            return False, "private_ip"
    blocked, why = await is_domain_hard_blocked(h)
    if blocked:
        return False, why
    if not is_ip_literal:
        dns_ok, dns_why = await resolves_to_public_ip(h)
        if not dns_ok:
            return False, dns_why
    return True, "ok"


async def safe_get_with_redirects(
    client: "httpx.AsyncClient",
    url: str,
    *,
    max_hops: int = 5,
    headers: dict | None = None,
    hop_validator: Callable[[str], Awaitable[bool]] | None = None,
) -> tuple["httpx.Response | None", str, str]:
    """GET ``url`` with manual redirect handling and per-hop SSRF re-checks.

    httpx's ``follow_redirects=True`` is a known SSRF sink: a hostile
    upstream can issue a 302 pointing at an internal IP and the client
    will obediently follow. This helper disables auto-redirects, walks
    up to ``max_hops`` hops manually, and re-runs the full host
    validation (private-IP, hard-deny, operator-block, DNS-resolution)
    on every Location target before issuing the next request.

    ``hop_validator``, when provided, is awaited with each redirect
    destination URL *before* the next GET is issued. If it returns
    ``False`` the chain is aborted and ``"hop_policy_rejected"`` is
    returned as the reason without issuing the blocked request. The
    initial ``url`` and each redirect destination are validated for
    scheme and host SSRF safety before any outbound GET is issued.

    Returns ``(response, final_url, reason)`` where ``reason`` is
    ``"ok"`` on success or an error code (``bad_redirect_scheme`` /
    ``too_many_redirects`` / ``no_location`` / ``hop_policy_rejected``
    / a value forwarded from ``validate_host_for_ssrf`` prefixed with
    ``redirect_``) on failure. On failure ``response`` may still hold
    the last successful hop but callers should treat the request as
    rejected.
    """
    def _normalize_and_validate_http_url(raw_url: str) -> tuple[bool, str, str]:
        p = urlparse(raw_url)
        if p.scheme not in ("http", "https"):
            return False, raw_url, "bad_redirect_scheme"
        if not p.hostname:
            return False, raw_url, "bad_redirect_url"
        # Reject credential-bearing URLs (userinfo in netloc).
        if p.username is not None or p.password is not None:
            return False, raw_url, "bad_redirect_url"
        # Keep behavior predictable: do not allow explicit non-default ports.
        if p.port is not None:
            if (p.scheme == "http" and p.port != 80) or (p.scheme == "https" and p.port != 443):
                return False, raw_url, "bad_redirect_url"
        return True, p.geturl(), "ok"

    current = url
    resp: "httpx.Response | None" = None
    for _ in range(max_hops):
        ok_url, normalized_current, url_reason = _normalize_and_validate_http_url(current)
        if not ok_url:
            return resp, current, url_reason
        current = normalized_current

        p0 = urlparse(current)
        ok0, why0 = await validate_host_for_ssrf((p0.hostname or "").lower())
        if not ok0:
            return resp, current, f"redirect_{why0}"
        resp = await client.get(current, headers=headers) if headers else await client.get(current)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp, current, "ok"
        loc = resp.headers.get("location")
        if not loc:
            return resp, current, "no_location"
        nxt = urljoin(current, loc)
        ok_nxt_url, normalized_nxt, nxt_reason = _normalize_and_validate_http_url(nxt)
        if not ok_nxt_url:
            return resp, current, nxt_reason
        nxt = normalized_nxt

        p = urlparse(nxt)
        ok, why = await validate_host_for_ssrf((p.hostname or "").lower())
        if not ok:
            return resp, current, f"redirect_{why}"
        if hop_validator and not await hop_validator(nxt):
            return resp, nxt, "hop_policy_rejected"
        current = nxt
    return resp, current, "too_many_redirects"
