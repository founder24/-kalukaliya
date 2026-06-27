"""
Admin Ads Endpoints
Cross-network ad revenue tracking: manual entry, CSV upload, and AdSense sync.
Collections: ad_earnings (per-day per-network entries).
"""

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request
import logging
import csv
import io
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from typing import Optional

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Ads"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)

ALLOWED_NETWORKS = {"adsense", "adpushup", "adsterra", "propellerads"}


def _db():
    return get_mongo_client()[settings.MONGODB_DB_NAME]


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id", ""))
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


@router.get("/ads/overview")
async def ads_overview(days: int = 30):
    """
    Aggregate ad revenue overview: totals, by-network breakdown, daily trend.
    """
    try:
        db = _db()
        since = datetime.now(timezone.utc) - timedelta(days=days)

        pipeline_totals = [
            {"$match": {"date": {"$gte": since.strftime("%Y-%m-%d")}}},
            {
                "$group": {
                    "_id": None,
                    "revenue_inr": {"$sum": "$revenue_inr"},
                    "impressions": {"$sum": {"$ifNull": ["$impressions", 0]}},
                    "entries": {"$sum": 1},
                }
            },
        ]
        totals_raw = await db.ad_earnings.aggregate(pipeline_totals).to_list(length=1)
        totals = totals_raw[0] if totals_raw else {}
        totals.pop("_id", None)
        totals.setdefault("revenue_inr", 0)
        totals.setdefault("impressions", 0)
        totals.setdefault("entries", 0)

        pipeline_by_network = [
            {"$match": {"date": {"$gte": since.strftime("%Y-%m-%d")}}},
            {
                "$group": {
                    "_id": "$network",
                    "revenue_inr": {"$sum": "$revenue_inr"},
                    "impressions": {"$sum": {"$ifNull": ["$impressions", 0]}},
                    "entries": {"$sum": 1},
                }
            },
            {"$sort": {"revenue_inr": -1}},
        ]
        by_network_raw = await db.ad_earnings.aggregate(pipeline_by_network).to_list(length=20)
        by_network = [
            {
                "network": r["_id"],
                "revenue_inr": round(r["revenue_inr"], 2),
                "impressions": r["impressions"],
                "rpm": (
                    round((r["revenue_inr"] / r["impressions"]) * 1000, 2)
                    if r["impressions"] > 0
                    else 0
                ),
            }
            for r in by_network_raw
        ]

        pipeline_daily = [
            {"$match": {"date": {"$gte": since.strftime("%Y-%m-%d")}}},
            {
                "$group": {
                    "_id": "$date",
                    "revenue_inr": {"$sum": "$revenue_inr"},
                    "impressions": {"$sum": {"$ifNull": ["$impressions", 0]}},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        daily_raw = await db.ad_earnings.aggregate(pipeline_daily).to_list(length=days + 5)
        daily = [
            {
                "date": r["_id"],
                "revenue_inr": round(r["revenue_inr"], 2),
                "impressions": r["impressions"],
            }
            for r in daily_raw
        ]

        pipeline_by_placement = [
            {"$match": {"date": {"$gte": since.strftime("%Y-%m-%d")}, "placement": {"$exists": True, "$ne": None}}},
            {
                "$group": {
                    "_id": "$placement",
                    "revenue_inr": {"$sum": "$revenue_inr"},
                    "impressions": {"$sum": {"$ifNull": ["$impressions", 0]}},
                }
            },
            {"$sort": {"revenue_inr": -1}},
            {"$limit": 20},
        ]
        by_placement_raw = await db.ad_earnings.aggregate(pipeline_by_placement).to_list(length=20)
        by_placement = [
            {
                "placement": r["_id"],
                "revenue_inr": round(r["revenue_inr"], 2),
                "impressions": r["impressions"],
            }
            for r in by_placement_raw
        ]

        adsense_token = getattr(settings, "ADSENSE_ACCESS_TOKEN", None)

        return {
            "days": days,
            "totals": {
                "revenue_inr": round(totals["revenue_inr"], 2),
                "impressions": totals["impressions"],
                "entries": totals["entries"],
                "rpm": (
                    round((totals["revenue_inr"] / totals["impressions"]) * 1000, 2)
                    if totals["impressions"] > 0
                    else 0
                ),
            },
            "by_network": by_network,
            "by_day": daily,
            "by_placement": by_placement,
            "adsense_configured": bool(adsense_token),
        }
    except Exception as e:
        logger.error(f"Ads overview error: {e}")
        return {
            "days": days,
            "totals": {"revenue_inr": 0, "impressions": 0, "entries": 0, "rpm": 0},
            "by_network": [],
            "by_day": [],
            "by_placement": [],
            "adsense_configured": False,
        }


@router.get("/ads/earnings")
async def list_ad_earnings(days: int = 30, network: Optional[str] = None):
    """List ad earnings entries, newest first."""
    try:
        db = _db()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        query: dict = {"date": {"$gte": since}}
        if network and network in ALLOWED_NETWORKS:
            query["network"] = network

        cursor = db.ad_earnings.find(query).sort("date", -1).limit(500)
        entries = await cursor.to_list(length=500)
        return {"entries": [_serialize(e) for e in entries]}
    except Exception as e:
        logger.error(f"List ad earnings error: {e}")
        return {"entries": []}


@router.post("/ads/earnings")
async def add_ad_earning(request: Request):
    """Add a single ad earnings entry."""
    body = await request.json()
    network = body.get("network", "")
    if network not in ALLOWED_NETWORKS:
        raise HTTPException(status_code=400, detail=f"Invalid network. Allowed: {', '.join(sorted(ALLOWED_NETWORKS))}")

    revenue = body.get("revenue_inr")
    if revenue is None or not isinstance(revenue, (int, float)) or revenue < 0:
        raise HTTPException(status_code=400, detail="revenue_inr must be a non-negative number")

    date_str = body.get("date", "")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    try:
        db = _db()
        doc = {
            "network": network,
            "date": date_str,
            "revenue_inr": float(revenue),
            "impressions": body.get("impressions"),
            "placement": body.get("placement"),
            "created_at": datetime.now(timezone.utc),
        }
        result = await db.ad_earnings.insert_one(doc)
        return {"id": str(result.inserted_id), "ok": True}
    except Exception as e:
        logger.error(f"Add ad earning error: {e}")
        raise HTTPException(status_code=500, detail="Failed to add earning")


@router.delete("/ads/earnings/{entry_id}")
async def delete_ad_earning(entry_id: str):
    """Delete an ad earnings entry by id."""
    try:
        db = _db()
        result = await db.ad_earnings.delete_one({"_id": ObjectId(entry_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Entry not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete ad earning error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete entry")


@router.post("/ads/earnings/csv")
async def upload_ad_earnings_csv(
    network: str = Form(...),
    file: UploadFile = File(...),
):
    """
    Bulk-import ad earnings from a CSV file.
    Expected columns: date (YYYY-MM-DD), revenue_inr, impressions (optional), placement (optional).
    Rows with an existing (network, date, placement) triplet are updated (upsert).
    """
    if network not in ALLOWED_NETWORKS:
        raise HTTPException(status_code=400, detail=f"Invalid network. Allowed: {', '.join(sorted(ALLOWED_NETWORKS))}")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        raise HTTPException(status_code=400, detail="CSV is empty or has no data rows")

    inserted = 0
    updated = 0
    errors = []

    try:
        db = _db()
        for i, row in enumerate(rows, start=2):
            try:
                date_str = (row.get("date") or "").strip()
                datetime.strptime(date_str, "%Y-%m-%d")

                revenue_raw = (row.get("revenue_inr") or row.get("revenue") or "0").strip().replace(",", "")
                revenue = float(revenue_raw)

                impressions_raw = (row.get("impressions") or "").strip().replace(",", "")
                impressions = int(impressions_raw) if impressions_raw else None

                placement = (row.get("placement") or "").strip() or None

                filter_q = {"network": network, "date": date_str, "placement": placement}
                update_q = {
                    "$set": {
                        "revenue_inr": revenue,
                        "impressions": impressions,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                }
                result = await db.ad_earnings.update_one(filter_q, update_q, upsert=True)
                if result.upserted_id:
                    inserted += 1
                else:
                    updated += 1
            except Exception as row_err:
                errors.append(f"Row {i}: {row_err}")

        return {
            "ok": True,
            "inserted": inserted,
            "updated": updated,
            "errors": errors[:10],
            "total_rows": len(rows),
        }
    except Exception as e:
        logger.error(f"CSV upload error: {e}")
        raise HTTPException(status_code=500, detail=f"CSV processing failed: {e}")


@router.get("/ads/adsense/status")
async def adsense_status():
    """AdSense account connection status."""
    adsense_pub_id = getattr(settings, "ADSENSE_PUBLISHER_ID", None)
    adsense_token = getattr(settings, "ADSENSE_ACCESS_TOKEN", None)

    if not adsense_pub_id or not adsense_token:
        return {
            "configured": False,
            "publisher_id": adsense_pub_id,
            "account_name": None,
            "currency_code": None,
            "last_synced": None,
            "message": "Set ADSENSE_PUBLISHER_ID and ADSENSE_ACCESS_TOKEN secrets to enable AdSense sync.",
        }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://adsense.googleapis.com/v2/accounts",
                headers={"Authorization": f"Bearer {adsense_token}"},
            )
            if resp.is_success:
                accounts = resp.json().get("accounts", [])
                account = next(
                    (a for a in accounts if adsense_pub_id in a.get("name", "")), accounts[0] if accounts else {}
                )
                return {
                    "configured": True,
                    "publisher_id": adsense_pub_id,
                    "account_name": account.get("displayName"),
                    "currency_code": account.get("currencyCode"),
                    "last_synced": None,
                    "source": "adsense_api",
                }
    except Exception as e:
        logger.warning(f"AdSense status check failed: {e}")

    return {
        "configured": bool(adsense_pub_id and adsense_token),
        "publisher_id": adsense_pub_id,
        "account_name": None,
        "currency_code": None,
        "last_synced": None,
        "source": "unavailable",
        "message": "Could not reach AdSense API. Check ADSENSE_ACCESS_TOKEN.",
    }


@router.post("/ads/adsense/sync")
async def adsense_sync(request: Request):
    """
    Pull recent AdSense earnings into the ad_earnings collection.
    Requires ADSENSE_PUBLISHER_ID and ADSENSE_ACCESS_TOKEN.
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    days = min(int(body.get("days", 7)), 90)

    adsense_pub_id = getattr(settings, "ADSENSE_PUBLISHER_ID", None)
    adsense_token = getattr(settings, "ADSENSE_ACCESS_TOKEN", None)

    if not adsense_pub_id or not adsense_token:
        raise HTTPException(
            status_code=400,
            detail="ADSENSE_PUBLISHER_ID and ADSENSE_ACCESS_TOKEN must be set to sync AdSense data.",
        )

    try:
        import httpx
        from datetime import date

        end = date.today()
        start = end - timedelta(days=days)

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"https://adsense.googleapis.com/v2/{adsense_pub_id}/reports:generate",
                headers={"Authorization": f"Bearer {adsense_token}"},
                params={
                    "dateRange": "CUSTOM",
                    "startDate.year": start.year,
                    "startDate.month": start.month,
                    "startDate.day": start.day,
                    "endDate.year": end.year,
                    "endDate.month": end.month,
                    "endDate.day": end.day,
                    "dimensions": "DATE",
                    "metrics": "ESTIMATED_EARNINGS,PAGE_VIEWS",
                    "currencyCode": "INR",
                },
            )

            if not resp.is_success:
                raise HTTPException(status_code=502, detail=f"AdSense API error: {resp.text[:300]}")

            report = resp.json()
            rows = report.get("rows", [])
            db = _db()
            rows_synced = 0

            for row in rows:
                cells = row.get("cells", [])
                if len(cells) < 3:
                    continue
                date_str = cells[0].get("value", "")
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    continue

                try:
                    revenue_inr = float(cells[1].get("value", 0))
                    impressions = int(float(cells[2].get("value", 0)))
                except (ValueError, TypeError):
                    continue

                await db.ad_earnings.update_one(
                    {"network": "adsense", "date": date_str, "placement": None},
                    {
                        "$set": {
                            "revenue_inr": revenue_inr,
                            "impressions": impressions,
                            "updated_at": datetime.now(timezone.utc),
                        },
                        "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                    },
                    upsert=True,
                )
                rows_synced += 1

            return {"ok": True, "rows_synced": rows_synced, "days": days}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AdSense sync error: {e}")
        raise HTTPException(status_code=500, detail=f"AdSense sync failed: {e}")
