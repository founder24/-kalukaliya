"""Unit tests for the shared SSRF primitives in ``url_safety``.

These cover the three cases the task brief calls out explicitly:

* loopback / RFC1918 / link-local IP literals are rejected before the
  first DNS or HTTP call.
* A public-looking FQDN that resolves to a private IP is rejected by
  the DNS guard (DNS-based SSRF).
* ``safe_get_with_redirects`` re-runs the host validation on every
  redirect hop and aborts when a hop points at private space, so a
  hostile 302 cannot walk us into an internal service.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import httpx
import pytest


_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import url_safety  # noqa: E402


def _install_loop_with_dns(dns_map: dict[str, str]) -> asyncio.AbstractEventLoop:
    """Fresh event loop whose ``getaddrinfo`` returns canned answers.

    Each ``host -> ip`` entry returns a single addrinfo. Hosts missing
    from the map raise ``gaierror`` (NXDOMAIN).
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _fake_getaddrinfo(host, port, *args, **kwargs):
        if host in dns_map:
            ip = dns_map[host]
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
        raise socket.gaierror(f"mocked NXDOMAIN for {host!r}")

    loop.getaddrinfo = _fake_getaddrinfo  # type: ignore[assignment]
    return loop


def _stub_hard_block(monkeypatch, hard: set[str] | None = None) -> None:
    """Neutralise ``edu_allowlist.is_domain_hard_blocked`` for a test.

    The real implementation reaches into the database / Redis, which
    we don't want to exercise from a unit test. Default: nothing is
    hard-blocked. Pass ``hard`` to simulate an operator-denied host.
    """
    async def _fake(host: str):
        if hard and host in hard:
            return True, "operator_blocked"
        return False, ""
    import edu_allowlist
    monkeypatch.setattr(edu_allowlist, "is_domain_hard_blocked", _fake)


# ───────────────────────── validate_host_for_ssrf ─────────────────────────


@pytest.mark.parametrize("host", [
    "127.0.0.1",        # loopback
    "10.0.0.5",         # RFC1918
    "192.168.1.1",      # RFC1918
    "172.16.0.1",       # RFC1918
    "169.254.169.254",  # link-local (EC2 metadata)
    "0.0.0.0",          # unspecified
    "::1",              # IPv6 loopback
    "fc00::1",          # IPv6 ULA (private)
    "fe80::1",          # IPv6 link-local
    "localhost",        # non-IP textual blacklist
    "host.local",       # mDNS
    "svc.internal",     # common internal TLD
])
def test_validate_rejects_private_hosts(monkeypatch, host):
    _stub_hard_block(monkeypatch)
    loop = _install_loop_with_dns({})
    try:
        ok, why = loop.run_until_complete(url_safety.validate_host_for_ssrf(host))
    finally:
        loop.close()
    assert ok is False
    assert why == "private_ip"


def test_validate_rejects_empty_host(monkeypatch):
    _stub_hard_block(monkeypatch)
    loop = asyncio.new_event_loop()
    try:
        ok, why = loop.run_until_complete(url_safety.validate_host_for_ssrf(""))
    finally:
        loop.close()
    assert ok is False
    assert why == "no_host"


def test_validate_accepts_public_fqdn_resolving_public(monkeypatch):
    _stub_hard_block(monkeypatch)
    loop = _install_loop_with_dns({"example.com": "93.184.216.34"})
    try:
        ok, why = loop.run_until_complete(
            url_safety.validate_host_for_ssrf("example.com"),
        )
    finally:
        loop.close()
    assert (ok, why) == (True, "ok")


def test_validate_rejects_public_fqdn_resolving_private(monkeypatch):
    """DNS-based SSRF: public-looking name → RFC1918 A record."""
    _stub_hard_block(monkeypatch)
    loop = _install_loop_with_dns({"sneaky.example": "10.0.0.5"})
    try:
        ok, why = loop.run_until_complete(
            url_safety.validate_host_for_ssrf("sneaky.example"),
        )
    finally:
        loop.close()
    assert ok is False
    assert why == "private_ip"


def test_validate_rejects_dns_failure(monkeypatch):
    _stub_hard_block(monkeypatch)
    loop = _install_loop_with_dns({})  # every lookup NXDOMAINs
    try:
        ok, why = loop.run_until_complete(
            url_safety.validate_host_for_ssrf("nx-domain.example"),
        )
    finally:
        loop.close()
    assert ok is False
    assert why == "dns_failed"


def test_validate_rejects_operator_blocked(monkeypatch):
    _stub_hard_block(monkeypatch, hard={"blocked.example"})
    loop = _install_loop_with_dns({"blocked.example": "93.184.216.34"})
    try:
        ok, why = loop.run_until_complete(
            url_safety.validate_host_for_ssrf("blocked.example"),
        )
    finally:
        loop.close()
    assert ok is False
    assert why == "operator_blocked"


# ───────────────────────── safe_get_with_redirects ─────────────────────────


def _run(coro):
    # Task #284 — fresh loop per call (Python 3.11+ no longer auto-creates
    # a main-thread loop on demand).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_safe_get_returns_response_when_no_redirect(monkeypatch):
    _stub_hard_block(monkeypatch)
    _install_loop_with_dns({"legit.example": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello")

    transport = httpx.MockTransport(handler)
    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await url_safety.safe_get_with_redirects(
                client, "https://legit.example/page",
            )
    resp, final, reason = _run(_go())
    assert reason == "ok"
    assert final == "https://legit.example/page"
    assert resp is not None and resp.status_code == 200


def test_safe_get_rejects_redirect_to_private_ip(monkeypatch):
    """A hostile 302 into 127.0.0.1 must be rejected without issuing
    the second request."""
    _stub_hard_block(monkeypatch)
    _install_loop_with_dns({"legit.example": "93.184.216.34"})

    hops: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if "legit.example" in str(request.url):
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        # If we ever reach here, the SSRF guard failed.
        return httpx.Response(500, text="SHOULD NOT HAPPEN")

    transport = httpx.MockTransport(handler)
    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await url_safety.safe_get_with_redirects(
                client, "https://legit.example/start",
            )
    resp, final, reason = _run(_go())
    assert reason == "redirect_private_ip"
    assert final == "https://legit.example/start"
    assert len(hops) == 1, "should not fetch the private-IP redirect target"


def test_safe_get_rejects_redirect_to_dns_private_host(monkeypatch):
    """Per-hop DNS SSRF: redirect target is a public-looking FQDN but
    its A record is private — the DNS guard must catch it on hop 2."""
    _stub_hard_block(monkeypatch)
    _install_loop_with_dns({
        "legit.example": "93.184.216.34",
        "sneaky.example": "10.0.0.5",
    })

    hops: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if "legit.example" in str(request.url):
            return httpx.Response(302, headers={"location": "https://sneaky.example/"})
        return httpx.Response(500, text="SHOULD NOT HAPPEN")

    transport = httpx.MockTransport(handler)
    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await url_safety.safe_get_with_redirects(
                client, "https://legit.example/start",
            )
    _resp, _final, reason = _run(_go())
    assert reason == "redirect_private_ip"
    assert len(hops) == 1


def test_safe_get_bounds_redirect_chain(monkeypatch):
    """``max_hops`` caps the loop so a redirect bomb cannot spin forever."""
    _stub_hard_block(monkeypatch)
    _install_loop_with_dns({"legit.example": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        # Endless self-redirect — must terminate at max_hops.
        return httpx.Response(302, headers={"location": "https://legit.example/next"})

    transport = httpx.MockTransport(handler)
    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await url_safety.safe_get_with_redirects(
                client, "https://legit.example/start", max_hops=3,
            )
    _resp, _final, reason = _run(_go())
    assert reason == "too_many_redirects"


def test_safe_get_rejects_non_http_redirect(monkeypatch):
    _stub_hard_block(monkeypatch)
    _install_loop_with_dns({"legit.example": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    transport = httpx.MockTransport(handler)
    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await url_safety.safe_get_with_redirects(
                client, "https://legit.example/start",
            )
    _resp, _final, reason = _run(_go())
    assert reason == "bad_redirect_scheme"


# ──────────────────────── edu_reader backward-compat ─────────────────────


def test_edu_reader_aliases_point_at_url_safety():
    """Monkeypatching `edu_reader._resolves_to_public_ip` (the historic
    pattern used by `test_educator_submit_site.py`) keeps working only
    as long as edu_reader re-exports the same callables. Lock that in."""
    import edu_reader
    assert edu_reader._resolves_to_public_ip is url_safety.resolves_to_public_ip
    assert edu_reader._validate_host_for_ssrf is url_safety.validate_host_for_ssrf
    assert edu_reader._safe_get_with_redirects is url_safety.safe_get_with_redirects


# ───────────────────────── extra redirect edge cases ─────────────────────


def test_safe_get_redirect_missing_location(monkeypatch):
    """3xx without a Location header returns the 3xx response as-is
    (no infinite loop, no crash)."""
    _stub_hard_block(monkeypatch)
    _install_loop_with_dns({"legit.example": "93.184.216.34"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)  # no location

    transport = httpx.MockTransport(handler)
    async def _go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await url_safety.safe_get_with_redirects(
                client, "https://legit.example/start",
            )
    resp, _final, reason = _run(_go())
    assert reason == "no_location"
    assert resp is not None and resp.status_code == 302


# ───────────────────────── resolves_to_public_ip ─────────────────────────


def test_resolves_to_public_ip_accepts_public():
    loop = _install_loop_with_dns({"example.com": "93.184.216.34"})
    try:
        ok, why = loop.run_until_complete(url_safety.resolves_to_public_ip("example.com"))
    finally:
        loop.close()
    assert (ok, why) == (True, "ok")


def test_resolves_to_public_ip_rejects_private_record():
    loop = _install_loop_with_dns({"sneaky.example": "10.0.0.5"})
    try:
        ok, why = loop.run_until_complete(url_safety.resolves_to_public_ip("sneaky.example"))
    finally:
        loop.close()
    assert ok is False
    assert why == "private_ip"


def test_resolves_to_public_ip_dns_failure():
    loop = _install_loop_with_dns({})
    try:
        ok, why = loop.run_until_complete(url_safety.resolves_to_public_ip("nx.example"))
    finally:
        loop.close()
    assert ok is False
    assert why == "dns_failed"
