"""Task #332 — symbol-drift guard for SQS Lambda consumers.

For every consumer in `services/backend/sqs_consumers/`, this test:

  1. Imports the consumer module (catches import-time errors).
  2. Asserts it exposes the canonical `handler(event, context)`
     entrypoint Lambda will invoke.
  3. Resolves the backend symbol the `_handle` body imports lazily
     and asserts it actually exists. This is what would have
     caught the `bing_keyword_client.refresh_keywords` /
     `cf_bot_crosscheck.crosscheck` /
     `discovery_engine_ingest.ingest` /
     `unified_logs_dao.pull_cf_window` drift the rev #14 reviewer
     flagged AND the `bing_webmaster.submit_url` /
     `seo_internal_linker.process_page` drift the rev #16 reviewer
     flagged.

The expected symbol map below is the contract — every new consumer
MUST be added here, otherwise the test will fail.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import pytest


_CONSUMER_PKG = "services.backend.sqs_consumers"


def _resolve_paths() -> tuple[pathlib.Path, pathlib.Path]:
    """Find the artifact root (parent of `services/`) and the backend
    dir (sibling `syrabit-backend/`) by walking up from this file.
    Tolerates being checked out at different depths so the test isn't
    brittle to repo-root changes."""
    here = pathlib.Path(__file__).resolve()
    artifact_root = None
    for ancestor in here.parents:
        if (ancestor / "services" / "backend" / "sqs_consumers").is_dir():
            artifact_root = ancestor
            break
    assert artifact_root is not None, (
        f"Could not locate artifact root from {here}"
    )
    backend_dir = artifact_root.parent / "syrabit-backend"
    assert backend_dir.is_dir(), f"backend dir not found at {backend_dir}"
    return artifact_root, backend_dir


_ARTIFACT_ROOT, _BACKEND_DIR = _resolve_paths()


@pytest.fixture(autouse=True, scope="module")
def _ensure_paths_on_syspath():
    """Make both the artifact root (so `services...` resolves) and the
    backend dir (so `bing_keyword_client` etc. resolve) importable."""
    added: list[str] = []
    for p in (_ARTIFACT_ROOT, _BACKEND_DIR):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
            added.append(sp)
    yield
    for sp in added:
        try:
            sys.path.remove(sp)
        except ValueError:
            pass


# (consumer_module, backend_module, backend_attr)
#
# These map 1:1 onto the lazy `from <backend_module> import <backend_attr>`
# inside each consumer's `_handle`. Drifting either side without
# updating this map is a test failure by design.
_EXPECTED = [
    ("bing_keyword",        "bing_keyword_client",       "refresh_keywords"),
    ("bing_submit",         "bing_submit_client",        "submit_url"),
    ("cf_bot_crosscheck",   "cf_bot_crosscheck",         "crosscheck"),
    ("discovery_engine",    "discovery_engine_ingest",   "ingest"),
    ("email_fallback",      None,                         None),  # uses boto3 SES directly
    ("seo_indexnow",        "routes.bot_discovery",      "notify_indexnow_for_page"),
    ("seo_internal_linker", "seo_internal_linker",       "propose_internal_links_for_page"),
    ("unified_logs_pull",   "unified_logs_dao",          "pull_cf_window"),
]


@pytest.mark.parametrize("consumer_name,backend_mod,backend_attr", _EXPECTED)
def test_consumer_handler_exposes_lambda_entrypoint(
    consumer_name, backend_mod, backend_attr,
):
    mod = importlib.import_module(f"{_CONSUMER_PKG}.{consumer_name}")
    assert hasattr(mod, "handler"), (
        f"{consumer_name}: must expose `handler(event, context)` for Lambda"
    )
    assert callable(mod.handler), f"{consumer_name}.handler must be callable"


@pytest.mark.parametrize("consumer_name,backend_mod,backend_attr", _EXPECTED)
def test_consumer_backend_symbol_exists(
    consumer_name, backend_mod, backend_attr,
):
    if backend_mod is None:
        pytest.skip(f"{consumer_name}: no backend symbol contract")
    try:
        bmod = importlib.import_module(backend_mod)
    except Exception as exc:
        pytest.fail(
            f"{consumer_name}: backend module {backend_mod!r} not importable: {exc!r}"
        )
    assert hasattr(bmod, backend_attr), (
        f"{consumer_name}: backend module {backend_mod!r} is missing "
        f"`{backend_attr}` — the consumer would raise AttributeError on "
        f"the first SQS message and accumulate in the DLQ."
    )
