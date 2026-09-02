"""
Security Utilities: Input Sanitization, URL Validation, SSRF Protection
"""

import re
import ssl
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse
import ipaddress
from typing import Awaitable, Callable, Optional

import httpx


def _log_injection_attempt(text: str, pattern: str) -> None:
    """Log prompt injection attempt with structured fields. Never logs full message."""
    import logging

    logger = logging.getLogger(__name__)
    logger.warning(
        "prompt_injection_detected",
        extra={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sanitized_snippet": text[:100],
            "matched_pattern": pattern,
            "severity": "warning",
        },
    )


def sanitize_user_input(text: str) -> str:
    """
    Sanitize user input to prevent prompt injection and DoS attacks.

    - Rejects messages containing prompt injection markers
    - Limits length to prevent buffer overflow/DoS
    - Removes control characters
    """
    if not text:
        return ""

    # NFKC Unicode normalization
    text = unicodedata.normalize("NFKC", text)

    # Strip zero-width characters
    text = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", text)

    # Strip invisible formatting characters that could be used for
    # homoglyph/confusable attacks. Includes soft hyphen, combining grapheme
    # joiner, line/paragraph separators, variation selectors, hangul fillers,
    # and khmer invisible characters.
    text = re.sub(
        r"[\u00ad\u034f\u2028\u2029\ufe00-\ufe0f\u115f\u1160\u17b4\u17b5]",
        "",
        text,
    )
    # Strip supplementary variation selectors (U+E0100-U+E01EF)
    text = re.sub(r"[\U000E0100-\U000E01EF]", "", text)

    # Reject potential prompt injection markers
    injection_patterns = [
        r"Ignore previous instructions",
        r"System:",
        r"You are now",
        r"Forget all",
        r"BEGINNING OF CONVERSATION",
        r"<\|im_end\|>",
        r"### Instruction:",
        r"\[INST\]",
        r"<\|system\|>",
        r"<\|user\|>",
        r"<\|assistant\|>",
        r"Human:",
        r"Assistant:",
        r"<<SYS>>",
        # DAN/jailbreak variants
        r"Do Anything Now",
        r"DAN mode",
        r"jailbreak",
        r"(?:enter|activate|enable) developer mode",
        r"Act as if you",
        r"Pretend you are",
        r"From now on you",
        # HTML/script injection attempts
        r"<script",
        r"javascript:",
        r"onerror\s*=",
        r"onload\s*=",
    ]

    for pattern in injection_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            _log_injection_attempt(text, pattern)
            raise ValueError("Message contains disallowed content")

    # Remove control characters except newlines and tabs
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)

    # Limit length to match ChatRequest model validator (2000 chars max).
    max_length = 2000
    if len(text) > max_length:
        text = text[:max_length]

    return text.strip()


def _normalise_hostname(hostname: str) -> Optional[str]:
    """Return a DNS-safe lower-case hostname, or None for malformed input."""
    try:
        host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, AttributeError):
        return None
    if not host or any(char in host for char in "\x00\r\n\\/%"):
        return None
    return host


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject every non-public address, including cloud metadata ranges."""
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in ipaddress.ip_network("169.254.169.254/32")
        or ip in ipaddress.ip_network("fd00:ec2::254/128")
    )


async def resolve_public_ip_addresses(
    hostname: str,
    port: int,
    *,
    timeout: float = 3.0,
) -> tuple[str, ...]:
    """Resolve a host and return addresses only when every answer is public."""
    import asyncio

    normalised = _normalise_hostname(hostname)
    if not normalised:
        return ()

    try:
        literal_ip = ipaddress.ip_address(normalised)
        addresses = [str(literal_ip)]
    except ValueError:
        try:
            loop = asyncio.get_running_loop()
            answers = await asyncio.wait_for(
                loop.getaddrinfo(normalised, port),
                timeout=timeout,
            )
            addresses = [answer[4][0] for answer in answers]
        except (asyncio.TimeoutError, OSError, ValueError):
            return ()

    unique_addresses: list[str] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return ()
        if _is_blocked_ip(ip):
            return ()
        canonical = str(ip)
        if canonical not in unique_addresses:
            unique_addresses.append(canonical)
    return tuple(unique_addresses)


async def is_safe_url(
    url: str,
    allowed_schemes: Optional[list[str]] = None,
    allowed_hosts: Optional[list[str] | set[str] | tuple[str, ...]] = None,
) -> bool:
    """
    Validate URL to prevent SSRF attacks.

    Checks:
    - Scheme must be http or https
    - No userinfo (user:pass@domain)
    - Not pointing to private/internal/reserved IPs
    - Not localhost, link-local, or cloud metadata addresses
    - If supplied, hostname must exactly match the allowlist

    Args:
        url: The URL to validate
        allowed_schemes: List of allowed schemes (default: ['http', 'https'])
        allowed_hosts: Optional exact hostname allowlist

    Returns:
        True if URL is safe, False otherwise
    """
    if allowed_schemes is None:
        allowed_schemes = ["http", "https"]

    try:
        if not isinstance(url, str) or not url or url != url.strip():
            return False
        if any(ord(char) < 0x20 or char == "\x7f" for char in url):
            return False

        parsed = urlparse(url)

        # Check scheme
        schemes = {scheme.lower() for scheme in allowed_schemes}
        if parsed.scheme.lower() not in schemes:
            return False

        # Check for userinfo (user:pass@domain)
        if parsed.username or parsed.password:
            return False

        # Must have a hostname
        if not parsed.hostname:
            return False
        hostname = _normalise_hostname(parsed.hostname)
        if not hostname:
            return False

        if allowed_hosts is not None:
            trusted_hosts = {
                normalised
                for host in allowed_hosts
                if (normalised := _normalise_hostname(host))
            }
            if hostname not in trusted_hosts:
                return False

        # Accessing .port validates malformed ports. HTTP(S) targets must not
        # be redirected to an alternate service port.
        try:
            port = parsed.port
        except ValueError:
            return False
        expected_port = 443 if parsed.scheme.lower() == "https" else 80
        if port is not None and port != expected_port:
            return False

        return bool(await resolve_public_ip_addresses(hostname, port or expected_port))

    except Exception:
        return False


class PinnedNetworkBackend:
    """Connect only to DNS answers that were validated as public."""

    def __init__(self, hostname: str, addresses: tuple[str, ...]):
        import httpcore

        self._hostname = hostname.rstrip(".").lower()
        self._addresses = addresses
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        import httpcore

        if host.rstrip(".").lower() != self._hostname:
            raise httpcore.ConnectError("Outbound host changed after validation")
        last_error: Exception | None = None
        for address in self._addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise httpcore.ConnectError("No validated address available")


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """An HTTPX transport that connects to a previously validated IP list."""

    def __init__(self, hostname: str, addresses: tuple[str, ...]):
        import httpcore

        super().__init__(verify=True, trust_env=False, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PinnedNetworkBackend(hostname, addresses),
            retries=0,
        )


async def fetch_url_safely(
    url: str,
    *,
    allowed_schemes: Optional[list[str]] = None,
    allowed_hosts: Optional[list[str] | set[str] | tuple[str, ...]] = None,
    max_redirects: int = 3,
    timeout: float = 30.0,
    url_validator: Optional[Callable[[str], Awaitable[bool]]] = None,
) -> httpx.Response:
    """Fetch a URL after validating and pinning every outbound connection.

    URL validation and the connection use the same resolved public addresses,
    preventing DNS rebinding between the security check and the socket
    connection. Redirects are never followed by HTTPX: each destination is
    independently validated, resolved, and pinned before it is requested.

    ``url_validator`` is an optional feature-specific asynchronous check. It
    runs for the initial URL and every redirect, after the generic SSRF
    checks, so callers can enforce rules such as an owned storage path.

    Raises:
        ValueError: If the URL, its resolved addresses, or a redirect is unsafe.
        httpx.HTTPError: If the validated request fails or returns an error
            status.
    """
    from urllib.parse import urljoin

    if max_redirects < 0:
        raise ValueError("max_redirects must not be negative")

    current_url = url
    for redirect_count in range(max_redirects + 1):
        if not await is_safe_url(
            current_url,
            allowed_schemes=allowed_schemes,
            allowed_hosts=allowed_hosts,
        ):
            raise ValueError("Outbound URL failed SSRF validation")
        if url_validator is not None and not await url_validator(current_url):
            raise ValueError("Outbound URL failed feature-specific validation")

        parsed = urlparse(current_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Outbound URL has an invalid port") from exc
        expected_port = 443 if parsed.scheme.lower() == "https" else 80
        addresses = await resolve_public_ip_addresses(
            parsed.hostname or "",
            port or expected_port,
        )
        if not addresses:
            raise ValueError("Outbound URL did not resolve to a public address")

        transport = PinnedAsyncHTTPTransport(parsed.hostname or "", addresses)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.get(current_url)

        if response.status_code not in {301, 302, 303, 307, 308}:
            response.raise_for_status()
            return response

        if redirect_count == max_redirects:
            raise ValueError("Outbound URL exceeded the redirect limit")
        location = response.headers.get("location")
        if not location:
            raise ValueError("Outbound URL redirect did not include a location")
        current_url = urljoin(current_url, location)

    raise ValueError("Outbound URL exceeded the redirect limit")


# Short name for callers that do not need to distinguish this from other
# safe outbound operations.
safe_fetch = fetch_url_safely


def is_internal_ip(ip_str: str) -> bool:
    """
    Check if an IP address is internal/private.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return _is_blocked_ip(ip)
    except ValueError:
        return True  # If we can't parse it, assume it's unsafe


# Rate limit header constants
RATE_LIMIT_HEADERS = {
    "limit": "X-RateLimit-Limit",
    "remaining": "X-RateLimit-Remaining",
    "reset": "X-RateLimit-Reset",
}
