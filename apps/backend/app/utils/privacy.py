"""Privacy-safe identifiers for operational logs."""

from __future__ import annotations

import hashlib


def redact_email(email: str | None) -> str:
    """Return a stable, non-reversible fingerprint for an email address."""
    value = (email or "").strip().lower()
    if not value:
        return "email:missing"
    return f"email:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def redact_identifier(identifier: str | None) -> str:
    """Return a stable, non-reversible fingerprint for an account identifier."""
    value = str(identifier or "").strip()
    if not value:
        return "id:missing"
    return f"id:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"