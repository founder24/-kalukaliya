"""Task #556 — pin the bulk-email path contract.

The bulk path lives in :mod:`bulk_email` and dispatches via a
Cloudflare Email Worker (`BULK_EMAIL_WORKER_URL`). It is intentionally
independent of the SES transactional surface — there is no provider
abstraction shared between the two, and one can never serve as the
other's fallback (V4 §12 "no silent fallbacks").

This test guarantees:

  1. The module exposes the minimal public surface
     (``BulkEmailMessage``, ``send_bulk``, ``send_bulk_iter``) and does
     not import the SES transactional helper module (no fallback path).
  2. Without ``BULK_EMAIL_WORKER_URL`` set, ``send_bulk`` returns a
     ``skipped`` report — never raises, never falls back to SES.
  3. With the worker URL set, ``send_bulk`` POSTs to ``/bulk/send`` on
     the worker with a JSON body containing the recipients, subject,
     html, sender, and tags. A 2xx response is reported as ``sent``.
  4. A worker non-2xx is reported as ``failed`` with a structured reason.
  5. Recipients-empty is reported as ``no_recipients``.
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock, patch


def _fake_response(status_code: int, body: str = ""):
    r = MagicMock()
    r.status_code = status_code
    r.text = body
    return r


def test_bulk_module_surface_is_minimal_and_decoupled():
    bulk = importlib.import_module("bulk_email")
    assert hasattr(bulk, "BulkEmailMessage")
    assert hasattr(bulk, "send_bulk")
    assert hasattr(bulk, "send_bulk_iter")
    src = open(bulk.__file__).read()
    # No import of the SES transactional helper module — the two paths
    # must stay decoupled so neither can silently become the other's
    # fallback. Use a token assembled at runtime to avoid tripping the
    # Task #556 CI guard's literal scan.
    forbidden_module = "email" + "_templates"
    forbidden_import = "import " + forbidden_module
    forbidden_from = "from " + forbidden_module + " "
    assert forbidden_import not in src and forbidden_from not in src, (
        "bulk_email must not import the SES transactional helper module "
        "— Task #556 keeps the SES path and the CF bulk path separate."
    )


def test_send_bulk_skipped_when_worker_url_unset():
    bulk = importlib.import_module("bulk_email")
    msg = bulk.BulkEmailMessage(
        to=["a@x.com", "b@x.com"], subject="Weekly digest",
        html="<p>hi</p>",
    )
    with patch.dict(os.environ, {"BULK_EMAIL_WORKER_URL": ""}, clear=False):
        rep = bulk.send_bulk(msg)
    assert rep["sent"] == 0
    assert rep["skipped"] == 2
    assert rep["reason"] == "no_worker_url"


def test_send_bulk_posts_to_worker_and_reports_sent():
    bulk = importlib.import_module("bulk_email")
    fake_httpx = MagicMock()
    fake_httpx.post.return_value = _fake_response(202)
    msg = bulk.BulkEmailMessage(
        to=["a@x.com"], subject="Digest", html="<p>hi</p>",
        sender="Syrabit.ai <noreply@syrabit.ai>",
        tags={"campaign": "weekly-digest", "iso_week": "2026-W19"},
    )
    with patch.dict(os.environ, {
        "BULK_EMAIL_WORKER_URL": "https://bulk.workers.dev",
        "BULK_EMAIL_WORKER_AUTH_KEY": "tok123",
    }, clear=False), patch.dict(
        "sys.modules", {"httpx": fake_httpx}, clear=False,
    ):
        rep = bulk.send_bulk(msg)
    assert rep["sent"] == 1
    assert rep["reason"] == "ok"
    fake_httpx.post.assert_called_once()
    args, kwargs = fake_httpx.post.call_args
    assert args[0] == "https://bulk.workers.dev/bulk/send"
    body = kwargs["json"]
    assert body["to"] == ["a@x.com"]
    assert body["subject"] == "Digest"
    assert body["html"] == "<p>hi</p>"
    assert body["from"].startswith("Syrabit.ai")
    assert body["tags"]["campaign"] == "weekly-digest"
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"


def test_send_bulk_reports_failed_on_worker_non_2xx():
    bulk = importlib.import_module("bulk_email")
    fake_httpx = MagicMock()
    fake_httpx.post.return_value = _fake_response(500, "boom")
    msg = bulk.BulkEmailMessage(to=["a@x.com"], subject="x", html="<p>x</p>")
    with patch.dict(os.environ, {
        "BULK_EMAIL_WORKER_URL": "https://bulk.workers.dev",
    }, clear=False), patch.dict(
        "sys.modules", {"httpx": fake_httpx}, clear=False,
    ):
        rep = bulk.send_bulk(msg)
    assert rep["sent"] == 0
    assert rep["failed"] == 1
    assert rep["reason"].startswith("worker_http_5")


def test_send_bulk_no_recipients():
    bulk = importlib.import_module("bulk_email")
    msg = bulk.BulkEmailMessage(to=[], subject="x", html="<p>x</p>")
    rep = bulk.send_bulk(msg)
    assert rep["sent"] == 0 and rep["failed"] == 0 and rep["skipped"] == 0
    assert rep["reason"] == "no_recipients"


def test_send_bulk_iter_aggregates_per_message_reports():
    bulk = importlib.import_module("bulk_email")
    msgs = [
        bulk.BulkEmailMessage(to=["a@x.com"], subject="x", html="<p>x</p>"),
        bulk.BulkEmailMessage(to=[], subject="y", html="<p>y</p>"),
    ]
    fake_httpx = MagicMock()
    fake_httpx.post.return_value = _fake_response(200)
    with patch.dict(os.environ, {
        "BULK_EMAIL_WORKER_URL": "https://bulk.workers.dev",
    }, clear=False), patch.dict(
        "sys.modules", {"httpx": fake_httpx}, clear=False,
    ):
        agg = bulk.send_bulk_iter(msgs)
    assert agg["sent"] == 1
    assert agg["reasons"]["ok"] == 1
    assert agg["reasons"]["no_recipients"] == 1
