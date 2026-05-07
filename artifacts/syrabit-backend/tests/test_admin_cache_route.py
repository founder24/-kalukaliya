"""Task #571 — regression tests for the admin cache health route.

Pinned because the original implementation had a broken
`_MONITORED_URLS_PATH` (parents[2] resolved to `artifacts/`, not the
repo root) which silently produced an empty `edge_targets` list and
defeated the route-target observability panel."""
from __future__ import annotations

from pathlib import Path


def test_monitored_urls_path_resolves_to_real_file():
    """The default path resolution must point at the repo's actual
    `workers/edge-proxy/monitored-urls.json`."""
    from routes.admin_cache import _MONITORED_URLS_PATH
    assert _MONITORED_URLS_PATH.exists(), (
        f"_MONITORED_URLS_PATH does not exist: {_MONITORED_URLS_PATH}. "
        "If you renamed the file or restructured the repo, update the "
        "default in routes/admin_cache.py or set the MONITORED_URLS_PATH "
        "env var on the Lambda runtime."
    )


def test_load_edge_targets_is_non_empty_in_normal_repo_layout():
    """`backend_paths` (NOT `backend_routes`!) is the canonical key in
    monitored-urls.json. The original code referenced the wrong key
    and silently returned []."""
    from routes.admin_cache import _load_edge_targets
    targets = _load_edge_targets()
    assert len(targets) > 0, (
        "Expected at least one cacheable route in edge_targets. Either "
        "monitored-urls.json no longer has any cacheable entries (which "
        "would be a regression in its own right) or _load_edge_targets "
        "is reading the wrong key/path."
    )
    sample = targets[0]
    for k in ("path", "ttl_seconds", "cache_hit_ratio_target", "user_keyed"):
        assert k in sample, f"missing field {k!r} in edge_targets row: {sample}"


def test_layer_helpers_never_raise():
    """All three best-effort helpers must downgrade gracefully when
    their backing service is absent (Lambda local invocation, dev box
    without redis, etc.)."""
    from routes import admin_cache as ac
    arc = ac._ai_response_cache_stats()
    assert "hits" in arc and "misses" in arc and "hit_rate" in arc
    rag = ac._rag_cache_stats()
    assert "hits" in rag and "misses" in rag and "hit_rate" in rag
    l1 = ac._l1_inproc_stats()
    assert isinstance(l1, dict)
