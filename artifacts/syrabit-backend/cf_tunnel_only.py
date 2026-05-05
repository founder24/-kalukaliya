"""Task #383 — Cloudflare Tunnel-only origin enforcement middleware.

When ``CF_TUNNEL_ONLY_ON`` is true, every non-open request must arrive
on a TCP connection whose **immediate peer** lies inside
``CF_TUNNEL_ALLOWED_IPS``. The peer address is taken straight from
``scope['client']`` — i.e. the real TCP source of the request — so
the check is non-spoofable: an attacker cannot fake the connection
origin by setting ``cf-connecting-ip`` or ``x-forwarded-for``
headers.

This is the application-layer companion to the cloud-side firewall
rules. The firewall is the primary defence; this middleware exists
so that:

  * the rejection is visible in our access logs (firewall drops are
    silent), and
  * a misconfigured firewall rule cannot turn the origin into an
    open door without us noticing.

Disabled (no enforcement, no overhead) when the flag is false so the
shipping container keeps working unchanged on environments that
haven't yet provisioned the tunnel.

Why the immediate peer
----------------------

There are two valid deployment shapes for "tunnel-only":

  1. **cloudflared on the same host** — the origin is bound to
     ``127.0.0.1`` and ``cloudflared`` runs locally as a sidecar.
     The TCP peer is always loopback, so
     ``CF_TUNNEL_ALLOWED_IPS=127.0.0.0/8,::1/128`` is the correct
     value.
  2. **Cloudflare edge → managed origin (Cloud Run / Railway)** —
     the TCP peer is the egress IP of a Cloudflare edge node, which
     comes from the public list at ``https://www.cloudflare.com/ips/``.
     ``CF_TUNNEL_ALLOWED_IPS`` should be the CF edge CIDRs.

Both shapes share the same enforcement primitive (immediate peer ∈
CIDR set) and neither trusts user-controlled headers, which is why
this middleware only consults ``scope['client']``.

Open paths
----------

A small set of probes is always allowed regardless of the flag,
since they are needed by load balancers / smoke tests that may not
flow through the tunnel:

  * ``/api/healthz``, ``/api/readyz``, ``/api/ready``, ``/health``
  * ``/api/admin/cf-health`` (so the operator can debug a misfire
    from inside the tunnel without a chicken-and-egg lockout)
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send

from config import (
    CF_TUNNEL_ALLOWED_IPS,
    CF_TUNNEL_FAIL_CLOSED_ON_EMPTY,
    CF_TUNNEL_ONLY_ON,
)

logger = logging.getLogger(__name__)


_OPEN_PATHS: tuple[str, ...] = (
    "/api/healthz",
    "/api/readyz",
    "/api/ready",
    "/health",
    "/api/admin/cf-health",
)


def _parse_cidrs(raw: str) -> list[ipaddress._BaseNetwork]:
    out: list[ipaddress._BaseNetwork] = []
    for part in (raw or "").split(","):
        cidr = part.strip()
        if not cidr:
            continue
        try:
            out.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError as exc:
            logger.warning("[cf-tunnel] ignoring invalid CIDR %r: %s",
                           cidr, exc)
    return out


def _ip_in_cidrs(ip_str: str, cidrs: Iterable[ipaddress._BaseNetwork]) -> bool:
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False
    for net in cidrs:
        if ip in net:
            return True
    return False


def _peer_ip_from_scope(scope: Scope) -> str:
    """Return the real TCP source of the request.

    We deliberately do **not** consult ``cf-connecting-ip`` or
    ``x-forwarded-for`` — those are set by user-controlled headers
    and would let a direct caller forge the source, defeating the
    point of the middleware. ``scope['client']`` is populated by the
    ASGI server from the actual socket peer and cannot be spoofed
    over the wire.
    """
    client = scope.get("client")
    if client and isinstance(client, (list, tuple)) and client:
        return str(client[0])
    return ""


class CfTunnelOnlyMiddleware:
    """Reject requests whose TCP peer is not in CF_TUNNEL_ALLOWED_IPS.

    Behaviour matrix:

    +-------------------+-----------------+--------------------+-----------------------------+
    | CF_TUNNEL_ONLY_ON | Allowed CIDRs   | FAIL_CLOSED_ON_EMPTY | Result                    |
    +===================+=================+====================+=============================+
    | false             | any             | any                | passthrough (no enforcement)|
    | true              | non-empty       | any                | enforce                     |
    | true              | empty           | false (default)    | passthrough + warning       |
    | true              | empty           | true               | reject all (fail-closed)    |
    +-------------------+-----------------+--------------------+-----------------------------+

    The "true + empty" case defaults to passthrough+warning because
    locking the entire origin out because the operator forgot to set
    ``CF_TUNNEL_ALLOWED_IPS`` is usually worse than the small
    detection lag. Environments that prefer lockdown over availability
    can set ``CF_TUNNEL_FAIL_CLOSED_ON_EMPTY=1`` to flip the default.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._cidrs = _parse_cidrs(CF_TUNNEL_ALLOWED_IPS)
        self._fail_closed_on_empty = bool(CF_TUNNEL_FAIL_CLOSED_ON_EMPTY)
        if CF_TUNNEL_ONLY_ON and not self._cidrs:
            if self._fail_closed_on_empty:
                logger.warning(
                    "[cf-tunnel] CF_TUNNEL_ONLY_ON=1 with empty "
                    "CF_TUNNEL_ALLOWED_IPS and "
                    "CF_TUNNEL_FAIL_CLOSED_ON_EMPTY=1 — middleware "
                    "will reject ALL non-open requests until CIDRs "
                    "are configured."
                )
            else:
                logger.warning(
                    "[cf-tunnel] CF_TUNNEL_ONLY_ON=1 but "
                    "CF_TUNNEL_ALLOWED_IPS is empty — middleware will "
                    "allow all requests until CIDRs are configured. "
                    "Set CF_TUNNEL_FAIL_CLOSED_ON_EMPTY=1 to lock "
                    "down on misconfiguration instead."
                )

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http" or not CF_TUNNEL_ONLY_ON:
            await self.app(scope, receive, send)
            return
        if not self._cidrs and not self._fail_closed_on_empty:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path == p or path.startswith(p) for p in _OPEN_PATHS):
            await self.app(scope, receive, send)
            return

        peer = _peer_ip_from_scope(scope)
        if self._cidrs and _ip_in_cidrs(peer, self._cidrs):
            await self.app(scope, receive, send)
            return

        if not self._cidrs:
            logger.warning(
                "[cf-tunnel] rejecting request from peer=%r (path=%s) — "
                "fail-closed: CF_TUNNEL_ALLOWED_IPS is empty",
                peer or "<unknown>", path,
            )
        else:
            logger.warning(
                "[cf-tunnel] rejecting request from peer=%r (path=%s) — "
                "not in CF_TUNNEL_ALLOWED_IPS",
                peer or "<unknown>", path,
            )
        from fastapi.responses import JSONResponse
        resp = JSONResponse(
            status_code=403,
            content={
                "detail": "Origin is restricted to Cloudflare Tunnel "
                          "traffic. Direct origin access is not "
                          "permitted.",
                "code": "cf_tunnel_only",
            },
        )
        await resp(scope, receive, send)


__all__ = ["CfTunnelOnlyMiddleware"]
