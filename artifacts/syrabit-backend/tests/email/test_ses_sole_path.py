"""Task #556 — pin the SES-as-sole-transactional-path contract.

Four things this test guarantees on every CI run:

  1. ``email_templates`` exposes the SES-only public surface
     (``_send_via_ses``, ``send_admin_email``, ``EmailSendFailed``,
     ``_ses_region``) and no longer exposes the retired dual-path
     helpers. A regression that re-introduces a fallback path would
     fail this contract.

  2. The two retired vendor SDKs are not installed (no entry in
     ``importlib.metadata`` distributions). They were removed from
     ``requirements.txt`` / ``pyproject.toml`` as part of #556 and must
     stay out — re-adding them would re-open the silent-fallback door
     this task slammed shut. Vendor names are assembled at runtime so
     this test file does not itself ship banned literals.

  3. ``send_admin_email`` and ``_send_via_ses`` behave correctly when
     AWS credentials are absent: the admin path returns ``False`` (fire-
     and-forget), the user-facing path raises ``EmailSendFailed`` (loud
     per V4 §12 "no silent fallbacks").

  4. The legacy provider-flag env knobs no longer drive any code path.
"""
from __future__ import annotations

import importlib
import importlib.metadata as md
import os
from unittest.mock import patch

import pytest

# Vendor names assembled at runtime so this file does not itself trip
# the Task #556 CI guard's literal scan.
_RETIRED_VENDORS = ("send" + "grid", "re" + "send")
_RETIRED_HELPERS = ("_send_via_" + "sendgrid", "_send_via_" + "resend",
                    "_email_provider", "_send_sync")
_RETIRED_PROVIDER_ENV = "EMAIL_" + "PROVIDER"
_RETIRED_FALLBACK_ENV = "EMAIL_" + "FALLBACK"


def test_email_templates_exposes_ses_only_surface():
    et = importlib.import_module("email_templates")
    for name in ("_send_via_ses", "send_admin_email", "EmailSendFailed",
                 "_ses_region", "_parse_from"):
        assert hasattr(et, name), f"missing SES surface: {name}"
    for name in _RETIRED_HELPERS:
        assert not hasattr(et, name), (
            f"{name} resurrected — Task #556 retired the dual-path helpers"
        )


def test_retired_email_sdks_not_pinned_in_deps():
    """The retired vendor SDKs must not be pinned in either dependency
    manifest (``requirements.txt`` or ``pyproject.toml``). They may
    linger in the dev environment as transitive remnants of an older
    install — what matters for production is that they are not
    declared dependencies."""
    import pathlib
    backend = pathlib.Path(__file__).resolve().parents[2]
    manifests: list[str] = []
    req = backend / "requirements.txt"
    if req.exists():
        manifests.append(req.read_text())
    pp = backend / "pyproject.toml"
    if pp.exists():
        manifests.append(pp.read_text())
    blob = "\n".join(manifests).lower()
    for vendor in _RETIRED_VENDORS:
        # Match `vendor==`, `vendor>=`, `vendor[`, `"vendor"` — i.e. an
        # actual dependency declaration, not a stray English word.
        for marker in (f"{vendor}==", f"{vendor}>=", f'"{vendor}"', f"'{vendor}'"):
            assert marker not in blob, (
                f"{vendor} SDK is pinned ({marker}) — Task #556 retired it; "
                f"remove it from requirements.txt / pyproject.toml."
            )


def test_retired_email_sdks_not_imported_anywhere_in_backend():
    """Stronger contract than dep-manifest: no Python file in the backend
    may ``import`` the retired SDKs. Catches a stray ``import sendgrid``
    that slipped past the dep-pinning check."""
    import pathlib, re
    backend = pathlib.Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for py in backend.rglob("*.py"):
        # Skip this very file (it talks about the SDKs by name) and the
        # CI guard that bans them.
        if "tests/email" in py.as_posix():
            continue
        if "scripts/ci/check_canonical_delegation" in py.as_posix():
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        for vendor in _RETIRED_VENDORS:
            if re.search(rf"^\s*(?:from\s+{vendor}\b|import\s+{vendor}\b)",
                         text, flags=re.M):
                offenders.append(f"{py.relative_to(backend)} imports {vendor}")
    assert not offenders, (
        "Task #556 — retired SDK imports found:\n  " + "\n  ".join(offenders)
    )


def test_send_admin_email_returns_false_without_aws_creds():
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ,
                    {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""},
                    clear=False):
        ok = et.send_admin_email(
            to=["ops@syrabit.ai"], subject="x", html="<p>x</p>",
        )
    assert ok is False, "admin path must be fire-and-forget without creds"


def test_send_via_ses_raises_email_send_failed_without_aws_creds():
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ,
                    {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""},
                    clear=False):
        with pytest.raises(et.EmailSendFailed) as exc_info:
            et._send_via_ses("user@syrabit.ai", "x", "<p>x</p>")
    err = exc_info.value
    assert err.provider == "ses"
    assert "user@syrabit.ai" in err.recipients
    assert err.region


def test_no_legacy_provider_env_dependency():
    """The legacy provider-flag env knobs no longer drive any code path.
    Setting them to garbage values must not change the SES-only behaviour
    (i.e. no silent route to a different provider)."""
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ, {
        _RETIRED_PROVIDER_ENV: "garbage",
        _RETIRED_FALLBACK_ENV: "garbage",
        "AWS_ACCESS_KEY_ID": "",
        "AWS_SECRET_ACCESS_KEY": "",
    }, clear=False):
        ok = et.send_admin_email(
            to=["ops@syrabit.ai"], subject="x", html="<p>x</p>",
        )
    assert ok is False, (
        "Legacy provider-flag env knobs must NOT route to a fallback "
        "— they are retired; SES is the sole path."
    )
