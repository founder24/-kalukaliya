"""Syrabit.ai — Ad revenue rollup (Task #551).

Cross-network ad earnings + viewability dashboard. Combines:

  1. Client-side viewability pings from <AdSlot/> (mirrored to
     `ad_impressions`, TTL 60 days) — gives impressions per
     network + placement and powers viewability-adjusted RPM.
  2. Admin-supplied revenue entries from publisher consoles
     (`ad_earnings`) — one row per (network, date, placement?).
     Supports manual JSON entry, CSV upload (v0), and an AdSense
     Management API sync when ADSENSE_* env vars are configured.

All endpoints under /admin require the admin user; the public
ingest endpoint /analytics/ad-impression is unauthenticated and
best-effort, mirroring the hydrate-event pattern.
"""
import csv
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from bson import ObjectId
from fastapi import (
    APIRouter, Body, Depends, File, Form, HTTPException, Query, Request,
    UploadFile,
)
from pydantic import BaseModel, Field

from auth_deps import get_admin_user
from deps import db, is_mongo_available

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
# Task #347 — quge5 removed (popunders + adult creatives, off site-wide).
KNOWN_NETWORKS = {"adsense", "adpushup", "adsterra", "propellerads"}
_AD_IMPRESSIONS_TTL_SECONDS = 60 * 60 * 24 * 60  # 60 days
_AD_INDEXES_READY = False


async def _ensure_ad_indexes() -> None:
    global _AD_INDEXES_READY
    if _AD_INDEXES_READY:
        return
    try:
        await db.ad_impressions.create_index(
            "created_at", expireAfterSeconds=_AD_IMPRESSIONS_TTL_SECONDS,
        )
        await db.ad_impressions.create_index(
            [("network", 1), ("placement", 1), ("created_at", -1)],
        )
        await db.ad_earnings.create_index(
            [("network", 1), ("date", 1), ("placement", 1)],
        )
        await db.ad_earnings.create_index([("date", -1)])
        _AD_INDEXES_READY = True
    except Exception as e:
        logger.warning(f"ad_* index create failed (non-fatal): {e}")


# ─────────────────────────────────────────────
# Public ingest — viewability ping mirror
# ─────────────────────────────────────────────
@router.post("/analytics/ad-impression")
async def track_ad_impression(
    request: Request,
    placement: str = Body(...),
    network: str = Body(...),
):
    """Persist one viewability event per <AdSlot/> mount.

    Best-effort + capped — never raises; analytics must not break
    page loads. Drops obviously-bogus payloads (unknown network,
    oversize fields) instead of polluting the collection.

    Note: an ``enabled`` body field used to be accepted and persisted
    here but it was always ``true`` at the only call site (AdSlot's
    IntersectionObserver fires only after a ``cfg.enabled`` early
    return) and no aggregation ever read it back. Removed in the
    admin-panel audit; legacy docs with ``enabled`` set will age out
    via the 60-day TTL on ``created_at``. FastAPI ignores any extra
    keys the client sends, so older bundles still hitting this route
    won't error.
    """
    if not isinstance(placement, str) or not isinstance(network, str):
        return {"status": "ignored"}
    if network not in KNOWN_NETWORKS:
        return {"status": "ignored"}
    if len(placement) > 80:
        return {"status": "ignored"}
    try:
        await _ensure_ad_indexes()
        ua = request.headers.get("user-agent", "")[:200]
        path = (request.headers.get("referer", "") or "")[:200]
        await db.ad_impressions.insert_one({
            "placement": placement,
            "network": network,
            "ua": ua or None,
            "path": path or None,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.debug(f"ad-impression ingest failed: {e}")
    return {"status": "tracked"}


# ─────────────────────────────────────────────
# Admin: overview rollup
# ─────────────────────────────────────────────
def _round2(v: float) -> float:
    try:
        return round(float(v), 2)
    except Exception:
        return 0.0


@router.get("/admin/ads/overview")
async def admin_ads_overview(
    days: int = Query(30, ge=1, le=180),
    admin: dict = Depends(get_admin_user),
):
    """Cross-network earnings + impressions rollup for the admin dashboard.

    Returns:
      networks: per-network totals (impressions, revenue_inr, rpm)
      placements: per-placement-key breakdown with network attribution
      daily: per-day series (impressions + revenue) for charts
      adsense_configured: bool — whether the AdSense API sync is wired
    """
    if not await is_mongo_available():
        return {
            "days": days, "networks": [], "placements": [], "daily": [],
            "adsense_configured": _adsense_configured(),
        }
    await _ensure_ad_indexes()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_date = since.strftime("%Y-%m-%d")

    # Impressions per (network, placement)
    impressions_by_network: Dict[str, int] = {n: 0 for n in KNOWN_NETWORKS}
    impressions_by_placement: Dict[str, Dict[str, Any]] = {}
    try:
        cur = db.ad_impressions.aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"network": "$network", "placement": "$placement"},
                "count": {"$sum": 1},
            }},
        ])
        async for row in cur:
            net = row["_id"].get("network") or "unknown"
            place = row["_id"].get("placement") or "unknown"
            cnt = int(row.get("count") or 0)
            impressions_by_network[net] = impressions_by_network.get(net, 0) + cnt
            impressions_by_placement.setdefault(place, {
                "placement": place, "network": net, "impressions": 0, "revenue_inr": 0.0,
            })
            impressions_by_placement[place]["impressions"] += cnt
    except Exception as e:
        logger.debug(f"ad_impressions aggregate failed: {e}")

    # Revenue per (network, placement, date) + fill-rate inputs
    revenue_by_network: Dict[str, float] = {n: 0.0 for n in KNOWN_NETWORKS}
    revenue_by_date: Dict[str, Dict[str, float]] = {}
    # ad_requests / matched_ad_requests per network and per date — used to
    # compute weighted fill_rate_pct (matched/requests*100). Only AdSense API
    # rows currently populate these fields, but the aggregator handles any
    # network that supplies them.
    requests_by_network: Dict[str, int] = {n: 0 for n in KNOWN_NETWORKS}
    matched_by_network: Dict[str, int] = {n: 0 for n in KNOWN_NETWORKS}
    requests_by_date: Dict[str, int] = {}
    matched_by_date: Dict[str, int] = {}
    try:
        cur2 = db.ad_earnings.find(
            {"date": {"$gte": since_date}},
            {"_id": 0, "network": 1, "placement": 1, "date": 1,
             "revenue_inr": 1, "impressions": 1,
             "ad_requests": 1, "matched_ad_requests": 1},
        )
        async for row in cur2:
            net = row.get("network") or "unknown"
            rev = float(row.get("revenue_inr") or 0)
            d = row.get("date") or ""
            place = row.get("placement") or ""
            ad_reqs = int(row.get("ad_requests") or 0)
            matched = int(row.get("matched_ad_requests") or 0)
            revenue_by_network[net] = revenue_by_network.get(net, 0.0) + rev
            requests_by_network[net] = requests_by_network.get(net, 0) + ad_reqs
            matched_by_network[net] = matched_by_network.get(net, 0) + matched
            day_bucket = revenue_by_date.setdefault(d, {})
            day_bucket[net] = day_bucket.get(net, 0.0) + rev
            day_bucket["__total__"] = day_bucket.get("__total__", 0.0) + rev
            if ad_reqs:
                requests_by_date[d] = requests_by_date.get(d, 0) + ad_reqs
            if matched:
                matched_by_date[d] = matched_by_date.get(d, 0) + matched
            if place:
                p = impressions_by_placement.setdefault(place, {
                    "placement": place, "network": net,
                    "impressions": 0, "revenue_inr": 0.0,
                })
                p["revenue_inr"] = float(p.get("revenue_inr") or 0) + rev
    except Exception as e:
        logger.debug(f"ad_earnings aggregate failed: {e}")

    # Per-network rollup
    networks_out: List[Dict[str, Any]] = []
    for net in sorted(KNOWN_NETWORKS):
        imps = impressions_by_network.get(net, 0)
        rev = revenue_by_network.get(net, 0.0)
        rpm = _round2((rev / imps) * 1000) if imps > 0 else 0.0
        reqs = requests_by_network.get(net, 0)
        matched = matched_by_network.get(net, 0)
        fill_rate = _round2((matched / reqs) * 100) if reqs > 0 else None
        networks_out.append({
            "network": net,
            "impressions": imps,
            "revenue_inr": _round2(rev),
            "rpm_inr": rpm,
            "ad_requests": reqs,
            "matched_ad_requests": matched,
            "fill_rate_pct": fill_rate,
        })

    # Daily series (last `days` days, zero-filled)
    daily_out: List[Dict[str, Any]] = []
    # daily impressions (network-agnostic)
    daily_imps: Dict[str, int] = {}
    try:
        cur3 = db.ad_impressions.aggregate([
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "count": {"$sum": 1},
            }},
        ])
        async for row in cur3:
            daily_imps[row["_id"]] = int(row.get("count") or 0)
    except Exception as e:
        logger.debug(f"daily impressions agg failed: {e}")

    for i in range(days):
        d = (datetime.now(timezone.utc) - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        rev_today = revenue_by_date.get(d, {})
        imps_today = daily_imps.get(d, 0)
        total_rev = float(rev_today.get("__total__", 0.0))
        reqs_today = requests_by_date.get(d, 0)
        matched_today = matched_by_date.get(d, 0)
        daily_out.append({
            "date": d,
            "impressions": imps_today,
            "revenue_inr": _round2(total_rev),
            "rpm_inr": _round2((total_rev / imps_today) * 1000) if imps_today > 0 else 0.0,
            "fill_rate_pct": (
                _round2((matched_today / reqs_today) * 100) if reqs_today > 0 else None
            ),
        })

    # Per-placement rollup (sorted by revenue desc, then impressions)
    placements_out = sorted(
        [
            {
                "placement": p["placement"],
                "network": p["network"],
                "impressions": int(p.get("impressions") or 0),
                "revenue_inr": _round2(p.get("revenue_inr") or 0),
                "rpm_inr": (
                    _round2((p["revenue_inr"] / p["impressions"]) * 1000)
                    if p.get("impressions") and p.get("revenue_inr") else 0.0
                ),
            }
            for p in impressions_by_placement.values()
        ],
        key=lambda r: (r["revenue_inr"], r["impressions"]),
        reverse=True,
    )

    return {
        "days": days,
        "networks": networks_out,
        "placements": placements_out,
        "daily": daily_out,
        "adsense_configured": _adsense_configured(),
        "totals": {
            "impressions": sum(n["impressions"] for n in networks_out),
            "revenue_inr": _round2(sum(n["revenue_inr"] for n in networks_out)),
        },
    }


# ─────────────────────────────────────────────
# Admin: earnings CRUD
# ─────────────────────────────────────────────
class AdEarningEntry(BaseModel):
    network: str
    date: str = Field(..., description="YYYY-MM-DD")
    revenue_inr: float
    impressions: Optional[int] = None
    placement: Optional[str] = None
    source: Optional[str] = "manual"


def _validate_date_str(s: str) -> str:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")


@router.get("/admin/ads/earnings")
async def admin_list_earnings(
    days: int = Query(30, ge=1, le=365),
    network: Optional[str] = Query(None),
    admin: dict = Depends(get_admin_user),
):
    if not await is_mongo_available():
        return {"entries": []}
    await _ensure_ad_indexes()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    q: Dict[str, Any] = {"date": {"$gte": since}}
    if network:
        q["network"] = network
    entries: List[Dict[str, Any]] = []
    cur = db.ad_earnings.find(q).sort("date", -1).limit(500)
    async for row in cur:
        row["_id"] = str(row["_id"])
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].isoformat()
        entries.append(row)
    return {"entries": entries}


@router.post("/admin/ads/earnings")
async def admin_add_earning(
    entry: AdEarningEntry, admin: dict = Depends(get_admin_user),
):
    if entry.network not in KNOWN_NETWORKS:
        raise HTTPException(400, f"Unknown network '{entry.network}'.")
    _validate_date_str(entry.date)
    if entry.revenue_inr < 0:
        raise HTTPException(400, "revenue_inr must be >= 0")
    # Task #731 S5 — every row's `source` must come from the closed set
    # so `adsense_api` can NEVER be forged via the manual entry path.
    # Manual entries default to "manual" and may opt into "csv" only
    # for parity with the CSV upload route.
    src = (entry.source or "manual").strip()
    if src not in {"manual", "csv"}:
        raise HTTPException(
            400,
            f"source must be one of {{manual, csv}} on this endpoint; "
            f"adsense_api rows are written exclusively by /admin/ads/adsense/sync.",
        )
    await _ensure_ad_indexes()
    doc = {
        "network": entry.network,
        "date": entry.date,
        "revenue_inr": float(entry.revenue_inr),
        "impressions": int(entry.impressions) if entry.impressions is not None else None,
        "placement": (entry.placement or None),
        "source": src,
        "currency_original": "INR",  # manual entries are always rupees
        "created_at": datetime.now(timezone.utc),
    }
    res = await db.ad_earnings.insert_one(doc)
    doc["_id"] = str(res.inserted_id)
    doc["created_at"] = doc["created_at"].isoformat()
    return {"entry": doc}


@router.delete("/admin/ads/earnings/{entry_id}")
async def admin_delete_earning(
    entry_id: str, admin: dict = Depends(get_admin_user),
):
    try:
        oid = ObjectId(entry_id)
    except Exception:
        raise HTTPException(400, "invalid id")
    res = await db.ad_earnings.delete_one({"_id": oid})
    if res.deleted_count == 0:
        raise HTTPException(404, "not found")
    return {"deleted": True}


@router.post("/admin/ads/earnings/csv")
async def admin_upload_earnings_csv(
    network: str = Form(...),
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """CSV upload (v0 path before AdSense API is wired).

    Expected columns (header row required):
      date,revenue_inr,impressions,placement
    `placement` is optional. Existing rows for the same
    (network,date,placement) are replaced (upsert) so re-uploading
    a corrected CSV is idempotent.
    """
    if network not in KNOWN_NETWORKS:
        raise HTTPException(400, f"Unknown network '{network}'.")
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    if not raw.strip():
        raise HTTPException(400, "empty file")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "date" not in reader.fieldnames or "revenue_inr" not in reader.fieldnames:
        raise HTTPException(400, "CSV must have 'date' and 'revenue_inr' columns")
    await _ensure_ad_indexes()
    inserted = 0
    updated = 0
    now = datetime.now(timezone.utc)
    for row in reader:
        try:
            d = (row.get("date") or "").strip()
            _validate_date_str(d)
            rev = float((row.get("revenue_inr") or "0").strip() or 0)
            imps_raw = (row.get("impressions") or "").strip()
            imps = int(imps_raw) if imps_raw else None
            place = (row.get("placement") or "").strip() or None
        except (HTTPException, ValueError):
            continue
        flt = {"network": network, "date": d, "placement": place}
        upd = {
            "$set": {
                "network": network, "date": d, "placement": place,
                "revenue_inr": rev, "impressions": imps,
                "source": "csv", "created_at": now,
            },
        }
        res = await db.ad_earnings.update_one(flt, upd, upsert=True)
        if res.upserted_id is not None:
            inserted += 1
        elif res.modified_count:
            updated += 1
    return {"inserted": inserted, "updated": updated, "network": network}


# ─────────────────────────────────────────────
# AdSense Management API sync (optional)
# ─────────────────────────────────────────────
def _adsense_configured() -> bool:
    return bool(
        os.environ.get("ADSENSE_REFRESH_TOKEN")
        and os.environ.get("ADSENSE_CLIENT_ID")
        and os.environ.get("ADSENSE_CLIENT_SECRET")
        and os.environ.get("ADSENSE_ACCOUNT_ID"),
    )


async def _adsense_access_token() -> str:
    rt = os.environ.get("ADSENSE_REFRESH_TOKEN", "")
    cid = os.environ.get("ADSENSE_CLIENT_ID", "")
    cs = os.environ.get("ADSENSE_CLIENT_SECRET", "")
    if not (rt and cid and cs):
        raise HTTPException(503, "AdSense not configured")
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": cid, "client_secret": cs,
                "refresh_token": rt, "grant_type": "refresh_token",
            },
        )
        if r.status_code != 200:
            raise HTTPException(502, f"AdSense oauth failed: {r.text[:200]}")
        # Google occasionally returns HTTP 200 with a body that's
        # missing `access_token` (e.g. revoked refresh token returning
        # `{"error": "invalid_grant", ...}` with a 200 from a transient
        # OAuth proxy). Returning "" here used to flow into a
        # downstream `Authorization: Bearer ` request that AdSense
        # answers with 401 — making it look like the AdSense API is
        # broken when the real cause is a stale refresh token. Fail
        # loud here so `_record_adsense_sync` records the actionable
        # cause and the status panel turns red.
        access_token = r.json().get("access_token")
        if not access_token:
            err = (r.json().get("error_description")
                   or r.json().get("error")
                   or "missing access_token in oauth response")
            raise HTTPException(502, f"AdSense oauth: {err}")
        return access_token


# Task #731 S5 — single source-of-truth list for the `source` field on
# every ad_earnings row. Anything outside this set is rejected at insert
# time so historical data stays trustworthy. The list is intentionally
# small: every value here corresponds to a code path that can produce
# the row in this file.
_VALID_AD_EARNINGS_SOURCES = {"adsense_api", "manual", "csv"}


# Task #731 S6 — record the last AdSense sync result so the admin status
# panel can flip red the moment a sync fails (instead of the previous
# behaviour where a 401/403/500 would silently produce zero rows and
# leave the dashboard showing "₹0 today" — indistinguishable from "no
# ads served today").
async def _record_adsense_sync(*, ok: bool, days: int, rows: int = 0,
                               error: str = "", fx_source: str = "",
                               fx_rate: float | None = None) -> None:
    try:
        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {
            "$set": {
                "_id": "adsense",
                "last_attempted_at": now.isoformat(),
                "last_status": "ok" if ok else "error",
                "last_days": int(days),
            }
        }
        if ok:
            update["$set"].update({
                "last_success_at": now.isoformat(),
                "last_rows_synced": int(rows),
                "last_fx_source": fx_source or None,
                "last_fx_rate": float(fx_rate) if fx_rate else None,
                "last_error_message": None,
                "last_error_at_recent": None,
            })
        else:
            update["$set"].update({
                "last_error_at": now.isoformat(),
                "last_error_at_recent": now.isoformat(),
                "last_error_message": (error or "")[:500],
            })
        await db.ad_sync_status.update_one({"_id": "adsense"}, update, upsert=True)
    except Exception as e:
        logger.warning(f"_record_adsense_sync failed: {e}")


@router.get("/admin/ads/adsense/status")
async def admin_adsense_status(admin: dict = Depends(get_admin_user)):
    # Task #731 S6 — read sync state from db.ad_sync_status so admin UI
    # can surface "AdSense sync failed at HH:MM — last error: ...".
    sync_state: dict[str, Any] = {}
    try:
        doc = await db.ad_sync_status.find_one({"_id": "adsense"}, {"_id": 0})
        if doc:
            sync_state = doc
    except Exception as e:
        logger.debug(f"adsense status read failed: {e}")
    return {
        "configured": _adsense_configured(),
        "account_id": os.environ.get("ADSENSE_ACCOUNT_ID", "") if _adsense_configured() else "",
        "missing_env": [
            k for k in (
                "ADSENSE_REFRESH_TOKEN", "ADSENSE_CLIENT_ID",
                "ADSENSE_CLIENT_SECRET", "ADSENSE_ACCOUNT_ID",
            ) if not os.environ.get(k)
        ],
        # The admin panel renders these directly: a red banner if
        # last_status == "error", a green check + relative timestamp
        # if last_status == "ok".
        "sync": sync_state,
    }


@router.post("/admin/ads/adsense/sync")
async def admin_adsense_sync(
    days: int = Query(7, ge=1, le=90),
    admin: dict = Depends(get_admin_user),
):
    """Pull daily AdSense earnings via the AdSense Management API.

    Stores one ad_earnings row per (date) with network='adsense' and
    source='adsense_api'. Re-running for the same window is idempotent
    (upsert on (network, date, placement=None)).
    """
    if not _adsense_configured():
        # S6: configuration is the user's mistake, not an outage —
        # don't poison the sync-status doc with this.
        raise HTTPException(503, "AdSense not configured. Set ADSENSE_* env vars.")

    # Task #731 S4 — fetch USD->INR FX up front. AdSense pays in the
    # account's currency (USD here per the published account profile);
    # ESTIMATED_EARNINGS is therefore USD and was previously written to
    # `revenue_inr` directly — which is what made the dashboard show
    # 1 impression earning ₹247. We refuse to sync if FX is unavailable
    # because writing zeroes is exactly the silent-failure mode S6
    # forbids.
    try:
        from fx import get_usd_inr_rate, FxRateUnavailable
        fx = await get_usd_inr_rate()
        fx_rate = float(fx["rate"])
        fx_source = str(fx["source"])
        fx_fetched_at = str(fx["fetched_at"])
    except FxRateUnavailable as e:
        msg = f"USD->INR FX unavailable: {e}"
        await _record_adsense_sync(ok=False, days=days, error=msg)
        raise HTTPException(503, msg)
    except Exception as e:
        msg = f"FX helper crashed: {e}"
        await _record_adsense_sync(ok=False, days=days, error=msg)
        raise HTTPException(500, msg)

    try:
        token = await _adsense_access_token()
    except HTTPException as e:
        await _record_adsense_sync(ok=False, days=days, error=f"oauth: {e.detail}")
        raise

    account = os.environ.get("ADSENSE_ACCOUNT_ID", "")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    url = f"https://adsense.googleapis.com/v2/accounts/{account}/reports:generate"
    params = [
        ("dateRange", "CUSTOM"),
        ("startDate.year", start.year), ("startDate.month", start.month), ("startDate.day", start.day),
        ("endDate.year", end.year), ("endDate.month", end.month), ("endDate.day", end.day),
        ("dimensions", "DATE"),
        ("metrics", "ESTIMATED_EARNINGS"),
        ("metrics", "IMPRESSIONS"),
        ("metrics", "AD_REQUESTS"),
        ("metrics", "MATCHED_AD_REQUESTS"),
    ]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as e:
        msg = f"network error contacting AdSense: {e}"
        await _record_adsense_sync(ok=False, days=days, error=msg)
        raise HTTPException(502, msg)

    if r.status_code != 200:
        # Task #731 S6 — record + raise. We deliberately do NOT write a
        # 0-revenue row for any date; the dashboard distinguishes
        # "we got a real zero from AdSense" from "we couldn't talk to
        # AdSense" via the sync status panel.
        msg = f"AdSense API error HTTP {r.status_code}: {r.text[:300]}"
        await _record_adsense_sync(ok=False, days=days, error=msg)
        raise HTTPException(502, msg)

    body = r.json()
    rows = body.get("rows", []) or []
    headers = [h.get("name") for h in body.get("headers", [])]
    try:
        date_idx = headers.index("DATE")
        earn_idx = headers.index("ESTIMATED_EARNINGS")
        imps_idx = headers.index("IMPRESSIONS")
        req_idx = headers.index("AD_REQUESTS")
        match_idx = headers.index("MATCHED_AD_REQUESTS")
    except ValueError:
        msg = "Unexpected AdSense response shape (missing required columns)"
        await _record_adsense_sync(ok=False, days=days, error=msg)
        raise HTTPException(502, msg)

    await _ensure_ad_indexes()
    upserts = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        cells = row.get("cells", [])
        try:
            d = cells[date_idx].get("value") or ""
            _validate_date_str(d)
            rev_usd = float(cells[earn_idx].get("value") or 0)  # AdSense reports in account currency = USD
            imps = int(cells[imps_idx].get("value") or 0)
            ad_reqs = int(cells[req_idx].get("value") or 0)
            matched = int(cells[match_idx].get("value") or 0)
        except (IndexError, AttributeError, ValueError):
            continue
        # S4 — convert at the FX rate captured at the START of the sync,
        # so every row in this batch shares one rate (auditable + means
        # admins can reconcile against AdSense's own report by undoing
        # one multiplication).
        rev_inr = round(rev_usd * fx_rate, 2)
        fill_rate = round((matched / ad_reqs) * 100, 2) if ad_reqs > 0 else None
        flt = {"network": "adsense", "date": d, "placement": None}
        await db.ad_earnings.update_one(
            flt,
            {"$set": {
                "network": "adsense", "date": d, "placement": None,
                # Both the original-currency receipt AND the unified INR
                # number — admin UI shows both via S9.
                "revenue_inr": rev_inr,
                "revenue_usd": round(rev_usd, 6),
                "currency_original": "USD",
                "fx_rate": fx_rate,
                "fx_source": fx_source,
                "fx_fetched_at": fx_fetched_at,
                "impressions": imps,
                "ad_requests": ad_reqs, "matched_ad_requests": matched,
                "fill_rate_pct": fill_rate,
                "source": "adsense_api", "created_at": now,
            }},
            upsert=True,
        )
        upserts += 1

    await _record_adsense_sync(
        ok=True, days=days, rows=upserts,
        fx_source=fx_source, fx_rate=fx_rate,
    )
    return {
        "days": days,
        "rows_synced": upserts,
        "fx_rate": fx_rate,
        "fx_source": fx_source,
        "fx_fetched_at": fx_fetched_at,
    }
