"""
Security Utilities: Input Sanitization, URL Validation, SSRF Protection
"""

import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse
import ipaddress
from typing import Optional


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
