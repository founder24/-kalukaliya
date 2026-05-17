"""Syrabit.ai — Safe logging utilities for sensitive data handling.

This module provides redaction helpers to prevent accidental logging of
sensitive information (API keys, tokens, passwords, secrets, etc.) in
application logs. All logging calls that might include user-provided or
credential data should use these helpers.

Usage:
    from utils.safe_logger import safe_dict, mask_api_key
    
    # Redact sensitive fields from dicts/objects before logging
    logger.info("User auth attempt: %s", safe_dict(user_data))
    
    # Mask API keys for logging
    logger.debug("Using key: %s", mask_api_key(api_key))
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Union


# Sensitive field names to redact (case-insensitive matching)
SENSITIVE_FIELD_PATTERNS = [
    r'password',
    r'passwd',
    r'secret',
    r'token',
    r'api_key',
    r'apikey',
    r'access_token',
    r'refresh_token',
    r'auth_token',
    r'bearer',
    r'credential',
    r'private_key',
    r'mongodb_url',
    r'database_url',
    r'connection_string',
    r'jwt_secret',
    r'admin_jwt',
    r'signing_key',
    r'encryption_key',
]

# Compile patterns for performance
_sensitive_regex = re.compile('|'.join(SENSITIVE_FIELD_PATTERNS), re.IGNORECASE)

# Redaction placeholder
REDACTED_VALUE = '[REDACTED]'


def _is_sensitive_key(key: str) -> bool:
    """Check if a key name matches sensitive patterns."""
    return bool(_sensitive_regex.search(key))


def redact_value(value: Any, max_length: int = 8) -> Any:
    """Redact a single value if it appears to be sensitive.
    
    For strings, shows first `max_length` chars followed by [REDACTED].
    For other types, returns REDACTED_VALUE.
    """
    if isinstance(value, str):
        if len(value) <= max_length:
            return REDACTED_VALUE
        return f"{value[:max_length]}...{REDACTED_VALUE}"
    return REDACTED_VALUE


def redact_dict(data: Dict[str, Any], max_length: int = 8) -> Dict[str, Any]:
    """Recursively redact sensitive fields from a dictionary.
    
    Args:
        data: Dictionary to redact
        max_length: Number of characters to show before redaction
        
    Returns:
        New dictionary with sensitive values redacted
    """
    if not isinstance(data, dict):
        return data
    
    result = {}
    for key, value in data.items():
        if _is_sensitive_key(key):
            result[key] = redact_value(value, max_length)
        elif isinstance(value, dict):
            result[key] = redact_dict(value, max_length)
        elif isinstance(value, list):
            result[key] = redact_list(value, max_length)
        else:
            result[key] = value
    return result


def redact_list(data: List[Any], max_length: int = 8) -> List[Any]:
    """Recursively redact sensitive data in a list."""
    result = []
    for item in data:
        if isinstance(item, dict):
            result.append(redact_dict(item, max_length))
        elif isinstance(item, list):
            result.append(redact_list(item, max_length))
        else:
            result.append(item)
    return result


def safe_dict(data: Dict[str, Any], max_length: int = 8) -> Dict[str, Any]:
    """Create a safe-to-log version of a dictionary.
    
    This is the main entry point for logging configuration objects,
    request/response data, etc. that might contain secrets.
    
    Usage:
        logger.info("Processing request: %s", safe_dict(request_data))
    """
    return redact_dict(data, max_length)


def safe_log_data(data: Any, max_length: int = 8) -> Any:
    """Prepare any data structure for safe logging.
    
    Automatically handles dicts, lists, and primitives.
    
    Usage:
        logger.debug("Response: %s", safe_log_data(response))
    """
    if isinstance(data, dict):
        return redact_dict(data, max_length)
    elif isinstance(data, list):
        return redact_list(data, max_length)
    elif isinstance(data, str):
        # Redact long alphanumeric strings that look like keys/tokens
        if len(data) > 20 and re.match(r'^[A-Za-z0-9+/=_-]{20,}$', data):
            return f"{data[:max_length]}...{REDACTED_VALUE}"
        return data
    return data


def mask_api_key(api_key: str, visible_chars: int = 8) -> str:
    """Mask an API key for logging, showing only first few characters.
    
    Usage:
        logger.info("Using API key: %s", mask_api_key(key))
    """
    if not api_key:
        return REDACTED_VALUE
    if len(api_key) <= visible_chars:
        return REDACTED_VALUE
    return f"{api_key[:visible_chars]}...{REDACTED_VALUE}"
