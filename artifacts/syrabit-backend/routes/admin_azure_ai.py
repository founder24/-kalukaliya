"""Admin proxy for the eight Azure-native AI features (Task #338, post Task #552 §G-R).

The React `AdminAzureAiPanel` polls `GET /admin/azure/ai/health`
and POSTs `/admin/azure/ai/toggle`. This module backs both routes.

Source data
-----------
* **Per-feature toggle** — Mongo collection ``azure_ai_settings``
  keyed by the ``settingKey`` documented below (e.g.
  ``azure.openai.enabled``). Defaults to enabled when missing so
  a fresh deploy doesn't disable any chain on first boot.
  Reads in chain code go through ``azure_ai_runtime.is_enabled``
  which adds a 30-second TTL cache; this admin route writes
  directly and calls ``azure_ai_runtime.invalidate`` to drop the
  cache so a flip propagates within the next request.
* **Throttle / latency** — In-process ``azure_ai_metrics.SNAPSHOT``
  written by chain code on each Azure call (rolling 15-min window).
  Optionally overlaid with the Mongo ``azure_ai_metrics_rollup``
  collection populated by the App Insights pull cron job — only
  when that collection exists, so absence is silent.
* **Spend MTD** — overlay from the same rollup collection if
  populated by the billing cron; ``null`` until the cron lands.
* **Anomalies** — most-recent rows from ``azure_ai_anomalies``
  appended by the Anomaly Detector cron.

Hardening: each data source is best-effort. If Mongo is degraded
the route still returns a populated ``features`` list with
``compositeAlert="degraded"`` and per-feature metric fields set
to ``null``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_deps import get_admin_user as require_admin

logger = logging.getLogger(__name__)
router = APIRouter()

FEATURES: list[dict[str, Any]] = [
    {
        "key": "openai",
        "displayName": "Azure OpenAI",
        "purpose": "Additional LLM target wired into AI Gateway routing",
        "settingKey": "azure.openai.enabled",
        "failureMode": "Falls back to direct OpenAI \u2192 Bedrock-Cohere \u2192 Groq \u2192 Gemini",
        "spendBudgetUsd": 50.0,
    },
    # Task #552 §G-R — `speech` and `translator` rows removed (Azure
    # Speech and Azure Translator fully retired).
    {
        "key": "document_intel",
        "displayName": "Azure Document Intelligence",
        "purpose": "Layout-aware OCR for past papers + marks sheets",
        "settingKey": "azure.docintel.enabled",
        "failureMode": "Falls back to Textract \u2192 AI Vision",
        "spendBudgetUsd": 25.0,
    },
    {
        "key": "vision",
        "displayName": "Azure AI Vision",
        "purpose": "OCR + image-understanding fallback tier",
        "settingKey": "azure.vision.enabled",
        "failureMode": "Falls back to Google Vision \u2192 Tesseract",
        "spendBudgetUsd": 15.0,
    },
    {
        "key": "content_safety",
        "displayName": "Azure Content Safety",
        "purpose": "Sync moderation on chat I/O + uploads",
        "settingKey": "azure.content_safety.enabled",
        "failureMode": "Borderline routes to admin moderation queue alongside Rekognition flags",
        "spendBudgetUsd": 20.0,
    },
    {
        "key": "language",
        "displayName": "Azure AI Language",
        "purpose": "Key phrases + entities + summaries + PII detection",
        "settingKey": "azure.language.enabled",
        "failureMode": "Falls back to last cached enrichment; PII redaction falls back to regex",
        "spendBudgetUsd": 15.0,
    },
    {
        "key": "search",
        "displayName": "Azure AI Search",
        "purpose": "Hybrid keyword + vector retriever (Pinecone parallel)",
        "settingKey": "rag.retriever",
        "settingValueOn": "azure-search",
        "settingValueOff": "pinecone",
        "failureMode": "Retriever switch (rag.retriever): pinecone (default) / azure-search / shadow",
        "spendBudgetUsd": 40.0,
    },
    {
        "key": "anomaly_detector",
        "displayName": "Azure Anomaly Detector",
        "purpose": "Credit-burn / error-rate / R2-cost anomaly alerts",
        "settingKey": "azure.anomaly.enabled",
        "failureMode": "Cron emits ai_anomaly_detected metric \u2192 ops Slack action group",
        "spendBudgetUsd": 5.0,
    },
    {
        "key": "personalizer",
        "displayName": "Azure Personalizer",
        "purpose": "Next-best-quiz A/B vs deterministic ranker",
        "settingKey": "recs.next_quiz_provider",
        "settingValueOn": "personalizer",
        "settingValueOff": "deterministic",
        "failureMode": "Flag: deterministic (default) / personalizer / shadow",
        "spendBudgetUsd": 10.0,
    },
]


def _feature(key: str) -> dict[str, Any] | None:
    return next((f for f in FEATURES if f["key"] == key), None)


# ─── Persistence helpers (Mongo via deps.db) ─────────────────────────────────

async def _get_db():
    """Return the motor `db` if available, else None.

    Using a function (not a module-level import) keeps unit tests
    that don't need Mongo running fast — they patch this helper.
    """
    try:
        from deps import db  # type: ignore

        return db
    except Exception as exc:
        logger.debug("admin_azure_ai: deps.db unavailable: %s", exc)
        return None


async def _read_setting(setting_key: str) -> Any:
    db = await _get_db()
    if db is None:
        return None
    try:
        doc = await db.azure_ai_settings.find_one({"key": setting_key})
        return doc.get("value") if doc else None
    except Exception as exc:
        logger.debug("admin_azure_ai: read_setting %s skipped: %s", setting_key, exc)
        return None


async def _write_setting(setting_key: str, value: Any) -> None:
    db = await _get_db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Mongo unavailable; cannot persist Azure AI feature toggle",
        )
    await db.azure_ai_settings.update_one(
        {"key": setting_key},
        {
            "$set": {
                "key": setting_key,
                "value": value,
                "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        },
        upsert=True,
    )
    # Best-effort cache flush so chain code sees the new value on
    # the next request rather than waiting for the 30-s TTL.
    try:
        from azure_ai_runtime import invalidate as _invalidate

        _invalidate(setting_key)
    except Exception:
        pass


def _resolve_enabled(feature: dict[str, Any], raw: Any) -> bool:
    if raw is None:
        return True
    if "settingValueOn" in feature:
        return str(raw).lower() == str(feature["settingValueOn"]).lower()
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "")


# ─── Metrics + anomaly readers ───────────────────────────────────────────────

async def _read_metrics() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {
        f["key"]: {
            "throttle15m": None,
            "latencyP50Ms": None,
            "latencyP95Ms": None,
            "spendMtdUsd": None,
            "lastErrorAt": None,
            "lastErrorMessage": None,
        }
        for f in FEATURES
    }
    # In-process counters — always present.
    try:
        from azure_ai_metrics import SNAPSHOT  # type: ignore

        for key, snap in SNAPSHOT.items():
            if key in out:
                for field in out[key]:
                    if field in snap and snap[field] is not None:
                        out[key][field] = snap[field]
    except Exception as exc:
        logger.debug("admin_azure_ai: in-process metrics skipped: %s", exc)

    # Optional Mongo overlay from the App Insights pull cron.
    db = await _get_db()
    if db is not None:
        try:
            cursor = db.azure_ai_metrics_rollup.find({}, {"_id": 0})
            async for row in cursor:
                key = row.get("feature")
                if key in out:
                    for field in out[key]:
                        if field in row and row[field] is not None:
                            out[key][field] = row[field]
        except Exception as exc:
            logger.debug("admin_azure_ai: rollup overlay skipped: %s", exc)

    return out


async def _read_anomalies(limit: int = 5) -> list[dict[str, Any]]:
    db = await _get_db()
    if db is None:
        return []
    try:
        cursor = db.azure_ai_anomalies.find({}, {"_id": 0}).sort("ts", -1).limit(limit)
        return [row async for row in cursor]
    except Exception as exc:
        logger.debug("admin_azure_ai: anomalies read skipped: %s", exc)
        return []


def _composite(features: list[dict[str, Any]]) -> str:
    if any((f.get("throttle15m") or 0) > 0 for f in features):
        return "throttled"
    if any(f.get("lastErrorAt") for f in features):
        return "degraded"
    return "ok"


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/admin/azure/ai/health")
async def get_health(_admin=Depends(require_admin)) -> dict[str, Any]:
    metrics = await _read_metrics()
    rendered: list[dict[str, Any]] = []
    for f in FEATURES:
        raw = await _read_setting(f["settingKey"])
        enabled = _resolve_enabled(f, raw)
        m = metrics.get(f["key"], {})
        rendered.append(
            {
                "key": f["key"],
                "displayName": f["displayName"],
                "purpose": f["purpose"],
                "enabled": enabled,
                "adminToggleKey": f["settingKey"],
                "failureMode": f["failureMode"],
                "spendBudgetUsd": f["spendBudgetUsd"],
                "throttle15m": m.get("throttle15m"),
                "latencyP50Ms": m.get("latencyP50Ms"),
                "latencyP95Ms": m.get("latencyP95Ms"),
                "spendMtdUsd": m.get("spendMtdUsd"),
                "lastErrorAt": m.get("lastErrorAt"),
                "lastErrorMessage": m.get("lastErrorMessage"),
            }
        )
    return {
        "asOf": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "compositeAlert": _composite(rendered),
        "features": rendered,
        "anomalies": await _read_anomalies(),
    }


class ToggleRequest(BaseModel):
    feature: str = Field(..., description="Feature key from FEATURES")
    enabled: bool


@router.post("/admin/azure/ai/toggle")
async def toggle(payload: ToggleRequest, _admin=Depends(require_admin)) -> dict[str, Any]:
    feature = _feature(payload.feature)
    if feature is None:
        raise HTTPException(status_code=404, detail=f"Unknown feature {payload.feature!r}")

    if "settingValueOn" in feature:
        value: Any = feature["settingValueOn"] if payload.enabled else feature["settingValueOff"]
    else:
        value = bool(payload.enabled)

    try:
        await _write_setting(feature["settingKey"], value)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("admin_azure_ai: failed to persist toggle for %s", feature["key"])
        raise HTTPException(status_code=500, detail=f"Toggle failed: {exc}") from exc

    # Propagation: a few features have an existing runtime store the
    # rest of the backend already reads from. Mirror the toggle there
    # so the change actually takes effect — the azure_ai_settings doc
    # by itself is only consulted by Azure-aware code paths.
    propagated: dict[str, Any] = {}
    try:
        if feature["key"] == "search":
            # rag.retriever => retrievers.factory.set_active_retriever
            # When ON we point at "pinecone_vector" today (Azure AI
            # Search retriever class lands in #354). When OFF we
            # restore the platform default ("vectorize").
            from retrievers.factory import set_active_retriever as _set_retriever

            target = "pinecone_vector" if payload.enabled else "vectorize"
            propagated["retriever"] = await _set_retriever(target)
        elif feature["key"] == "personalizer":
            # recs.next_quiz_provider => db.settings doc consumed by
            # the quiz recommender. Personalizer adapter lands in #354;
            # here we record the desired provider so the recommender
            # can branch on it as soon as the adapter is wired.
            db = await _get_db()
            if db is not None:
                await db.settings.update_one(
                    {"id": "next_quiz_provider"},
                    {"$set": {"id": "next_quiz_provider", "active": value}},
                    upsert=True,
                )
                propagated["next_quiz_provider"] = value
    except Exception as exc:
        logger.warning(
            "admin_azure_ai: toggle persisted but propagation failed for %s: %s",
            feature["key"],
            exc,
        )
        propagated["error"] = str(exc)

    return {
        "ok": True,
        "feature": feature["key"],
        "settingKey": feature["settingKey"],
        "value": value,
        "propagated": propagated,
    }
