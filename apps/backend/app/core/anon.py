"""Anonymous user identity resolution.

IP address is the primary auth for anonymous users.

Resolution order (first non-empty wins):
  1. X-Real-IP header (set by reverse proxy / edge worker)
  2. X-Forwarded-For first hop
  3. Direct TCP client IP (request.client.host)
  4. x-anon-id header (browser localStorage ID — fallback only)
  5. 'anon_unknown' (absolute last resort)

The resolved key always matches ^[a-z][a-z0-9_-]{7,63}$ so it passes
the existing anon_id validation pattern in conversations.py and chat.py.
"""

import re
from typing import Optional
from fastapi import Request


# Accepts ip_* keys (from IP normalisation) and the legacy anon_* keys
# (browser-generated localStorage IDs already in the database).
ANON_ID_PATTERN = re.compile(
    r"^(?:ip_[a-z0-9_]{6,62}|anon_[a-f0-9]{32}|anon_unknown)$"
)


def normalize_ip(ip: str) -> str:
    """Normalize an IP address to a valid anon key.

    Examples:
        '127.0.0.1'         → 'ip_127_0_0_1'
        '::1'               → 'ip___1'
        '2001:db8::1'       → 'ip_2001_db8__1'
        '10.0.0.42'         → 'ip_10_0_0_42'
    """
    sanitized = re.sub(r"[^a-z0-9]", "_", ip.strip().lower())[:55]
    return f"ip_{sanitized}"


def resolve_anon_id(request: Optional[Request]) -> str:
    """Return the canonical anonymous identity for a request.

    Only call this when the user is NOT authenticated (user is None).
    """
    if not request:
        return "anon_unknown"

    # 1. Proxy-forwarded real IP
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return normalize_ip(real_ip)

    # 2. X-Forwarded-For first hop
    forwarded_for = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded_for:
        return normalize_ip(forwarded_for)

    # 3. Direct TCP connection IP
    if hasattr(request, "client") and request.client and request.client.host:
        return normalize_ip(request.client.host)

    # 4. Browser-generated localStorage anon ID (legacy fallback)
    anon_id = (request.headers.get("x-anon-id") or "").strip()
    if anon_id and re.match(r"^[a-z][a-z0-9_-]{7,63}$", anon_id):
        return anon_id

    return "anon_unknown"
