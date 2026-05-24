"""
Security Utilities: Input Sanitization, URL Validation, SSRF Protection
"""

import asyncio
import logging
import re
import unicodedata
from urllib.parse import urlparse
import ipaddress
from typing import Optional

logger = logging.getLogger(__name__)

# Zero-width characters to strip before pattern matching
_ZERO_WIDTH_CHARS = re.compile(
    "[\u200b\u200c\u200d\ufeff\u00ad]"
)

# Prompt injection patterns (case-insensitive, whitespace-flexible)
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+.*?instructions", re.IGNORECASE),
    re.compile(r"system\s*:", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"forget\s+all", re.IGNORECASE),
    re.compile(r"BEGINNING\s+OF\s+CONVERSATION", re.IGNORECASE),
    re.compile(r"<\|(?:im_end|im_start)\|>", re.IGNORECASE),
    re.compile(r"###\s*(?:Instruction|System|User)\s*:", re.IGNORECASE),
    re.compile(r"ASSISTANT\s*:", re.IGNORECASE),
    re.compile(r"Human\s*:", re.IGNORECASE),
    re.compile(r"<\|(?:system|user|assistant|im_start|im_end)\|>", re.IGNORECASE),
    re.compile(r"DAN\s+mode", re.IGNORECASE),
    re.compile(r"(?:jail|jail)\s*break", re.IGNORECASE),
    re.compile(r"disregard\s+(?:previous|above|prior)", re.IGNORECASE),
    re.compile(r"override\s+.*?instructions", re.IGNORECASE),
]


def sanitize_user_input(text: str) -> str:
    """
    Sanitize user input to prevent prompt injection and DoS attacks.

    - Normalizes Unicode (NFKC) to defeat homoglyph attacks
    - Strips zero-width characters
    - Detects and strips prompt injection markers
    - Uses scoring: if 2+ patterns match, rejects input entirely
    - Limits length to prevent buffer overflow/DoS
    - Removes control characters
    """
    if not text:
        return ""

    # Normalize Unicode to NFKC to defeat homoglyph attacks
    text = unicodedata.normalize("NFKC", text)

    # Strip zero-width characters
    text = _ZERO_WIDTH_CHARS.sub("", text)

    # Count how many distinct injection patterns match
    match_count = 0
    matched_patterns = []
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            match_count += 1
            matched_patterns.append(pattern.pattern)

    # If 2+ patterns match, reject the input entirely
    if match_count >= 2:
        logger.warning(
            "Prompt injection detected: %d patterns matched (%s)",
            match_count,
            ", ".join(matched_patterns[:5]),
        )
        return ""

    # If 1 pattern matches, strip it
    if match_count == 1:
        for pattern in _INJECTION_PATTERNS:
            text = pattern.sub("", text)

    # Remove control characters except newlines and tabs
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)

    # Limit length to prevent DoS (4000 chars should be enough for any query)
    max_length = 4000
    if len(text) > max_length:
        text = text[:max_length]

    return text.strip()


async def is_safe_url(url: str, allowed_schemes: Optional[list[str]] = None) -> bool:
    """
    Validate URL to prevent SSRF attacks.

    Checks:
    - Scheme must be http or https
    - No userinfo (user:pass@domain)
    - Not pointing to private/internal IPs
    - Not localhost or link-local addresses

    Args:
        url: The URL to validate
        allowed_schemes: List of allowed schemes (default: ['http', 'https'])

    Returns:
        True if URL is safe, False otherwise
    """
    if allowed_schemes is None:
        allowed_schemes = ["http", "https"]

    try:
        parsed = urlparse(url)

        # Check scheme
        if parsed.scheme.lower() not in allowed_schemes:
            return False

        # Check for userinfo (user:pass@domain)
        if parsed.username or parsed.password:
            return False

        # Must have a hostname
        if not parsed.hostname:
            return False

        # Resolve hostname to IP and check if it's private
        try:
            loop = asyncio.get_event_loop()
            ip_addresses = await asyncio.wait_for(
                loop.getaddrinfo(parsed.hostname, None),
                timeout=3.0,
            )
            for family, socktype, proto, canonname, sockaddr in ip_addresses:
                ip_str = sockaddr[0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                    # Block private, loopback, link-local, and multicast IPs
                    if (
                        ip.is_private
                        or ip.is_loopback
                        or ip.is_link_local
                        or ip.is_multicast
                    ):
                        return False
                    # Block AWS metadata endpoint specifically
                    if str(ip).startswith("169.254."):
                        return False
                except ValueError:
                    continue
        except asyncio.TimeoutError:
            # DNS resolution timed out - treat as unsafe
            return False
        except OSError:
            # DNS resolution failed - treat as unsafe
            return False

        return True

    except Exception:
        return False


def is_internal_ip(ip_str: str) -> bool:
    """
    Check if an IP address is internal/private.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
    except ValueError:
        return True  # If we can't parse it, assume it's unsafe


# Rate limit header constants
RATE_LIMIT_HEADERS = {
    "limit": "X-RateLimit-Limit",
    "remaining": "X-RateLimit-Remaining",
    "reset": "X-RateLimit-Reset",
}
