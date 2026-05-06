"""Task #425 — staging end-to-end smoke for the CF_EDGE_CACHE_ON write-through path.

Proves that a `CF_EDGE_CACHE_ON=1` flip in staging actually moves the
`kv_writes` / `kv_reads` counters surfaced under
`/admin/cf-health → kv_cache`. The check exercises:

  Python `kv_cache.KvCache`
        ──→ HTTP PUT  /api/edge/kv-cache/<key>   (worker `dispatchKvCache`)
        ──→ HTTP GET  /api/edge/kv-cache/<key>
        ──→ HTTP DELETE …

so a contract drift on either side (Python client header / payload
shape, or the TypeScript worker request handler) breaks the smoke
before promotion to production.

Flow
----
1. ``GET  {BASE}/admin/cf-health``           → capture baseline counters
   (corroborating signal only — see "Multi-replica" below).
2. ``POST {BASE}/admin/cf-health/kv-smoke``  → trigger one round trip
   through the deployed edge worker via the backend's ``KvCache``
   singleton. The endpoint returns the **same-replica** baseline /
   after / deltas it observed locally — these are the primary
   assertion because they are guaranteed to come from the pod that
   actually performed the round trip.
3. ``GET  {BASE}/admin/cf-health``           → corroborating diff
   against the baseline. When it agrees with the endpoint deltas we
   know the cf-health panel surfaces the live numbers; when it
   disagrees (e.g. the GET landed on a different replica behind a
   non-sticky LB) we log a warning but defer to the endpoint deltas.

Multi-replica
-------------
Staging may run more than one Container Apps replica behind a
non-sticky load balancer. The two ``/admin/cf-health`` GETs can
therefore land on different pods and disagree on absolute counter
values even though every pod is healthy. The endpoint's own
baseline/after pair is immune to that drift — both reads happen on
the pod that handled the POST — so the smoke uses it as the source
of truth and only logs (does not fail on) cf-health disagreement.

Required env vars
-----------------
  STAGING_BASE_URL    Backend base URL, e.g.
                      ``https://syrabit-backend-staging.eastus.azurecontainerapps.io``
  STAGING_ADMIN_JWT   Admin Bearer token accepted by ``get_admin_user``
                      (the same JWT shape the admin dashboard uses).

Optional
--------
  STAGING_TIMEOUT_S   HTTP timeout per call. Default: 15.

Exit codes
----------
  0 — round trip succeeded and both counters advanced.
  1 — env not configured (BASE_URL or admin token missing).
  2 — backend reported the edge mirror is not active
      (CF_EDGE_CACHE_ON off / URL or secret missing in staging).
  3 — round trip ran but counters did not advance — contract drift.
  4 — HTTP transport failure (timeout, connection refused, 5xx, etc.).

Run locally::

    STAGING_BASE_URL=https://staging.syrabit.ai \
    STAGING_ADMIN_JWT=eyJhbGc... \
    python -m scripts.cf_edge_cache_smoke
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _log(msg: str, *, payload: object | None = None) -> None:
    line = f"[cf-edge-cache smoke] {msg}"
    if payload is not None:
        line += " " + json.dumps(payload, default=str, sort_keys=True)
    print(line)


def _kv_counters(cf_health: dict) -> tuple[int, int, int]:
    kv = (cf_health or {}).get("kv_cache") or {}
    return (
        int(kv.get("kv_writes") or 0),
        int(kv.get("kv_reads") or 0),
        int(kv.get("kv_failures") or 0),
    )


def main() -> int:
    base_url = _env("STAGING_BASE_URL").rstrip("/")
    admin_jwt = _env("STAGING_ADMIN_JWT")
    timeout_s = float(_env("STAGING_TIMEOUT_S") or "15")

    if not base_url or not admin_jwt:
        _log("FAIL — STAGING_BASE_URL and STAGING_ADMIN_JWT must both be set",
             payload={"base_url_set": bool(base_url),
                      "admin_jwt_set": bool(admin_jwt)})
        return 1

    import httpx

    headers = {"Authorization": f"Bearer {admin_jwt}",
               "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=timeout_s, headers=headers) as client:
            # Step 1 — baseline.
            r1 = client.get(f"{base_url}/admin/cf-health")
            if r1.status_code != 200:
                _log("FAIL — baseline /admin/cf-health did not return 200",
                     payload={"status": r1.status_code, "body": r1.text[:400]})
                return 4
            baseline = r1.json()
            pre_w, pre_r, pre_f = _kv_counters(baseline)
            edge_active = bool(((baseline.get("kv_cache") or {})
                                .get("edge_active")))
            _log("baseline captured",
                 payload={"kv_writes": pre_w, "kv_reads": pre_r,
                          "kv_failures": pre_f, "edge_active": edge_active,
                          "CF_EDGE_CACHE_ON": (baseline.get("flags") or {})
                          .get("CF_EDGE_CACHE_ON")})

            # Step 2 — round trip.
            r2 = client.post(f"{base_url}/admin/cf-health/kv-smoke")
            if r2.status_code == 503:
                _log("FAIL — backend reports edge mirror not active",
                     payload={"detail": r2.json().get("detail")
                              if r2.headers.get("content-type", "")
                              .startswith("application/json")
                              else r2.text[:400]})
                return 2
            if r2.status_code != 200:
                _log("FAIL — /admin/cf-health/kv-smoke returned non-200",
                     payload={"status": r2.status_code,
                              "body": r2.text[:400]})
                return 4
            smoke = r2.json()
            _log("round trip executed", payload=smoke)
            if not smoke.get("round_trip_ok"):
                _log("FAIL — value written did not match value read back")
                return 3

            # Step 3 — re-read counters.
            r3 = client.get(f"{base_url}/admin/cf-health")
            if r3.status_code != 200:
                _log("FAIL — post-smoke /admin/cf-health did not return 200",
                     payload={"status": r3.status_code, "body": r3.text[:400]})
                return 4
            after = r3.json()
            post_w, post_r, post_f = _kv_counters(after)
            _log("post-smoke counters",
                 payload={"kv_writes": post_w, "kv_reads": post_r,
                          "kv_failures": post_f})
    except httpx.HTTPError as exc:
        _log("FAIL — HTTP transport error",
             payload={"error": f"{type(exc).__name__}: {exc}"})
        return 4

    # Primary assertion — the endpoint's own deltas are same-replica
    # and therefore robust against multi-pod LB scatter.
    endpoint_deltas = (smoke.get("deltas") or {})
    ep_write_delta = int(endpoint_deltas.get("kv_writes") or 0)
    ep_read_delta = int(endpoint_deltas.get("kv_reads") or 0)
    ep_fail_delta = int(endpoint_deltas.get("kv_failures") or 0)

    # Corroborating signal — diff of the two cf-health calls. If it
    # disagrees with the endpoint deltas we log it but don't fail on
    # it (a non-sticky LB can land the two GETs on different replicas).
    health_write_delta = post_w - pre_w
    health_read_delta = post_r - pre_r
    health_fail_delta = post_f - pre_f
    _log("deltas computed",
         payload={"endpoint": {"kv_writes": ep_write_delta,
                               "kv_reads": ep_read_delta,
                               "kv_failures": ep_fail_delta},
                  "cf_health_diff": {"kv_writes": health_write_delta,
                                     "kv_reads": health_read_delta,
                                     "kv_failures": health_fail_delta}})

    if (health_write_delta < ep_write_delta
            or health_read_delta < ep_read_delta):
        _log("WARN — cf-health diff lower than endpoint deltas; the "
             "two GETs likely hit different replicas behind a non-sticky "
             "LB. Trusting endpoint deltas (same-replica) for the gate.")

    if ep_fail_delta > 0:
        _log("FAIL — kv_failures incremented during the round trip",
             payload={"kv_failures_delta": ep_fail_delta})
        return 3
    if ep_write_delta < 1 or ep_read_delta < 1:
        _log("FAIL — counters did not advance, CF_EDGE_CACHE_ON flip is "
             "not actually wiring through to the worker",
             payload={"kv_writes_delta": ep_write_delta,
                      "kv_reads_delta": ep_read_delta})
        return 3

    _log("OK — CF_EDGE_CACHE write-through verified end-to-end",
         payload={"kv_writes_delta": ep_write_delta,
                  "kv_reads_delta": ep_read_delta,
                  "elapsed_ms": smoke.get("elapsed_ms")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
