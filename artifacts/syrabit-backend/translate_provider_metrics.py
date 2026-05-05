"""Task #386 — translation-provider distribution counters.

Tiny in-process tally used by ``/admin/cf-health`` to surface which
translation providers actually serviced traffic over the lifetime of
the worker. Aggregating across worker processes is out of scope — the
admin panel already understands per-pod numbers from other counters
and can sum them client-side.

Used by:
  * ``vertex_services.translate``         — Indic translate fan-out
  * ``llm.call_translate_with_dispatch``  — weighted-pool dispatcher
  * ``routes/admin_cf_health``            — surface in cf-health row

Failure mode: every helper here is best-effort and never raises so a
counter glitch cannot demote a translation request.
"""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_counts: dict[str, dict[str, int]] = {}


def record_provider_call(provider: str, success: bool) -> None:
    """Increment the success/failure counter for ``provider``.

    Safe to call from anywhere; never raises. Unknown / empty provider
    names are coerced to ``"unknown"`` so the panel always renders.
    """
    name = (provider or "unknown").strip().lower() or "unknown"
    key = "success" if success else "failure"
    with _lock:
        bucket = _counts.setdefault(name, {"success": 0, "failure": 0})
        bucket[key] = bucket.get(key, 0) + 1


def snapshot() -> dict[str, Any]:
    """Return a deep copy of current counts plus derived totals.

    Shape::

        {
          "providers": {
            "workers_indic": {"success": 12, "failure": 1, "total": 13, "share": 0.86},
            "google_translate": {"success": 2, "failure": 0, "total": 2, "share": 0.13},
          },
          "total_calls": 15,
          "primary_provider": "workers_indic",
          "primary_share": 0.86,
        }
    """
    with _lock:
        snap = {name: dict(buckets) for name, buckets in _counts.items()}

    total = 0
    for buckets in snap.values():
        buckets["total"] = buckets.get("success", 0) + buckets.get("failure", 0)
        total += buckets["total"]

    for buckets in snap.values():
        buckets["share"] = (buckets["total"] / total) if total else 0.0

    primary = max(snap.items(), key=lambda kv: kv[1]["total"], default=(None, None))
    return {
        "providers": snap,
        "total_calls": total,
        "primary_provider": primary[0],
        "primary_share": primary[1]["share"] if primary[1] else 0.0,
    }


def reset() -> None:
    """Test helper — clear all counts."""
    with _lock:
        _counts.clear()
