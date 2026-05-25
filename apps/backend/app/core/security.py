"""
Security Utilities: Input Sanitization, URL Validation, SSRF Protection
"""

import re
import unicodedata
from urllib.parse import urlparse
import ipaddress
from typing import Optional


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
    ]

    for pattern in injection_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            raise ValueError("Message contains disallowed content")

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
        import asyncio

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
