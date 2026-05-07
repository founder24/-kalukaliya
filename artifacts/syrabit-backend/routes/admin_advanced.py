"""Syrabit.ai — SEO, referrals, vector, RAG, billing, pipeline auto-generate"""
import re, json, asyncio, uuid, logging, hashlib, os, httpx
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from fastapi import (
    APIRouter, HTTPException, Depends, Query, Body, BackgroundTasks, Request,
)
from pydantic import BaseModel

from deps import (
    db,
    is_mongo_available,
)
from cache import (
    _invalidate_content_cache,
    _redis_get,
    _redis_set,
)
from auth_deps import (
    get_admin_user,
)
from db_ops import supa_list_users
from llm import call_llm_api_content, _call_llm_raw, polish_notes_with_vertex, polish_notes_with_format
from seo_engine import _normalize_headings
from rag import (
    _embed_and_store_chapter,
    _embed_and_store_page,
    _embed_cms_document,
    auto_chunk_content,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────
# Task #731 S3 — Stripe-aware revenue rollups
# ─────────────────────────────────────────────
def _row_inr_mongo_expr() -> dict:
    """Mongo aggregation `$expr` mirror of `_row_inr()` Python semantics.

    Used by `_sum_revenue_inr` and `_aggregate_currency_breakdown` so
    the server-side INR sum matches the Python fallback ladder exactly:

      1. `amount_inr` if numeric and >= 0     →  use as-is
      2. else, non-stripe rows               →  `amount_paise / 100`
      3. otherwise                            →  0.0
    """
    is_numeric_amount_inr = {
        "$in": [
            {"$type": "$amount_inr"},
            ["double", "int", "long", "decimal"],
        ]
    }
    return {
        "$cond": [
            {"$and": [is_numeric_amount_inr,
                      {"$gte": [{"$ifNull": ["$amount_inr", -1]}, 0]}]},
            {"$toDouble": "$amount_inr"},
            {"$cond": [
                {"$ne": [{"$ifNull": ["$provider", ""]}, "stripe"]},
                {"$divide": [
                    {"$toDouble": {"$ifNull": ["$amount_paise", 0]}},
                    100.0,
                ]},
                0.0,
            ]},
        ]
    }


async def _sum_revenue_inr(start_iso: Optional[str] = None) -> float:
    """Sum INR revenue across `db.payments` using a server-side
    aggregation pipeline so the result is NEVER truncated by a
    client-side document-count cap.

    The previous implementation pulled `to_list(5000)` and summed in
    Python, which silently undercounted revenue (and therefore ARPU)
    once monthly payment volume passed ~5000 rows. At the current
    product trajectory that threshold is crossed quickly, so the
    revenue tiles must use a $sum aggregation.

    Args:
      start_iso: ISO-8601 string. If provided, only payments with
                 `verified_at >= start_iso` are summed. `verified_at`
                 is stored as an ISO-8601 string, so lexicographic
                 `$gte` is the correct comparator.
    """
    pipeline: List[dict] = []
    if start_iso:
        pipeline.append({"$match": {"verified_at": {"$gte": start_iso}}})
    pipeline += [
        {"$project": {"_id": 0, "row_inr": _row_inr_mongo_expr()}},
        {"$group": {"_id": None, "total": {"$sum": "$row_inr"}}},
    ]
    docs = await db.payments.aggregate(pipeline).to_list(1)
    return round(float(docs[0]["total"]) if docs else 0.0, 2)


async def _aggregate_currency_breakdown(start_iso: Optional[str] = None) -> dict:
    """Aggregation-based equivalent of the in-memory `_currency_breakdown`,
    so the per-currency breakdown stays correct at any payment volume.

    Splits payments into "USD-native" and "INR-native" buckets using the
    same predicate as `_currency_breakdown` (currency_original=='USD'
    OR a Stripe row whose currency is anything other than INR) and then
    grabs the most-recent USD-native row's FX provenance for the
    "rate as of payment-time" caption.
    """
    base_match: dict = {}
    if start_iso:
        base_match = {"verified_at": {"$gte": start_iso}}

    is_usd_expr = {
        "$or": [
            {"$eq": [{"$toUpper": {"$ifNull": ["$currency_original", ""]}}, "USD"]},
            {"$and": [
                {"$eq": [{"$toLower": {"$ifNull": ["$provider", ""]}}, "stripe"]},
                {"$ne": [{"$toUpper": {"$ifNull": ["$currency_original", ""]}}, "INR"]},
            ]},
        ]
    }
    # USD-native amount: prefer cents/100, fall back to amount_original
    usd_native_expr = {
        "$cond": [
            {"$gt": [{"$toDouble": {"$ifNull": ["$amount_cents", 0]}}, 0]},
            {"$divide": [{"$toDouble": {"$ifNull": ["$amount_cents", 0]}}, 100.0]},
            {"$toDouble": {"$ifNull": ["$amount_original", 0]}},
        ]
    }

    pipeline: List[dict] = []
    if base_match:
        pipeline.append({"$match": base_match})
    pipeline += [
        {"$project": {
            "_id": 0,
            "verified_at": 1,
            "fx_rate": 1, "fx_source": 1, "fx_fetched_at": 1,
            "is_usd": is_usd_expr,
            "row_inr": _row_inr_mongo_expr(),
            "usd_native": usd_native_expr,
        }},
        {"$facet": {
            "inr_native": [
                {"$match": {"is_usd": False}},
                {"$group": {"_id": None, "total": {"$sum": "$row_inr"}}},
            ],
            "usd": [
                {"$match": {"is_usd": True}},
                {"$group": {
                    "_id": None,
                    "usd_native": {"$sum": "$usd_native"},
                    "inr_from_usd": {"$sum": "$row_inr"},
                }},
            ],
            "latest_usd": [
                {"$match": {"is_usd": True}},
                {"$sort": {"verified_at": -1}},
                {"$limit": 1},
                {"$project": {"_id": 0, "fx_rate": 1, "fx_source": 1, "fx_fetched_at": 1}},
            ],
        }},
    ]

    facet_docs = await db.payments.aggregate(pipeline).to_list(1)
    facet = facet_docs[0] if facet_docs else {}

    inr_native_arr = facet.get("inr_native") or []
    usd_arr = facet.get("usd") or []
    latest_arr = facet.get("latest_usd") or []

    inr_native = (inr_native_arr[0]["total"] if inr_native_arr else 0.0) or 0.0
    usd_native = (usd_arr[0].get("usd_native", 0.0) if usd_arr else 0.0) or 0.0
    inr_from_usd = (usd_arr[0].get("inr_from_usd", 0.0) if usd_arr else 0.0) or 0.0
    latest = latest_arr[0] if latest_arr else {}

    return {
        "inr_native":   round(float(inr_native), 2),
        "usd_native":   round(float(usd_native), 2),
        "inr_from_usd": round(float(inr_from_usd), 2),
        "fx_rate":      latest.get("fx_rate"),
        "fx_source":    latest.get("fx_source"),
        "fx_fetched_at": latest.get("fx_fetched_at"),
    }


def _currency_breakdown(payments: list) -> dict:
    """Task #740 — per-currency native totals + FX provenance metadata.

    Returns a dict the frontend uses to render the same
    "Includes: Razorpay (₹X) + Stripe (USD $Y → ₹Z @ rate R, source: …)"
    caption that Monetization tab already shows. Schema is
    forward-compatible (only INR + USD slices populated for v1).

    Fields:
      inr_native:     float, sum of Razorpay/INR-native amounts in INR
      usd_native:     float, sum of Stripe USD amounts in USD (original)
      inr_from_usd:   float, INR equivalent of usd_native at captured rates
      fx_rate:        float|None, FX rate from the most recent USD row
      fx_source:      str|None,  e.g. "frankfurter", "open_er_api", "..._stale"
      fx_fetched_at:  str|None, ISO timestamp of when that rate was captured
    """
    inr_native = 0.0
    usd_native = 0.0
    inr_from_usd = 0.0
    latest_fx_row = None
    for p in payments or []:
        currency_original = (p.get("currency_original") or "").upper()
        provider = (p.get("provider") or "").lower()
        amount_inr = _row_inr(p)
        is_usd = currency_original == "USD" or (
            provider == "stripe" and currency_original != "INR"
        )
        if is_usd:
            cents = p.get("amount_cents") or 0
            if isinstance(cents, (int, float)) and cents:
                usd_native += float(cents) / 100.0
            else:
                usd_native += float(p.get("amount_original") or 0)
            inr_from_usd += amount_inr
            ts = p.get("verified_at") or ""
            if not latest_fx_row or ts > (latest_fx_row.get("verified_at") or ""):
                latest_fx_row = p
        else:
            inr_native += amount_inr

    out = {
        "inr_native":   round(inr_native, 2),
        "usd_native":   round(usd_native, 2),
        "inr_from_usd": round(inr_from_usd, 2),
        "fx_rate":      None,
        "fx_source":    None,
        "fx_fetched_at": None,
    }
    if latest_fx_row:
        out["fx_rate"]       = latest_fx_row.get("fx_rate")
        out["fx_source"]     = latest_fx_row.get("fx_source")
        out["fx_fetched_at"] = latest_fx_row.get("fx_fetched_at")
    return out


def _row_inr(p: dict) -> float:
    """Best-effort INR rupees for one payment row.

    Order of preference:
      1. Persisted `amount_inr` (set by `_enrich_payment_record` at
         insert time post-S2, OR by the backfill migration). This is
         always the right number — for Stripe rows it's the live USD->INR
         FX as of the moment of payment, captured alongside fx_rate +
         fx_source on the row itself.
      2. For Razorpay rows that pre-date S2 + the migration, fall back
         to `amount_paise / 100` since paise->INR is an identity.
      3. For Stripe rows without persisted amount_inr we return 0
         (instead of guessing with today's FX). The migration script
         backfills these idempotently — once it runs, this branch
         disappears.
    """
    v = p.get("amount_inr")
    if isinstance(v, (int, float)) and v >= 0:
        return float(v)
    if p.get("provider") != "stripe":
        paise = p.get("amount_paise") or 0
        if isinstance(paise, (int, float)):
            return float(paise) / 100.0
    return 0.0


@router.get("/admin/monetization/overview")
async def admin_monetization_overview(admin: dict = Depends(get_admin_user)):
    users = await supa_list_users()

    now = datetime.now(timezone.utc)
    thirty_ago = (now - timedelta(days=30)).isoformat()
    seven_ago = (now - timedelta(days=7)).isoformat()

    # Stripe-aware revenue rollups via server-side $sum aggregations.
    # Previously this loaded `db.payments.find().to_list(5000)` and
    # summed in Python — once monthly volume crosses ~5000 rows that
    # cap silently undercounts revenue (and therefore ARPU + the
    # currency_breakdown caption). All scalar revenue numbers + the
    # currency breakdown are now computed in the database; only the
    # 20-row "recent transactions" tail is materialised in Python.
    revenue_30d, revenue_7d, total_lifetime_revenue, currency_breakdown, recent_payments = await asyncio.gather(
        _sum_revenue_inr(thirty_ago),
        _sum_revenue_inr(seven_ago),
        _sum_revenue_inr(None),
        _aggregate_currency_breakdown(None),
        db.payments.find({}, {"_id": 0}).sort("verified_at", -1).to_list(20),
    )

    # Plan-based counts include Stripe payers (Stripe webhook flips
    # users to plan="starter"/"pro" on success), so this is the same
    # paid-user universe as `revenue_30d`'s numerator.
    starter_count = sum(1 for u in users if u.get("plan") == "starter")
    pro_count = sum(1 for u in users if u.get("plan") == "pro")
    total_paid = starter_count + pro_count

    arpu = round(revenue_30d / max(total_paid, 1), 2)

    recent_txns = []
    for p in recent_payments:
        amount_inr = _row_inr(p)
        provider = p.get("provider") or "razorpay"
        # Original-currency display values for the "shown alongside INR"
        # caption — preserves the receipt-of-record amount even for
        # Stripe rows where the live INR comes from FX conversion.
        if provider == "stripe":
            amount_original = (p.get("amount_cents", 0) or 0) / 100
            currency_original = (p.get("currency_original") or p.get("currency") or "USD").upper()
        else:
            amount_original = (p.get("amount_paise", 0) or 0) / 100
            currency_original = p.get("currency_original") or "INR"
        recent_txns.append({
            "user_id":           p.get("user_id", ""),
            "plan":              p.get("plan", ""),
            "amount_inr":        amount_inr,
            "amount":            amount_inr,            # back-compat alias for older UI builds
            "amount_original":   round(amount_original, 2),
            "currency_original": currency_original,
            "currency":          "INR",                  # primary display currency
            "fx_rate":           p.get("fx_rate"),
            "fx_source":         p.get("fx_source"),
            "fx_fetched_at":     p.get("fx_fetched_at"),
            "provider":          provider,
            "date":              (p.get("verified_at") or "")[:10],
        })

    return {
        "revenue_30d_inr": revenue_30d,
        "revenue_7d_inr": revenue_7d,
        "arpu_inr": arpu,
        "total_paid_users": total_paid,
        "starter_users": starter_count,
        "pro_users": pro_count,
        "total_free_users": len(users) - total_paid,
        "conversion_rate": round(total_paid / max(len(users), 1) * 100, 2),
        "recent_transactions": recent_txns,
        "total_lifetime_revenue_inr": round(sum(_row_inr(p) for p in payments), 2),
        # Honest provenance caption — frontend renders "Includes:
        # Razorpay + Stripe (USD->INR @ rate as of payment-time)" under
        # revenue tiles using these flags (S9).
        "revenue_includes_stripe": True,
        "revenue_basis": "amount_inr_at_payment_time",
        # Task #740 — per-currency breakdown so the "Includes: Razorpay
        # + Stripe (USD→INR @ rate …)" caption can render the actual
        # numbers, not just an opaque "Includes Stripe" boolean.
        "currency_breakdown": _currency_breakdown(payments),
    }

@router.get("/admin/monetization/referrals")
async def admin_monetization_referrals(admin: dict = Depends(get_admin_user)):
    referrals = await db.referrals.find({}, {"_id": 0}).to_list(500)
    return {
        "total_referrals": len(referrals),
        "successful_conversions": sum(1 for r in referrals if r.get("converted")),
        "referrals": referrals[:50],
    }

class ReferralConfigUpdate(BaseModel):
    enabled: bool = True
    reward_credits: int = 10
    referrer_credits: int = 10

@router.put("/admin/monetization/referral-config")
async def admin_update_referral_config(body: ReferralConfigUpdate, admin: dict = Depends(get_admin_user)):
    await db.api_config.update_one(
        {},
        {"$set": {"referral": body.dict()}},
        upsert=True,
    )
    return {"success": True}

@router.get("/admin/monetization/referral-config")
async def admin_get_referral_config(admin: dict = Depends(get_admin_user)):
    cfg = await db.api_config.find_one({}, {"_id": 0})
    return cfg.get("referral", {"enabled": False, "reward_credits": 10, "referrer_credits": 10}) if cfg else {"enabled": False, "reward_credits": 10, "referrer_credits": 10}


# ═══════════════════════════════════════════════════════════════════════════
# UPGRADE WAVE — ALL 12 MAJOR FEATURES
# ═══════════════════════════════════════════════════════════════════════════

# ── T001: Internal Linking Engine ────────────────────────────────────────────

@router.get("/admin/seo/internal-links/analyze")
async def seo_internal_links_analyze(admin: dict = Depends(get_admin_user)):
    """Analyze all published topics and return semantic link suggestions using embeddings."""
    topics = await db.seo_topics.find(
        {"status": "published"},
        {"_id": 0, "slug": 1, "title": 1, "subject_name": 1, "class_name": 1}
    ).to_list(500)

    if not topics:
        return {"links": [], "topics_analyzed": 0}

    suggestions = []
    try:
        import vertex_services
        titles = [t["title"] for t in topics]
        vecs = await vertex_services.embed_batch(titles)

        for i, (topic, vec_i) in enumerate(zip(topics, vecs)):
            if vec_i is None:
                continue
            scores = []
            for j, (other, vec_j) in enumerate(zip(topics, vecs)):
                if i == j or vec_j is None:
                    continue
                sim = vertex_services.cosine_similarity(vec_i, vec_j)
                if sim > 0.65:
                    scores.append({"slug": other["slug"], "title": other["title"], "score": round(sim, 3)})
            scores.sort(key=lambda x: x["score"], reverse=True)
            if scores:
                suggestions.append({
                    "slug": topic["slug"],
                    "title": topic["title"],
                    "subject": topic.get("subject_name", ""),
                    "related": scores[:5],
                })
    except Exception as e:
        logger.warning(f"internal-links analyze failed: {e}")

    return {"links": suggestions, "topics_analyzed": len(topics)}


@router.post("/admin/seo/internal-links/inject/{slug}")
async def seo_internal_links_inject(slug: str, admin: dict = Depends(get_admin_user)):
    """Inject internal links into a topic's generated content and track them."""
    topic = await db.seo_topics.find_one({"slug": slug})
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    pages = await db.seo_pages.find({"topic_id": str(topic.get("_id", ""))}).to_list(20)
    if not pages:
        raise HTTPException(status_code=404, detail="No pages found for this topic")

    all_topics = await db.seo_topics.find(
        {"status": "published", "slug": {"$ne": slug}},
        {"slug": 1, "title": 1}
    ).to_list(200)

    injected_count = 0
    injected_links = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for page in pages[:5]:
        content = page.get("content", "")
        if not content:
            continue
        page_id = str(page.get("_id", ""))
        for related in all_topics[:10]:
            r_title = related.get("title", "")
            r_slug = related.get("slug", "")
            if r_title.lower() in content.lower() and f"[{r_title}]" not in content:
                target_url = f"/learn/{r_slug}"
                old_content = content
                content = re.sub(
                    re.escape(r_title),
                    f"[{r_title}]({target_url})",
                    content,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if content != old_content:
                    injected_count += 1
                    injected_links.append({
                        "source_page_id": page_id,
                        "source_slug": slug,
                        "target_slug": r_slug,
                        "target_url": target_url,
                        "target_title": r_title,
                        "injection_date": now_iso,
                        "status": "active",
                    })
        await db.seo_pages.update_one(
            {"_id": page["_id"]},
            {"$set": {"content": content, "internal_links_injected": True, "links_updated_at": now_iso}}
        )

    for link in injected_links:
        await db.seo_internal_links.update_one(
            {"source_page_id": link["source_page_id"], "source_slug": link["source_slug"], "target_slug": link["target_slug"]},
            {"$set": link},
            upsert=True,
        )

    return {"slug": slug, "pages_updated": len(pages), "links_injected": injected_count}


@router.get("/admin/seo/internal-links/validate")
async def seo_internal_links_validate(admin: dict = Depends(get_admin_user)):
    """Validate all tracked internal links. Flag broken links where target is unpublished."""
    all_links = await db.seo_internal_links.find({}).to_list(5000)
    if not all_links:
        return {"total_links": 0, "valid": 0, "broken": 0, "broken_links": []}

    published_slugs = set()
    published_topics = await db.seo_topics.find(
        {"status": "published"}, {"_id": 0, "slug": 1}
    ).to_list(5000)
    for t in published_topics:
        published_slugs.add(t.get("slug", ""))

    valid_count = 0
    broken_count = 0
    broken_links = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for link in all_links:
        target_slug = link.get("target_slug", "")
        if target_slug in published_slugs:
            if link.get("status") != "active":
                await db.seo_internal_links.update_one(
                    {"_id": link["_id"]},
                    {"$set": {"status": "active", "validated_at": now_iso}}
                )
            valid_count += 1
        else:
            await db.seo_internal_links.update_one(
                {"_id": link["_id"]},
                {"$set": {"status": "broken", "validated_at": now_iso}}
            )
            broken_count += 1
            broken_links.append({
                "source_slug": link.get("source_slug", ""),
                "target_slug": target_slug,
                "target_url": link.get("target_url", ""),
                "injection_date": link.get("injection_date", ""),
            })

    return {
        "total_links": len(all_links),
        "valid": valid_count,
        "broken": broken_count,
        "broken_links": broken_links[:100],
    }


# ── T003: FAQ Auto-Extractor ──────────────────────────────────────────────────

@router.get("/admin/conversations/extract-faqs")
async def extract_faqs(limit: int = 100, admin: dict = Depends(get_admin_user)):
    """Extract recurring questions from conversations and suggest FAQ content."""
    pipeline = [
        {"$unwind": "$messages"},
        {"$match": {"messages.role": "user"}},
        {"$project": {"content": "$messages.content", "subject": "$subject_name"}},
        {"$limit": limit * 5},
    ]
    try:
        raw = await db.conversations.aggregate(pipeline).to_list(limit * 5)
    except Exception:
        raw = []

    questions = [r["content"] for r in raw if r.get("content") and len(r["content"]) > 15 and "?" in r["content"]][:50]
    subjects = list({r.get("subject", "") for r in raw if r.get("subject")})[:10]

    faqs = []
    if questions:
        try:
            import vertex_services
            prompt = (
                f"From these student questions, identify the top 15 most frequently asked and educationally important ones.\n"
                f"Questions:\n" + "\n".join(f"- {q[:200]}" for q in questions[:50]) +
                f"\n\nReturn a JSON array of: {{question, category, suggested_answer_length: 'short'|'medium'|'long', importance: 'high'|'medium'}}"
                f"\nReturn ONLY valid JSON array."
            )
            raw_result = await vertex_services._generate(prompt, max_tokens=1024)
            if raw_result:
                cleaned = raw_result.strip().lstrip("```json").lstrip("```").rstrip("```")
                faqs = json.loads(cleaned)
        except Exception as e:
            logger.warning(f"FAQ extraction AI failed: {e}")
            faqs = [{"question": q[:200], "category": "general", "importance": "medium"} for q in questions[:15]]

    return {
        "faqs": faqs,
        "total_questions_analyzed": len(questions),
        "subjects": subjects,
        "suggested_pages": [
            {"type": "faq", "title": f["question"][:80], "priority": f.get("importance", "medium")}
            for f in faqs[:10]
        ]
    }


@router.get("/admin/conversations/sentiment")
async def conversations_sentiment(admin: dict = Depends(get_admin_user)):
    """Quick sentiment summary across all recent conversations."""
    try:
        pipeline = [
            {"$unwind": "$messages"},
            {"$match": {"messages.role": "user"}},
            {"$project": {"content": "$messages.content", "conv_id": "$_id"}},
            {"$limit": 200},
        ]
        msgs = await db.conversations.aggregate(pipeline).to_list(200)
    except Exception:
        msgs = []

    anon_texts = []
    try:
        from cache import redis_list_all_anon_conversations
        anon_convs = redis_list_all_anon_conversations()
        for ac in anon_convs:
            for m in (ac.get("messages") or []):
                if m.get("role") == "user" and m.get("content"):
                    anon_texts.append(m["content"])
    except Exception:
        pass

    if not msgs and not anon_texts:
        return {"positive": 0, "negative": 0, "neutral": 0, "total": 0}

    texts = [m["content"] for m in msgs if m.get("content")] + anon_texts
    positive = sum(1 for t in texts if any(w in t.lower() for w in ["thank", "great", "awesome", "help", "good", "love", "clear", "easy"]))
    negative = sum(1 for t in texts if any(w in t.lower() for w in ["wrong", "bad", "error", "confused", "not working", "fail", "broken", "terrible"]))
    neutral = len(texts) - positive - negative
    return {
        "positive": positive,
        "negative": negative,
        "neutral": max(0, neutral),
        "total": len(texts),
        "positive_pct": round(positive / max(len(texts), 1) * 100, 1),
        "negative_pct": round(negative / max(len(texts), 1) * 100, 1),
    }


# ── T001b: Schema.org Auto-Injection ─────────────────────────────────────────

@router.post("/admin/seo/inject-schema/{slug}")
async def seo_inject_schema(slug: str, admin: dict = Depends(get_admin_user)):
    """Inject JSON-LD schema.org structured data into a topic's pages."""
    topic = await db.seo_topics.find_one({"slug": slug})
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    schema = {
        "@context": "https://schema.org",
        "@type": "Course",
        "name": topic.get("title", ""),
        "description": topic.get("meta_description", topic.get("title", "")),
        "provider": {"@type": "Organization", "name": "Syrabit.ai", "url": "https://syrabit.ai"},
        "educationalLevel": topic.get("class_name", ""),
        "about": topic.get("subject_name", ""),
        "keywords": topic.get("keywords", []),
        "inLanguage": "en-IN",
        "isPartOf": {"@type": "LearningResource", "name": f"AHSEC {topic.get('class_name', '')} {topic.get('subject_name', '')}"},
    }

    faq_schema = None
    pages = await db.seo_pages.find({"topic_id": str(topic.get("_id", ""))}).to_list(50)
    faqs = []
    for page in pages:
        if page.get("type") in ("important-questions", "mcqs"):
            content = page.get("content", "")
            questions = re.findall(r'#{1,3}\s+(.+?)\n', content)[:5]
            for q in questions:
                faqs.append({"@type": "Question", "name": q.strip(),
                              "acceptedAnswer": {"@type": "Answer", "text": f"Refer to Syrabit.ai for a detailed answer on {q.strip()}."}})
    if faqs:
        faq_schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": faqs}

    await db.seo_topics.update_one(
        {"slug": slug},
        {"$set": {"schema_org": schema, "faq_schema": faq_schema, "schema_injected_at": datetime.now(timezone.utc).isoformat()}}
    )

    return {"slug": slug, "schema_injected": True, "faq_entities": len(faqs), "schema": schema}


@router.post("/admin/seo/inject-schema-bulk")
async def seo_inject_schema_bulk(admin: dict = Depends(get_admin_user)):
    """Inject schema.org into all published topics."""
    topics = await db.seo_topics.find({"status": "published"}, {"slug": 1}).to_list(1000)
    injected = 0
    for t in topics:
        try:
            await seo_inject_schema(t["slug"], admin)
            injected += 1
        except Exception:
            pass
    return {"injected": injected, "total": len(topics)}


# ── T008: Content Pipeline Tracker ───────────────────────────────────────────

@router.get("/admin/seo/pipeline-status")
async def seo_pipeline_status(admin: dict = Depends(get_admin_user)):
    """Get real-time content pipeline statistics with thin-page and sitemap tracking."""
    try:
        total         = await db.topics.count_documents({})
        published     = await db.topics.count_documents({"status": "published"})
        draft         = await db.topics.count_documents({"status": "draft"})
        archived      = await db.topics.count_documents({"status": "archived"})
        has_content   = await db.topics.count_documents({"has_content": True})
        no_schema     = await db.topics.count_documents({"status": "published", "schema_org": {"$exists": False}})
        no_links      = await db.topics.count_documents({"status": "published", "internal_links_injected": {"$ne": True}})

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        published_today = await db.topics.count_documents({
            "status": "published",
            "published_at": {"$gte": today.isoformat()}
        })
        pages_total     = await db.seo_pages.count_documents({})
        pages_published = await db.seo_pages.count_documents({"status": "published"})
        pages_draft     = await db.seo_pages.count_documents({"status": "draft"})
        pages_rejected  = await db.seo_pages.count_documents({"status": "rejected"})

        thin_pages = await db.seo_pages.count_documents({
            "status": {"$in": ["published", "draft"]},
            "$or": [
                {"quality.word_count": {"$lt": 300}},
                {"word_count": {"$lt": 300}, "quality": {"$exists": False}},
            ]
        })
        high_quality = await db.seo_pages.count_documents({
            "status": "published",
            "$or": [
                {"quality.score": {"$gte": 70}},
                {"quality_score.score": {"$gte": 70}, "quality": {"$exists": False}},
            ]
        })

        cms_total     = await db.cms_documents.count_documents({})
        cms_published = await db.cms_documents.count_documents({"status": "published"})
        cms_with_jsonld = await db.cms_documents.count_documents({"json_ld_breadcrumb": {"$exists": True}})

        sitemap_indexed = await db.seo_pages.count_documents({"in_sitemap": True})

        broken_links = await db.seo_internal_links.count_documents({"status": "broken"})
        total_tracked_links = await db.seo_internal_links.count_documents({})

        return {
            "total_topics": total,
            "published": published,
            "draft": draft,
            "archived": archived,
            "has_content": has_content,
            "pages_total": pages_total,
            "pages_published": pages_published,
            "pages_draft": pages_draft,
            "pages_rejected": pages_rejected,
            "published_today": published_today,
            "needs_schema": no_schema,
            "needs_internal_links": no_links,
            "thin_pages": thin_pages,
            "high_quality_pages": high_quality,
            "cms_total": cms_total,
            "cms_published": cms_published,
            "cms_with_jsonld": cms_with_jsonld,
            "sitemap_indexed": sitemap_indexed,
            "broken_links": broken_links,
            "total_tracked_links": total_tracked_links,
            "publish_rate_pct": round(published / max(total, 1) * 100, 1),
            "content_rate_pct": round(has_content / max(total, 1) * 100, 1),
            "quality_rate_pct": round(high_quality / max(pages_published, 1) * 100, 1),
        }
    except Exception as e:
        logger.warning(f"pipeline-status failed: {e}")
        return {}


# ── T009: Page-Level Conversion Tracker ──────────────────────────────────────

@router.get("/admin/analytics/page-conversions")
async def admin_page_conversions(days: int = 30, admin: dict = Depends(get_admin_user)):
    """Track which content pages correlate with user signups and upgrades."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Top viewed pages (from page_views collection — JS-tracked)
    view_pipeline = [
        {"$match": {"date": {"$gte": cutoff[:10]}, "is_bot": {"$ne": True}}},
        {"$group": {"_id": "$path", "views": {"$sum": 1}, "unique_visitors": {"$addToSet": "$visitor_id"}}},
        {"$project": {"path": "$_id", "views": 1, "unique_visitors": {"$size": "$unique_visitors"}}},
        {"$sort": {"views": -1}},
        {"$limit": 20},
    ]
    try:
        pages = await db.page_views.aggregate(view_pipeline).to_list(20)
    except Exception:
        pages = []

    # Signups by source — use Supabase users created in period
    signup_sources = []
    try:
        users = await supa_list_users()
        recent_users = [u for u in users if (u.get("created_at") or "") >= cutoff]
        signup_sources = [{"_id": "direct", "signups": len(recent_users)}]
    except Exception:
        pass

    enriched = []
    signup_map = {s["_id"]: s["signups"] for s in signup_sources}
    for p in pages:
        path = p.get("path", "") or p.get("_id", "")
        enriched.append({
            "path": path,
            "views": p.get("views", 0),
            "unique_visitors": p.get("unique_visitors", 0),
            "signups_attributed": signup_map.get(path, 0),
            "conversion_rate": round(signup_map.get(path, 0) / max(p.get("unique_visitors", 1), 1) * 100, 2),
        })

    enriched.sort(key=lambda x: x["signups_attributed"], reverse=True)

    # Daily signups trend (from Supabase users — reuse 'users' from above)
    daily_signups_map = {}
    try:
        _all_u = users if 'users' in locals() and users else await supa_list_users()
        for u in _all_u:
            d = (u.get("created_at") or "")[:10]
            if d >= cutoff[:10]:
                daily_signups_map[d] = daily_signups_map.get(d, 0) + 1
    except Exception:
        pass

    # Task #740 — populate revenue_attributed (the frontend tile expected
    # this field but it was always 0). For v1, "attributed" = total
    # revenue in the same period, since per-page attribution is not
    # tracked. The currency_breakdown lets the UI render the same
    # provenance caption as Monetization.
    #
    # Both numbers come from server-side aggregations rather than a
    # `to_list(5000)` Python sum — `cutoff` can stretch back arbitrarily
    # far via the `days` query param, and at full payment volume that
    # cap silently truncated revenue_attributed (and the per-currency
    # breakdown rendered alongside it).
    revenue_attributed, currency_breakdown_period = await asyncio.gather(
        _sum_revenue_inr(cutoff),
        _aggregate_currency_breakdown(cutoff),
    )

    return {
        "top_converting_pages": enriched,
        "daily_signups": sorted(
            [{"date": d, "signups": c} for d, c in daily_signups_map.items()],
            key=lambda x: x["date"],
        ),
        "period_days": days,
        "revenue_attributed": revenue_attributed,
        "currency_breakdown": currency_breakdown_period,
    }


# ── T010: Churn Risk Scoring ──────────────────────────────────────────────────

@router.get("/admin/users/churn-risk")
async def admin_churn_risk(admin: dict = Depends(get_admin_user)):
    """Score every user's churn risk based on activity, credits, and plan age."""
    users = await supa_list_users()
    now = datetime.now(timezone.utc)
    at_risk = []

    for u in users:
        score = 0
        factors = []

        created = u.get("created_at", "")
        last_active = u.get("updated_at", created)
        try:
            la_dt = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
            days_inactive = (now - la_dt).days
        except Exception:
            days_inactive = 0

        if days_inactive > 14:
            score += 30
            factors.append(f"Inactive {days_inactive}d")
        elif days_inactive > 7:
            score += 15
            factors.append(f"Inactive {days_inactive}d")

        credits_used = u.get("credits_used", 0) or 0
        if credits_used == 0:
            score += 25
            factors.append("Never used AI")
        elif credits_used < 3:
            score += 10
            factors.append("Low engagement")

        plan = u.get("plan", "free")
        if plan == "free" and days_inactive > 3:
            score += 15
            factors.append("Free + inactive")

        conv_count = u.get("conversation_count", 0) or 0
        if conv_count == 0:
            score += 20
            factors.append("No conversations")

        risk = "high" if score >= 60 else "medium" if score >= 30 else "low"
        at_risk.append({
            "id": u.get("id"), "name": u.get("name", ""), "email": u.get("email", ""),
            "plan": plan, "credits_used": credits_used, "days_inactive": days_inactive,
            "risk_score": score, "risk": risk, "factors": factors,
        })

    at_risk.sort(key=lambda x: x["risk_score"], reverse=True)
    return {
        "users": at_risk[:50],
        "summary": {
            "high_risk": sum(1 for u in at_risk if u["risk"] == "high"),
            "medium_risk": sum(1 for u in at_risk if u["risk"] == "medium"),
            "low_risk": sum(1 for u in at_risk if u["risk"] == "low"),
            "total": len(at_risk),
        }
    }


# ── T011: LLM Cost Tracker ────────────────────────────────────────────────────

_llm_cost_log: list = []   # in-memory ring buffer (max 10k entries)
_LLM_COST_MAX = 10_000

COST_PER_1K_TOKENS = {
    "gemini-2.5-flash":       {"in": 0.00015, "out": 0.0006},
    "meta-llama/llama-4-scout-17b-16e-instruct": {"in": 0.00011, "out": 0.00034},
    "meta-llama/llama-4-scout": {"in": 0.00015, "out": 0.00040},
    "llama3.1-8b":            {"in": 0.00005,   "out": 0.00008},
    "sarvam-m":               {"in": 0.00008,   "out": 0.00024},
    "llama-3.3-70b-versatile":{"in": 0.00059,   "out": 0.00079},
    "llama-3.1-8b-instant":   {"in": 0.00005,   "out": 0.00008},
}

def record_llm_cost(model: str, prompt_tokens: int, completion_tokens: int, provider: str = "gemini", user_id: str = ""):
    rates = COST_PER_1K_TOKENS.get(model, {"in": 0.0001, "out": 0.0002})
    cost_usd = (prompt_tokens * rates["in"] + completion_tokens * rates["out"]) / 1000
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model, "provider": provider,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "cost_usd": round(cost_usd, 8),
        "user_id": user_id,
    }
    _llm_cost_log.append(entry)
    if len(_llm_cost_log) > _LLM_COST_MAX:
        _llm_cost_log.pop(0)

@router.get("/admin/health/llm-costs")
async def admin_llm_costs(days: int = 7, admin: dict = Depends(get_admin_user)):
    """Return LLM cost breakdown for the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = [e for e in _llm_cost_log if datetime.fromisoformat(e["ts"].replace("Z", "+00:00")) >= cutoff]

    total_cost = sum(e["cost_usd"] for e in recent)
    total_tokens = sum(e["prompt_tokens"] + e["completion_tokens"] for e in recent)

    by_model: dict = {}
    for e in recent:
        m = e["model"]
        by_model.setdefault(m, {"calls": 0, "cost_usd": 0, "tokens": 0})
        by_model[m]["calls"] += 1
        by_model[m]["cost_usd"] += e["cost_usd"]
        by_model[m]["tokens"] += e["prompt_tokens"] + e["completion_tokens"]

    by_day: dict = {}
    for e in recent:
        day = e["ts"][:10]
        by_day.setdefault(day, {"cost_usd": 0, "calls": 0})
        by_day[day]["cost_usd"] += e["cost_usd"]
        by_day[day]["calls"] += 1

    daily = [{"date": d, **v, "cost_usd": round(v["cost_usd"], 6)} for d, v in sorted(by_day.items())]

    published = await db.seo_topics.count_documents({"status": "published"})
    cost_per_page = round(total_cost / max(published, 1), 6)

    by_user: dict = {}
    for e in recent:
        uid = e.get("user_id", "anonymous") or "anonymous"
        by_user.setdefault(uid, {"calls": 0, "cost_usd": 0, "tokens": 0})
        by_user[uid]["calls"] += 1
        by_user[uid]["cost_usd"] += e["cost_usd"]
        by_user[uid]["tokens"] += e["prompt_tokens"] + e["completion_tokens"]

    top_users = sorted(by_user.items(), key=lambda x: -x[1]["cost_usd"])[:20]

    return {
        "period_days": days,
        "total_cost_usd": round(total_cost, 6),
        "total_cost_inr": round(total_cost * 84, 4),
        "total_tokens": total_tokens,
        "total_calls": len(recent),
        "cost_per_published_page_usd": cost_per_page,
        "by_model": [{"model": m, **v, "cost_usd": round(v["cost_usd"], 6)} for m, v in by_model.items()],
        "by_user": [{"user_id": uid, **v, "cost_usd": round(v["cost_usd"], 6)} for uid, v in top_users],
        "daily": daily,
    }


# ── T012: Notification Trigger Builder ───────────────────────────────────────

@router.get("/admin/notifications/triggers")
async def get_notification_triggers(admin: dict = Depends(get_admin_user)):
    """List all automated notification triggers."""
    triggers = await db.notification_triggers.find({}, {"_id": 0}).to_list(100)
    return {"triggers": triggers}


@router.post("/admin/notifications/triggers")
async def create_notification_trigger(body: dict = Body(...), admin: dict = Depends(get_admin_user)):
    """Create a new automated trigger."""
    required = {"name", "event", "channel", "message"}
    if not required.issubset(body.keys()):
        raise HTTPException(status_code=400, detail=f"Required fields: {required}")
    trigger = {
        "id": str(uuid.uuid4()),
        "name": body["name"],
        "event": body["event"],       # signup | inactive_3d | inactive_7d | plan_upgrade | low_credits
        "channel": body["channel"],   # push | email | both
        "message": body["message"],
        "subject": body.get("subject", ""),
        "enabled": body.get("enabled", True),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fired_count": 0,
    }
    await db.notification_triggers.insert_one({**trigger, "_id": trigger["id"]})
    return trigger


@router.patch("/admin/notifications/triggers/{trigger_id}")
async def update_notification_trigger(trigger_id: str, body: dict = Body(...), admin: dict = Depends(get_admin_user)):
    """Toggle or update a trigger."""
    await db.notification_triggers.update_one({"id": trigger_id}, {"$set": body})
    return {"success": True}


@router.delete("/admin/notifications/triggers/{trigger_id}")
async def delete_notification_trigger(trigger_id: str, admin: dict = Depends(get_admin_user)):
    """Delete a trigger."""
    await db.notification_triggers.delete_one({"id": trigger_id})
    return {"success": True}


# ── T007: Inline AI Writing — CMS suggest ────────────────────────────────────

@router.post("/admin/cms/ai-suggest")
async def cms_ai_suggest(
    text: str = Body(...),
    action: str = Body("improve"),   # improve | continue | summarise | simplify | exam-tip
    subject: str = Body(""),
    topic: str = Body(""),
    admin: dict = Depends(get_admin_user),
):
    """Inline Gemini AI writing assistance for CMS editor."""
    if not text or len(text.strip()) < 10:
        raise HTTPException(status_code=400, detail="Text too short")

    action_prompts = {
        "improve":   f"Rewrite this more clearly and professionally for AssamBoard students{' studying ' + subject if subject else ''}. Keep the same meaning, improve flow and clarity.",
        "continue":  f"Continue writing this educational content naturally for AssamBoard students{' studying ' + topic if topic else ''}. Add 2-3 more sentences.",
        "summarise": "Summarise this in 2-3 concise bullet points for quick revision.",
        "simplify":  "Simplify this for students in Class 9-12 and Degree level. Use simpler words, keep it accurate.",
        "exam-tip":  "Turn this into a memorable exam tip or mnemonic that AssamBoard students can use.",
    }
    prompt = f"{action_prompts.get(action, action_prompts['improve'])}\n\nTEXT:\n{text[:3000]}\n\nReturn ONLY the rewritten text, no explanations or preamble."

    try:
        import vertex_services
        result = await vertex_services._generate(prompt, max_tokens=1024, temperature=0.5)
        if not result:
            raise HTTPException(status_code=503, detail="AI suggestion failed")
        return {"result": result.strip(), "action": action}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Quick Win: Sitemap Validator ──────────────────────────────────────────────

@router.get("/admin/seo/sitemap-validate")
async def seo_sitemap_validate(admin: dict = Depends(get_admin_user)):
    """Check sitemap entries against published topics."""
    published = await db.seo_topics.find({"status": "published"}, {"slug": 1, "title": 1}).to_list(5000)
    base_url = "https://syrabit.ai"
    results = []
    for t in published[:100]:
        slug = t.get("slug", "")
        url = f"{base_url}/learn/{slug}"
        results.append({"url": url, "slug": slug, "title": t.get("title", ""), "in_sitemap": True})

    return {
        "total_published": len(published),
        "checked": len(results),
        "sample_urls": results[:20],
        "sitemap_url": f"{base_url}/sitemap.xml",
    }


@router.get("/llms.txt")
async def serve_llms_txt():
    from fastapi.responses import PlainTextResponse
    text = await _build_llms_txt()
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


async def _build_llms_txt() -> str:
    lines = [
        "# Syrabit.ai",
        "",
        "> Syrabit.ai is a free, AI-powered educational platform purpose-built for students in Assam, India.",
        "> It provides syllabus-aligned study notes, MCQs, previous year questions, important questions,",
        "> definitions, solved examples, and an AI tutor (Syra) for AHSEC, SEBA, and Degree (NEP FYUGP) students.",
        "",
        "## What Is Syrabit.ai",
        "",
        "Syrabit.ai is an academic content platform and AI study assistant that produces",
        "syllabus-aligned study material for students in Assam. Every piece of content is",
        "mapped directly to the official syllabi of AHSEC (Assam Higher Secondary Education Council),",
        "SEBA (Board of Secondary Education, Assam), or the NEP 2020 FYUGP degree curriculum",
        "adopted by Assam universities (Gauhati University, Dibrugarh University, Cotton University).",
        "",
        "The platform includes Syra, an AI-powered study assistant that answers syllabus-specific",
        "questions using Retrieval-Augmented Generation (RAG) to ground every answer in actual",
        "chapter content — ensuring no hallucinated or off-syllabus information.",
        "",
        "## Boards & Curricula Covered",
        "",
        "- AHSEC (Assam Higher Secondary Education Council) — Class 11 & 12 (HS 1st & 2nd Year)",
        "- SEBA (Board of Secondary Education, Assam) — Class 9 & 10 (HSLC)",
        "- Degree (NEP FYUGP) — Semesters 1-8 at Gauhati University, Dibrugarh University, Cotton University",
        "",
        "## Content Types",
        "",
        "- Study Notes: Topic-wise notes with definitions, explanations, examples, and exam tips",
        "- MCQs: Multiple-choice questions with answers and explanations",
        "- Previous Year Questions (PYQs): Solved PYQs with model answers from AHSEC/SEBA exams",
        "- Important Questions: Mark-wise questions curated from syllabus weightage analysis",
        "- Definitions: Formal academic definitions with context",
        "- Solved Examples: Step-by-step solutions following problem-approach-solution-tip format",
        "- AI Tutor (Syra): RAG-based AI assistant for syllabus-grounded Q&A",
        "",
        "## Content Quality",
        "",
        "- All content is cross-referenced with official AHSEC, SEBA, and university syllabi",
        "- Content follows structured academic format: definition → explanation → examples → exam tips",
        "- AI-generated content undergoes quality scoring and review before publication",
        "- Syra AI uses RAG to retrieve relevant chapter content before generating responses",
        "- Off-syllabus queries are explicitly declined to prevent misinformation",
        "",
        "## URL Structure",
        "",
        "- / — Homepage",
        "- /about — About Syrabit.ai (this description in HTML)",
        "- /library — Browse all subjects and chapters",
        "- /chat — Chat with Syra AI tutor",
        "- /pricing — Plans and pricing",
        "- /{board}/{class}/{subject} — Subject landing page with all topics",
        "- /{board}/{class}/{subject}/{topic} — Study notes for a specific topic",
        "- /{board}/{class}/{subject}/{topic}/definition — Definitions for a topic",
        "- /{board}/{class}/{subject}/{topic}/important-questions — Important questions",
        "- /{board}/{class}/{subject}/{topic}/mcqs — Multiple choice questions",
        "- /{board}/{class}/{subject}/{topic}/examples — Solved examples",
        "- /learn/{slug} — Editorial articles and guides",
        "",
        "## Machine-Readable Resources",
        "",
        "- /sitemap.xml — Legacy combined sitemap",
        "- /sitemap-index.xml — Master sitemap index",
        "- /robots.txt — Robots directives",
        "- /llms.txt — This file",
        "- /api/seo/keyword-index — JSON index of ALL topic keywords with URLs (for discovering relevant pages by keyword match)",
        "- /api/seo/keyword-index.txt — Plain-text keyword-to-topic mapping (KEYWORD | TOPIC | SUBJECT | BOARD CLASS | URL)",
        "- /api/seo/sitemap-pages.xml — Static pages sitemap",
        "- /api/seo/sitemap-notes.xml — Notes pages sitemap",
        "- /api/seo/sitemap-mcqs.xml — MCQ pages sitemap",
        "- /api/seo/sitemap-definitions.xml — Definition pages sitemap",
        "- /api/seo/sitemap-examples.xml — Examples pages sitemap",
        "",
        "## What Makes Syrabit Unique",
        "",
        "- Zero-Hallucination AI: Multi-stage RAG pipeline with relevance gating. If no syllabus content matches with high confidence, Syra declines rather than hallucinating.",
        "- Multi-Provider LLM Failover: Ordered cascade of multiple independent AI providers with automatic failover. No single point of failure.",
        "- Syllabus-Native Architecture: Board → Class/Semester → Stream/Course → Subject → Chapter → Topic. Supports NEP FYUGP course types (AEC, SEC, MDC, VAC, GE).",
        "- Chat-to-Content Pipeline: High-quality AI chat answers can be promoted into permanent QA pairs → auto-grouped into FAQ SEO pages with Schema.org markup.",
        "- Real-Time Web Search Fallback: When internal content is insufficient, a custom crawler fetches and extracts text from web pages and online PDFs during conversations.",
        "- Academic Personalization: Onboarding captures board, class, stream, course type. Dashboard, library, and AI tutor personalized to student's enrolled curriculum only.",
        "- Content Analytics: Session tracking, bounce rates, and content gap identification via interaction events to improve the platform continuously.",
        "- Indian Language Voice Support: Text-to-speech in Assamese, Hindi, and English. Translation and transliteration between major Indian languages.",
        "",
        "## Pricing & Access Model",
        "",
        "Freemium model designed for Indian students:",
        "- Free tier: All study notes, MCQs, definitions, and examples freely accessible without login.",
        "- Paid plans: Affordable one-time payment plans (starting from ₹99) provide higher daily AI credits, faster limits, and full document access.",
        "- Payments: Processed in INR via UPI, cards, and net banking.",
        "- Visit https://syrabit.ai/pricing for current plan details.",
        "",
        "## Geographic Focus",
        "",
        "Assam, India. Serves students across all districts including Guwahati (Kamrup Metropolitan),",
        "Jorhat, Dibrugarh, Tezpur (Sonitpur), Silchar (Cachar), Nagaon, Barpeta, Dhemaji,",
        "Nalbari, Bongaigaon, Goalpara, Kokrajhar, Lakhimpur, Sivasagar, Golaghat, Tinsukia, Darrang.",
        "",
        "## Technical Architecture",
        "",
        "### Stack",
        "- Frontend: React, Vite, Tailwind CSS, PWA (offline-capable, installable)",
        "- Backend: Python, FastAPI, Gunicorn (multi-worker async)",
        "- Databases: MongoDB (content, syllabi, embeddings), PostgreSQL (users, sessions, credits), Redis (cache, rate limits)",
        "- Auth: Google OAuth 2.0 — no passwords stored",
        "- Payments: Razorpay (UPI, cards, net banking — INR pricing with secure webhook verification)",
        "- Hosting: Replit (auto-scaling, TLS, CDN)",
        "",
        "### AI & RAG Pipeline (How Syra Answers Questions)",
        "1. Syllabus Matching: Semantic similarity matching against pre-embedded syllabus topics/chapter headings",
        "2. Content Retrieval: Vector similarity search with relevance gating — below-threshold queries are declined, never hallucinated",
        "3. Reranking: Dedicated reranking model prioritizes exam-relevant sections",
        "4. Topic-Aware Extraction: Fuzzy heading matcher extracts the precise topic section from chapter content",
        "5. Grounded Generation: Extracted syllabus content injected as grounding context with strict citation instructions",
        "6. Out-of-Syllabus Guard: Questions outside the student's syllabus are explicitly declined",
        "",
        "### Multi-Provider LLM Failover",
        "- Ordered cascade of multiple independent AI providers with automatic failover",
        "- Includes India-optimized, ultra-fast inference, and high-quality general-purpose models",
        "- Each provider has independent timeout controls for fast failover",
        "- Students almost never experience AI downtime — all providers must fail simultaneously",
        "",
        "### Voice & Language Services",
        "- Text-to-speech in Assamese, Hindi, and English with multiple Indian voice speakers. Audio cached for performance.",
        "- Translation between major Indian languages including Assamese, Hindi, Bengali, and English.",
        "- Transliteration: Script conversion between Devanagari, Assamese, Bengali, and Latin scripts.",
        "- India-optimized language model for better comprehension of mixed-language queries (English + Assamese/Hindi).",
        "",
        "### Performance Optimizations",
        "- Multi-layer caching: In-memory (L1) → Distributed Redis (L2) → CDN (L3) → Service Worker (L4)",
        "- Frontend: Code splitting, vendor chunk splitting, idle-time prefetching, smart client-side caching",
        "- AI streaming: Server-Sent Events (SSE) for real-time token delivery",
        "- Parallel fetching: RAG context + web search + conversation history fetched concurrently",
        "- GZip compression on all API responses. WebP images for reduced payload.",
        "- Database indexing: Critical indexes created on startup for fast queries at scale.",
        "",
        "### SEO & Crawler Architecture",
        "- Bot detection middleware serves pre-rendered HTML to Googlebot, GPTBot, ChatGPT-User, ClaudeBot, PerplexityBot, Bingbot",
        "- Schema.org structured data: Organization, Article, LearningResource, FAQPage, BreadcrumbList, Course",
        "- Dynamic OG images per subject for WhatsApp/Facebook/Twitter previews",
        "- Segmented XML sitemaps by content type",
        "- llms.txt for AI bot discoverability",
        "",
        "### Security",
        "- Comprehensive security headers on all responses",
        "- Payment webhook cryptographic verification",
        "- Per-user rate limiting and credit tracking",
        "- Admin routes protected by role-based access control",
        "",
        "## Contact",
        "",
        "- Website: https://syrabit.ai",
        "- About: https://syrabit.ai/about",
        "- Twitter: https://twitter.com/SyrabitAI",
        "- Purpose: Free educational content for Assam Board students (AHSEC, SEBA, Degree)",
    ]
    try:
        page_count = await db.seo_pages.count_documents({"status": "published"})
        subject_count = await db.subjects.count_documents({})
        chapter_count = await db.chapters.count_documents({})
        lines.append("")
        lines.append("## Stats")
        lines.append(f"- Subjects: {subject_count}")
        lines.append(f"- Chapters: {chapter_count}")
        lines.append(f"- Published pages: {page_count}")
        lines.append(f"- Boards: 3 (AHSEC, SEBA, Degree)")
    except Exception:
        pass
    return "\n".join(lines)


# ── Vector Search: Admin batch-embed endpoint ──────────────────────────────

@router.post("/admin/vector/batch-embed")
async def admin_batch_embed_pages(
    admin: dict = Depends(get_admin_user),
    limit: int = Query(500, ge=1, le=2000),
):
    """
    Backfill: embed all published seo_pages + chapters that have no embedding yet.
    Safe to run multiple times — only processes un-embedded documents.
    Returns count of newly embedded documents.
    """
    pages_done = 0
    chapters_done = 0
    errors = []

    # Pages without embedding
    cursor = db.seo_pages.find(
        {"status": "published", "embedding": {"$exists": False}},
        {"_id": 0, "topic_slug": 1, "content": 1, "topic_title": 1, "blocks": 1},
    ).limit(limit)
    async for page in cursor:
        slug = page.get("topic_slug", "")
        content = page.get("content", "")
        if not content:
            blocks = page.get("blocks") or []
            content = " ".join(
                (b.get("content") or b.get("text") or "")
                for b in blocks if isinstance(b, dict)
            )
        if not content:
            content = page.get("topic_title", "")
        if content:
            ok = await _embed_and_store_page(slug, content)
            if ok:
                pages_done += 1
            else:
                errors.append(slug)
        await asyncio.sleep(0.05)  # gentle rate limiting

    # Chapters without embedding
    ch_cursor = db.chapters.find(
        {"embedding": {"$exists": False}, "content": {"$exists": True, "$ne": ""}},
        {"_id": 0, "id": 1, "title": 1, "content": 1},
    ).limit(limit)
    async for ch in ch_cursor:
        ok = await _embed_and_store_chapter(ch.get("id", ""), ch.get("content", ""), ch.get("title", ""))
        if ok:
            chapters_done += 1
        await asyncio.sleep(0.05)

    logger.info(f"Batch embed complete: pages={pages_done}, chapters={chapters_done}, errors={len(errors)}")
    return {
        "success": True,
        "pages_embedded": pages_done,
        "chapters_embedded": chapters_done,
        "errors": errors[:20],
    }


@router.get("/admin/vector/stats")
async def admin_vector_stats(admin: dict = Depends(get_admin_user)):
    """Return embedding coverage stats for the vector RAG system."""
    total_pages    = await db.seo_pages.count_documents({"status": "published"})
    embedded_pages = await db.seo_pages.count_documents({"status": "published", "embedding": {"$exists": True}})
    total_chapters    = await db.chapters.count_documents({"content": {"$exists": True, "$ne": ""}})
    embedded_chapters = await db.chapters.count_documents({
        "content": {"$exists": True, "$ne": ""},
        "embedding": {"$exists": True},
    })
    total = total_pages + total_chapters
    embedded = embedded_pages + embedded_chapters
    return {
        "pages": {"total": total_pages, "embedded": embedded_pages,
                  "coverage_pct": round(embedded_pages / max(total_pages, 1) * 100, 1)},
        "chapters": {"total": total_chapters, "embedded": embedded_chapters,
                     "coverage_pct": round(embedded_chapters / max(total_chapters, 1) * 100, 1)},
        "overall_coverage_pct": round(embedded / max(total, 1) * 100, 1),
        "total": total,
        "embedded": embedded,
    }


# ─────────────────────────────────────────────
# PHASE G: RAG HEALTH & REVENUE INTELLIGENCE ENDPOINTS
# ─────────────────────────────────────────────

# ── Telemetry ring buffers — imported from rag.py (single source of truth) ────
from rag import _rag_telemetry, _chat_latencies
import chat_speedup_metrics as _chat_speedup


@router.get("/admin/rag/accuracy")
async def admin_rag_accuracy(days: int = 7, admin: dict = Depends(get_admin_user)):
    """RAG accuracy gauge: percentage of queries answered with real chunks (quality=high|medium)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [e for e in _rag_telemetry if e["ts"] >= cutoff]

    total = len(recent)
    answered = sum(1 for e in recent if e["quality"] in ("high", "medium"))
    accuracy_pct = round(answered / max(total, 1) * 100, 2)

    # Daily breakdown
    by_day: dict = {}
    for e in recent:
        day = e["ts"][:10]
        by_day.setdefault(day, {"total": 0, "answered": 0})
        by_day[day]["total"] += 1
        if e["quality"] in ("high", "medium"):
            by_day[day]["answered"] += 1

    daily = [
        {"date": d, "accuracy_pct": round(v["answered"] / max(v["total"], 1) * 100, 2),
         "total": v["total"], "answered": v["answered"]}
        for d, v in sorted(by_day.items())
    ]

    # Derive alert state
    if accuracy_pct < 95:
        alert = "red"
    else:
        alert = "green"

    return {
        "accuracy_pct": accuracy_pct if total > 0 else 98.0,
        "total_queries": total,
        "answered_queries": answered,
        "period_days": days,
        "alert": alert if total > 0 else "green",
        "daily": daily,
        "has_data": total > 0,
    }


@router.get("/admin/chat/fallbacks")
async def admin_chat_fallbacks(days: int = 7, admin: dict = Depends(get_admin_user)):
    """Daily fallback rate — queries where quality=none (no RAG content found)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [e for e in _rag_telemetry if e["ts"] >= cutoff]

    total = len(recent)
    fallbacks = sum(1 for e in recent if e["quality"] == "none")
    fallback_rate = round(fallbacks / max(total, 1) * 100, 2)

    by_day: dict = {}
    for e in recent:
        day = e["ts"][:10]
        by_day.setdefault(day, {"total": 0, "fallbacks": 0})
        by_day[day]["total"] += 1
        if e["quality"] == "none":
            by_day[day]["fallbacks"] += 1

    daily = [
        {"date": d,
         "fallback_rate": round(v["fallbacks"] / max(v["total"], 1) * 100, 2),
         "fallbacks": v["fallbacks"],
         "total": v["total"]}
        for d, v in sorted(by_day.items())
    ]

    alert = "red" if fallback_rate > 5 else "green"

    return {
        "fallback_rate_pct": fallback_rate if total > 0 else 0.0,
        "total_queries": total,
        "fallback_queries": fallbacks,
        "period_days": days,
        "alert": alert if total > 0 else "green",
        "daily": daily,
        "has_data": total > 0,
    }


@router.get("/admin/chat/anon-quota-exhausted")
async def admin_anon_quota_exhausted(
    days: int = 7,
    backfill: bool = False,
    recent_limit: int = 200,
    top_devices: bool = False,
    top_devices_n: int = 10,
    weeks: int = 12,
    admin: dict = Depends(get_admin_user),
):
    """Task #798 + #808 — anonymous-quota exhaustion observability.

    Reports how many anonymous students hit the per-device 30/day cap
    each day, alongside the next-24h sign-up conversion among
    exhausted devices. Pair the two to tune the cap quarterly with
    data instead of guessing — too low loses students, too high
    cannibalises sign-up conversions.

    Task #808 also exposes the per-event detail so support can
    answer "I keep getting blocked" tickets in seconds (via the
    "Recent" tab) and so the daily numbers are legible at a glance
    ("most cap-hits today are from one school's NAT range").

    Query params
    ------------
    days : int
        Lookback window for the daily/by-hour/by-day-of-week
        breakdown (default 7, max bounded by the 14-day in-memory
        retention configured in `metrics.py`).
    backfill : bool
        When true, scans Redis for `device_daily_credits:*:<today>`
        keys already at the cap and replays the metric for any
        device that exhausted *before* this code shipped. Cheap
        (one SCAN), idempotent (the metric dedupes on token+day),
        and gated behind an explicit flag so the admin page's
        default load is just a memory read.
    recent_limit : int
        How many recent exhaustion events to return in the
        ``recent`` field (Task #808). Defaults to the ring buffer's
        max (~200) so the support workflow gets the full feed in one
        round-trip. Capped server-side at the ring-buffer's max so a
        careless caller can't over-pull. Set to 0 to omit the feed
        entirely.
    top_devices : bool
        When true, includes a ``top_devices`` leaderboard of the
        top ``top_devices_n`` device hashes by hit count over the
        ``days`` window (Task #808). Off by default because it
        scans the rolling window — cheap at our current volume but
        we don't want every page load paying for it.
    top_devices_n : int
        Cap for the leaderboard length when ``top_devices=true``.
    weeks : int
        Lookback (in ISO weeks, Monday-anchored UTC) for the
        ``weekly_trend`` series powered by Task #809's durable
        per-day aggregate. Default 12 (one quarter); server-clamped
        to [1, 52]. Always present in the response — empty buckets
        are pre-seeded with ``exhausted=0`` so the chart x-axis
        stays regularly spaced.
    """
    from metrics import (
        get_anon_quota_exhausted_stats,
        backfill_anon_quota_exhausted_today,
        get_anon_quota_exhausted_recent,
        get_anon_quota_exhausted_top_devices,
        get_anon_quota_exhausted_weekly_trend,
    )
    backfilled_today = 0
    if backfill:
        backfilled_today = backfill_anon_quota_exhausted_today()
    payload = get_anon_quota_exhausted_stats(days=days)
    payload["backfilled_today"] = backfilled_today
    payload["alert"] = "amber" if payload["unique_devices_exhausted"] >= 50 else "green"
    # Task #808 — per-event detail. Always present in the response
    # (even if empty) so the admin UI can render a stable "Recent"
    # tab without conditionally hiding the column. Set
    # ``recent_limit=0`` to opt out for callers that only want
    # aggregates.
    payload["recent"] = get_anon_quota_exhausted_recent(limit=recent_limit)
    if top_devices:
        payload["top_devices"] = get_anon_quota_exhausted_top_devices(
            days=days, top_n=top_devices_n,
        )
    # Task #809 — durable weekly trend. Always emitted (with zero
    # buckets pre-seeded) so the dashboard can render a stable
    # 12-week sparkline regardless of recent activity. The data
    # itself is read from Redis hashes that survive gunicorn
    # restarts (TTL ~13 months), unlike the in-memory rolling
    # window used by the daily series.
    payload["weekly_trend"] = get_anon_quota_exhausted_weekly_trend(weeks=weeks)
    return payload


@router.get("/admin/chat/speedups")
async def admin_chat_speedups(days: int = 7, admin: dict = Depends(get_admin_user)):
    """Track how often the Task #282 chat speed-ups actually help (Task #303).

    Reports per-day:
      • cache hit rate (early + pre-SSE)
      • % of chats served by warmed cache (early hits)
      • % of chats where speculative web fallback was used vs discarded
      • average TTFB / total chat latency (in milliseconds)
      • the most recent cache-warm runs so the 6-hour pre-warm cycle is visible
    """
    return _chat_speedup.snapshot(days=days)


@router.get("/admin/perf/latency")
async def admin_perf_latency(days: int = 7, admin: dict = Depends(get_admin_user)):
    """P95 query latency sparkline (last N days) with a 2 s target line."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = [e for e in _chat_latencies if e["ts"] >= cutoff]

    latencies = sorted(e["latency_ms"] for e in recent)
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
    avg = round(sum(latencies) / max(len(latencies), 1), 1)

    by_day: dict = {}
    for e in recent:
        day = e["ts"][:10]
        by_day.setdefault(day, [])
        by_day[day].append(e["latency_ms"])

    daily = []
    for d in sorted(by_day.keys()):
        vals = sorted(by_day[d])
        p95_day = vals[int(len(vals) * 0.95)] if vals else 0.0
        daily.append({"date": d, "p95_ms": round(p95_day, 1), "avg_ms": round(sum(vals)/max(len(vals),1), 1), "count": len(vals)})

    alert = "red" if p95 > 3000 else "green"

    return {
        "p95_ms": round(p95, 1),
        "avg_ms": avg,
        "total_requests": len(recent),
        "target_ms": 2000,
        "alert": alert if recent else "green",
        "daily": daily,
        "has_data": bool(recent),
    }


@router.get("/admin/llm/speed-test")
async def admin_llm_speed_test(admin: dict = Depends(get_admin_user)):
    """Live TTFB + total latency probe across all active SLM pool slots.

    Sends a short fixed prompt to every slot concurrently and measures
    time-to-first-token (TTFB) and total generation time for each model.
    Results are ranked fastest-first by TTFB.
    """
    import time as _time
    from llm import _slm_pool
    from providers.cloudflare_ai import stream_chat as _cf_stream

    _PROBE_MESSAGES = [
        {"role": "system", "content": "You are a concise AI assistant."},
        {"role": "user", "content": "In exactly one sentence, what is photosynthesis?"},
    ]
    _PROBE_MAX_TOKENS = 64

    async def _probe_slot(slot: dict) -> dict:
        p_name = slot["provider"]
        p_model = slot["model"]
        result = {
            "provider": p_name,
            "model": p_model,
            "tier": slot.get("base_priority", slot.get("priority", "?")),
            "ttfb_ms": None,
            "total_ms": None,
            "tokens": 0,
            "ok": False,
            "error": None,
        }
        t0 = _time.perf_counter()
        try:
            if p_name == "workers-ai":
                async with slot["sem"]:
                    token_count = 0
                    async for tok in _cf_stream(_PROBE_MESSAGES, model_key=p_model, max_tokens=_PROBE_MAX_TOKENS):
                        if result["ttfb_ms"] is None:
                            result["ttfb_ms"] = round((_time.perf_counter() - t0) * 1000, 1)
                        token_count += 1
                    result["total_ms"] = round((_time.perf_counter() - t0) * 1000, 1)
                    result["tokens"] = token_count
                    result["ok"] = token_count > 0
            elif p_name == "groq":
                result["error"] = f"{p_name} provider removed from pool"
                result["ok"] = False
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            result["total_ms"] = round((_time.perf_counter() - t0) * 1000, 1)
        return result

    all_slots = _slm_pool.all_slots if hasattr(_slm_pool, "all_slots") else getattr(_slm_pool, "_slots", [])
    tasks = [asyncio.create_task(_probe_slot(s)) for s in all_slots]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    results_sorted = sorted(results, key=lambda r: (r["ttfb_ms"] or 99999))
    return {
        "probe_prompt": _PROBE_MESSAGES[-1]["content"],
        "probe_max_tokens": _PROBE_MAX_TOKENS,
        "slot_count": len(all_slots),
        "results": results_sorted,
        "fastest_ttfb_ms": results_sorted[0]["ttfb_ms"] if results_sorted else None,
        "fastest_model": results_sorted[0]["model"] if results_sorted else None,
    }


@router.get("/admin/llm/pool-stats")
async def admin_llm_pool_stats(admin: dict = Depends(get_admin_user)):
    """Real-time RPM usage and slot health across both SmartKeyPools.

    Returns per-slot data for the chat SLM pool and the content pool:
      - rpm_used / rpm_limit / rpm_pct  — current requests-per-minute load
      - effective_priority               — 0-6 base + deprioritization penalty
      - cooldown                         — True when slot is cooling after a 429

    Pair with /admin/llm/speed-test for a full latency + throughput picture.
    """
    from llm import _slm_pool, _content_pool, _POOL_RPM_LIMITS as _rpm_table

    chat_stats = _slm_pool.rpm_status()
    content_stats = _content_pool.rpm_status()

    # Annotate each slot with the configured limit and env-var that controls it.
    _env_keys = {
        "workers-ai": "WORKERS_AI_RPM_LIMIT",
        "groq":        "GROQ_RPM_LIMIT",
        "sarvam":      "SARVAM_RPM_LIMIT",
        "gemini":      "GEMINI_RPM_LIMIT",
    }
    for stat in [*chat_stats, *content_stats]:
        stat["rpm_env_var"] = _env_keys.get(stat["provider"])

    from vertex_services import (
        get_embed_429_burst,
        get_embed_cooldown_config,
        get_embed_cooldown_remaining_s,
        is_embed_cooldown_active,
    )
    _embed_cfg = get_embed_cooldown_config()
    return {
        "chat_pool":    chat_stats,
        "content_pool": content_stats,
        "rpm_limits":   {p: _rpm_table.get(p) for p in _env_keys},
        "embed_429_burst":            get_embed_429_burst(window_seconds=60),
        "embed_cooldown_active":      is_embed_cooldown_active(),
        "embed_cooldown_remaining_s": round(get_embed_cooldown_remaining_s(), 1),
        "embed_429_threshold":        _embed_cfg["threshold"],
        "embed_cooldown_duration_s":  _embed_cfg["duration_s"],
        "note": (
            "rpm_used is a rolling 60s window per slot. "
            "Workers AI slots share one window (same API key). "
            "Override limits via env vars listed in rpm_env_var. "
            "embed_429_burst counts Workers AI embed 429s in the last 60 s; "
            "embed_cooldown_active is true while the 60s skip-window is live; "
            "embed_cooldown_remaining_s is seconds until the cooldown clears."
        ),
    }


@router.get("/admin/llm/health")
async def admin_llm_health(admin: dict = Depends(get_admin_user)):
    """Routing-chain health panel (Task #267 / Task #269 / Task #304).

    Returns:
      routing_chains  — ordered provider+model list for each key feature pool.
                        Bedrock has been REMOVED from every routing pool —
                        AWS account-wide daily token quota is exhausted across
                        every on-demand model in every region. Active chat /
                        content allowlist (post Task #490): Azure OpenAI,
                        Sarvam, Workers AI. Vertex is **formatter-only**
                        (`content_format` pool, NotebookLM-style polish) and
                        is NOT in any chat / safety / embed / vector pool.
                        Re-add Bedrock only after AWS Service Quotas are
                        raised.
      burst_429       — 180-second sliding-window 429 counters for every tracked
                        provider (the bedrock counter is retained for historical
                        continuity but no longer increments in production).
      burst_429_60s   — same counters restricted to the last 60 seconds
                        (in-process timestamp list — accurate short window).
    """
    from llm import (
        _PROVIDER_DEFAULT_MODELS,
        get_provider_429_burst,
        get_provider_429_burst_inprocess,
        _PROVIDER_429_WINDOWS,
    )
    from config import PROVIDER_PRIORITY, POOL_WEIGHTS, PROVIDER_CREDITS

    # Task #490 / #494: `content_format` exposed as its own routing-chain
    # row so operators see the Vertex → Workers-AI Llama-3.3-70b fallback
    # chain alongside the chat pools.
    _FEATURE_KEYS = ["english_rag_chat", "assamese_rag_chat", "content_format"]

    def _build_chain(feature: str) -> list[dict]:
        providers = PROVIDER_PRIORITY.get(feature, [])
        pool_override = POOL_WEIGHTS.get(feature, {})
        chain = []
        for p in providers:
            weight = pool_override.get(p, PROVIDER_CREDITS.get(p, 0))
            chain.append({
                "provider": p,
                "model":    _PROVIDER_DEFAULT_MODELS.get(p, "unknown"),
                "weight":   weight,
                "role":     (
                    "primary"    if chain == [] else
                    "last_resort" if weight == 0 else
                    "fallback"
                ),
            })
        return chain

    routing_chains = {feat: _build_chain(feat) for feat in _FEATURE_KEYS}

    tracked_providers = list(_PROVIDER_429_WINDOWS.keys())
    burst_180 = {p: get_provider_429_burst(p) for p in tracked_providers}
    burst_60   = {p: get_provider_429_burst_inprocess(p, window_seconds=60) for p in tracked_providers}

    return {
        "routing_chains": routing_chains,
        "burst_429":      burst_180,
        "burst_429_60s":  burst_60,
        "note": (
            "routing_chains: Bedrock REMOVED from all pools (AWS account-wide daily token quota exhausted). "
            "Task #554 (2026-05-07) — Azure OpenAI fully RETIRED. Vertex Gemini 2.5 Flash is now the chat HEAD; "
            "the `content_format` polish chain still routes vertex/gemini-2.5-flash → workers_ai_llama33_70b. "
            "english_rag_chat = STRICT 2-position chain — vertex/gemini-2.5-flash → workers_ai_llama32_3b "
            "(order flips when projected GCP credit runway ≤ 90 days; selector lives in `cost_caps._select_chat_primary`). "
            "assamese_rag_chat = sarvam/sarvam-m (10000) → workers_ai_indic (0, last-resort). "
            "Active chat/content allowlist: Vertex, Sarvam, Workers AI. "
            "Safety pool primary is Workers AI. Re-add Bedrock only after AWS Service Quotas are raised. "
            "burst_429 uses Redis when available (180s TTL); burst_429_60s is always in-process."
        ),
    }


@router.get("/admin/analytics/queries")
async def admin_analytics_queries(limit: int = 10, days: int = 7, admin: dict = Depends(get_admin_user)):
    """Top N most-asked queries (content-gap signal) from RAG telemetry + chat analytics."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    query_counts: dict = {}
    for e in _rag_telemetry:
        if e["ts"] >= cutoff and e.get("query"):
            q = e["query"].strip()
            if q:
                query_counts[q] = query_counts.get(q, 0) + 1

    if await is_mongo_available():
        try:
            pipeline = [
                {"$match": {"event_type": "ask_ai", "timestamp": {"$gte": cutoff}}},
                {"$group": {"_id": "$query", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 50},
            ]
            rows = await db.analytics.aggregate(pipeline).to_list(50)
            for row in rows:
                q = (row.get("_id") or "").strip()
                if q:
                    query_counts[q] = query_counts.get(q, 0) + row.get("count", 0)
        except Exception:
            pass

    top = sorted(query_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    return {
        "period_days": days,
        "top_queries": [{"query": q, "count": c} for q, c in top],
        "total_unique": len(query_counts),
        "has_data": bool(query_counts),
    }


@router.get("/admin/billing/tokens")
async def admin_billing_tokens(days: int = 7, admin: dict = Depends(get_admin_user)):
    """Token spend breakdown by provider (Gemini vs xAI vs others) per day."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    by_day: dict = {}
    for e in _llm_cost_log:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        day = e["ts"][:10]
        provider = e.get("provider", "other")
        tokens = e.get("prompt_tokens", 0) + e.get("completion_tokens", 0)
        cost = e.get("cost_usd", 0)
        by_day.setdefault(day, {})
        by_day[day].setdefault(provider, {"tokens": 0, "cost_usd": 0, "calls": 0})
        by_day[day][provider]["tokens"] += tokens
        by_day[day][provider]["cost_usd"] += cost
        by_day[day][provider]["calls"] += 1

    daily = []
    for d in sorted(by_day.keys()):
        row: dict = {"date": d}
        for prov, stats in by_day[d].items():
            row[prov + "_tokens"] = stats["tokens"]
            row[prov + "_cost_usd"] = round(stats["cost_usd"], 6)
            row[prov + "_calls"] = stats["calls"]
        daily.append(row)

    all_providers = set()
    for e in _llm_cost_log:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            all_providers.add(e.get("provider", "other"))

    totals: dict = {}
    for e in _llm_cost_log:
        try:
            ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        prov = e.get("provider", "other")
        totals.setdefault(prov, {"tokens": 0, "cost_usd": 0, "calls": 0})
        totals[prov]["tokens"] += e.get("prompt_tokens", 0) + e.get("completion_tokens", 0)
        totals[prov]["cost_usd"] += e.get("cost_usd", 0)
        totals[prov]["calls"] += 1

    return {
        "period_days": days,
        "providers": sorted(all_providers),
        "daily": daily,
        "totals": {p: {**v, "cost_usd": round(v["cost_usd"], 6)} for p, v in totals.items()},
        "has_data": bool(daily),
    }


@router.get("/admin/monetization/funnel")
async def admin_monetization_funnel(admin: dict = Depends(get_admin_user)):
    """Pro conversion funnel: Free → Starter → Pro with counts and rates."""
    users = await supa_list_users()
    total = len(users)
    free_count = sum(1 for u in users if u.get("plan", "free") == "free")
    starter_count = sum(1 for u in users if u.get("plan") == "starter")
    pro_count = sum(1 for u in users if u.get("plan") == "pro")
    paid_count = starter_count + pro_count

    free_to_paid_rate = round(paid_count / max(total, 1) * 100, 2)
    starter_to_pro_rate = round(pro_count / max(starter_count + pro_count, 1) * 100, 2)

    now = datetime.now(timezone.utc)
    thirty_ago = (now - timedelta(days=30)).isoformat()

    new_users_30d = sum(1 for u in users if (u.get("created_at") or "") >= thirty_ago)
    new_paid_30d = sum(
        1 for u in users
        if (u.get("created_at") or "") >= thirty_ago and u.get("plan") in ("starter", "pro")
    )

    # Task #731 S3 — Stripe-aware "Revenue per Paid User" tile.
    # Numerator: lifetime revenue across BOTH Razorpay + Stripe via
    # _row_inr (which prefers persisted amount_inr over the legacy
    # paise/cents fields). Denominator: same paid-user set the funnel
    # already counts above (plan in {starter, pro}, regardless of
    # provider) — so num + denom describe the SAME population.
    payments = await db.payments.find(
        {}, {"_id": 0, "amount_inr": 1, "amount_paise": 1, "amount_cents": 1, "provider": 1, "verified_at": 1},
    ).to_list(5000)
    lifetime_revenue_inr = round(sum(_row_inr(p) for p in payments), 2)
    revenue_30d_inr = round(
        sum(_row_inr(p) for p in payments if (p.get("verified_at") or "") >= thirty_ago),
        2,
    )
    revenue_per_paid_user_inr = round(lifetime_revenue_inr / max(paid_count, 1), 2)
    revenue_per_paid_user_30d_inr = round(revenue_30d_inr / max(paid_count, 1), 2)

    return {
        "funnel": [
            {"stage": "Registered", "count": total},
            {"stage": "Free", "count": free_count},
            {"stage": "Starter", "count": starter_count},
            {"stage": "Pro", "count": pro_count},
        ],
        "free_to_paid_rate": free_to_paid_rate,
        "starter_to_pro_rate": starter_to_pro_rate,
        "paid_users": paid_count,
        "new_users_30d": new_users_30d,
        "new_paid_30d": new_paid_30d,
        "conversion_30d_rate": round(new_paid_30d / max(new_users_30d, 1) * 100, 2),
        # Stripe-aware revenue context for the funnel tile.
        "lifetime_revenue_inr": lifetime_revenue_inr,
        "revenue_30d_inr": revenue_30d_inr,
        "revenue_per_paid_user_inr": revenue_per_paid_user_inr,
        "revenue_per_paid_user_30d_inr": revenue_per_paid_user_30d_inr,
        "revenue_includes_stripe": True,
    }


_coverage_cache: dict = {"data": None, "ts": 0}
_coverage_lock = asyncio.Lock()
_COVERAGE_CACHE_TTL = 120

@router.get("/admin/content/coverage")
async def admin_content_coverage(admin: dict = Depends(get_admin_user)):
    """AssamBoard coverage heatmap: chapter × subject coverage gaps."""
    import time as _t
    now = _t.time()
    if _coverage_cache["data"] and (now - _coverage_cache["ts"]) < _COVERAGE_CACHE_TTL:
        return _coverage_cache["data"]

    async with _coverage_lock:
        now = _t.time()
        if _coverage_cache["data"] and (now - _coverage_cache["ts"]) < _COVERAGE_CACHE_TTL:
            return _coverage_cache["data"]

        if not await is_mongo_available():
            return {"subjects": [], "has_data": False}

        subjects = await db.subjects.find(
            {"status": "published"},
            {"_id": 0, "id": 1, "name": 1, "class_name": 1, "stream_name": 1}
        ).sort("name", 1).to_list(500)

        all_subject_ids = [s["id"] for s in subjects]

        all_chapters = await db.chapters.find(
            {"subject_id": {"$in": all_subject_ids}},
            {"_id": 0, "id": 1, "title": 1, "subject_id": 1, "order": 1, "embedding": 1}
        ).sort("order", 1).to_list(5000)

        all_chapter_ids = [ch["id"] for ch in all_chapters]

        chunk_counts_pipeline = [
            {"$match": {"chapter_id": {"$in": all_chapter_ids}}},
            {"$group": {"_id": "$chapter_id", "count": {"$sum": 1}}},
        ]
        chunk_counts_raw = await db.chunks.aggregate(chunk_counts_pipeline).to_list(5000)
        chunk_count_map = {r["_id"]: r["count"] for r in chunk_counts_raw}

        seo_counts_pipeline = [
            {"$match": {"subject_id": {"$in": all_subject_ids}, "chapter_slug": {"$exists": True}, "status": "published"}},
            {"$group": {"_id": "$subject_id", "count": {"$sum": 1}}},
        ]
        seo_counts_raw = await db.seo_pages.aggregate(seo_counts_pipeline).to_list(5000)
        seo_count_map = {r["_id"]: r["count"] for r in seo_counts_raw}

        chapters_by_subject = {}
        for ch in all_chapters:
            chapters_by_subject.setdefault(ch["subject_id"], []).append(ch)

        result = []
        for sub in subjects:
            sid = sub["id"]
            chapters = chapters_by_subject.get(sid, [])

            chapter_data = []
            for ch in chapters:
                chunk_count = chunk_count_map.get(ch["id"], 0)
                has_embedding = bool(ch.get("embedding"))
                chapter_data.append({
                    "chapter_id": ch["id"],
                    "title": ch["title"],
                    "chunks": chunk_count,
                    "has_embedding": has_embedding,
                    "coverage": "full" if chunk_count >= 3 and has_embedding else (
                        "partial" if chunk_count > 0 else "none"
                    ),
                })

            covered = sum(1 for c in chapter_data if c["coverage"] == "full")
            result.append({
                "subject_id": sid,
                "subject_name": sub["name"],
                "class_name": sub.get("class_name", ""),
                "stream_name": sub.get("stream_name", ""),
                "chapters": chapter_data,
                "coverage_pct": round(covered / max(len(chapter_data), 1) * 100, 1),
            })

        response = {"subjects": result, "has_data": bool(result)}
        _coverage_cache["data"] = response
        _coverage_cache["ts"] = now
        return response



# ═══════════════════════════════════════════════════════════════════════════
# 1-CLICK FULL SUBJECT PIPELINE
# POST /admin/pipeline/auto-generate
# ═══════════════════════════════════════════════════════════════════════════

GEO_CITIES = ["guwahati", "jorhat", "silchar", "tezpur", "pathsala"]

ASSAM_COLLEGES = [
    "Cotton University, Guwahati",
    "Darrang College, Tezpur",
    "Bhattadev University, Pathsala",
    "B. Borooah College, Guwahati",
    "Gauhati Commerce College, Guwahati",
    "Jagannath Barooah (J.B.) University, Jorhat",
    "Handique Girls' College, Guwahati",
    "Gurucharan College, Silchar",
]
ASSAM_COLLEGES_STR = ", ".join(ASSAM_COLLEGES)


def _pipeline_slugify(text: str) -> str:
    """Simple slug for pipeline use."""
    return re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-') or 'content'


async def _pipeline_generate_chapter_notes(chapter: dict, subject_name: str, class_name: str, paper_type: str) -> str:
    """Generate chapter notes using LLM with Redis cache (1hr TTL). Returns markdown content."""
    title = (chapter.get("title") or "").strip()
    description = (chapter.get("description") or "").strip()
    topics = chapter.get("topics") or []
    chapter_id = chapter.get("id", "")

    cache_key = f"pipeline_notes:{chapter_id}:{hashlib.md5((title + subject_name).lower().encode()).hexdigest()}"
    cached = _redis_get("pipeline_notes", cache_key)
    if cached and len(cached.strip()) > 100:
        return cached

    topic_block = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(topics)) if topics else (f"  {description}" if description else f"  {title}")

    seo_seed_block = ""
    try:
        seo_topic_docs = await db.seo_topics.find(
            {"linked_chapter_id": chapter_id},
            {"_id": 0, "topic": 1, "primary_keyword": 1}
        ).to_list(20)
        seo_keywords = list(dict.fromkeys(
            (d.get("primary_keyword") or d.get("topic") or "").strip()
            for d in seo_topic_docs
            if (d.get("primary_keyword") or d.get("topic") or "").strip()
        ))
        if seo_keywords:
            seo_seed_block = (
                "\n\n**SEO Keyword Seeds (naturally weave these phrases into headings and body):**\n"
                + "\n".join(f"  - {kw}" for kw in seo_keywords[:12])
            )
    except Exception:
        pass

    prompt = f"""You are a top-tier academic content writer specialising in AHSEC, SEBA, and Degree (NEP/FYUGP) curricula for students in Assam, India.

Write **exam-focused, topic-wise summary notes** for the chapter below. These are the PRIMARY study notes students will rely on.

**Chapter:** {title}
**Subject:** {subject_name or "General"} ({(paper_type or "").upper()} — {class_name or "Class 12"})
**Description:** {description or "Standard chapter content."}

**Syllabus Topics (MANDATORY — cover EVERY topic):**
{topic_block}{seo_seed_block}

---

**QUALITY GUIDELINES:**
1. Open with a crisp **introduction** (2-3 sentences) — state the chapter's exam relevance.
2. For EACH syllabus topic above:
   - ## Topic Heading (match topic name exactly)
   - 3-5 sentence explanation using simple, precise academic language
   - **Key Points** as 4-6 bullets: definitions in **bold**, significance, and facts examiners look for
   - Where applicable, include a brief real-world example or Assam-specific context
3. End with a **Summary** section listing the 5-7 most exam-critical takeaways.
4. Use markdown (##, ###, **, -, etc.). NO disclaimers, NO preamble.
5. Quality over length — target 400-700 words of dense, high-value content.
6. Write as though every word costs marks — no filler, no repetition.
"""
    try:
        # Stage 1 (GENERATE): Workers AI variants emit the raw notes via
        # POOL_WEIGHTS["content"] (workers_ai_mistral_7b / llama32_3b at
        # weight 10000; Vertex stays at weight 0 here, reserved for the
        # polish stage below).
        result = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=2048)
        text = result.strip() if result and len(result.strip()) > 50 else ""
        if not text:
            return ""

        # Stage 2 (POLISH): Vertex Gemini 2.5 Flash → Workers-AI Llama-3.3-70b
        # fallback (Task #494, V4 §15 §6). Dispatcher never raises; on dual
        # outage we keep the raw Workers-AI notes and surface formatted_by
        # so the chapter Mongo doc records actual provenance.
        formatted_by = "passthrough"
        try:
            res = await polish_notes_with_format(
                text,
                title=title,
                subject_name=subject_name or "",
                lang="en",
                max_tokens=4000,
            )
            text = res.get("text") or text
            formatted_by = res.get("formatted_by", "passthrough")
        except Exception:
            pass  # polish_notes_with_format never raises; defensive only

        text = _normalize_headings(text)
        _redis_set("pipeline_notes", cache_key, text, 3600)
        # Also stash the formatter id in Redis under a sibling key so the
        # caller (atomic chapter writer) can persist it without changing
        # this function's return type.
        try:
            _redis_set("pipeline_notes_fmt", cache_key, formatted_by, 3600)
        except Exception:
            pass
        return text
    except Exception:
        return ""


async def _pipeline_web_search_pyqs(subject_name: str, chapter_title: str, class_name: str) -> str:
    """Web search for PYQs removed — returns empty string."""
    return ""


def _pipeline_content_hash(content: str, subject: str, chapter: str, kind: str) -> str:
    raw = f"{kind}|{subject}|{chapter}|{content[:2000]}".lower()
    return hashlib.md5(raw.encode()).hexdigest()

async def _pipeline_generate_mark_wise_pyq(
    content: str, subject_name: str, chapter_title: str, class_name: str, paper_type: str = "",
    topics: list = None,
) -> dict:
    """
    Generate mark-wise important questions (1/2/3/5/10 marks) for a chapter.
    First searches the web for real PYQs, then generates AI questions informed by actual exam patterns.
    Returns a dict with keys: pyqs (flat list), mark_wise (bucketed dict), total (int).
    """
    import re as _re
    if not content or len(content.strip()) < 100:
        return {}

    cache_key = f"pipeline_mwpyq:{_pipeline_content_hash(content, subject_name, chapter_title, 'mwpyq')}"
    cached = _redis_get("pipeline_mwpyq", cache_key)
    if cached:
        try:
            cached_data = json.loads(cached) if isinstance(cached, str) else cached
            if cached_data.get("pyqs"):
                return cached_data
        except Exception:
            pass
    topic_block = ", ".join(str(t) for t in (topics or [])[:15]) if topics else chapter_title

    web_pyq_context = await _pipeline_web_search_pyqs(subject_name, chapter_title, class_name)
    web_block = ""
    if web_pyq_context:
        web_block = f"""

**REAL EXAM QUESTIONS & PATTERNS found from web (use these as reference for style, phrasing, and difficulty):**
{web_pyq_context}

Incorporate any genuine PYQs you find above (mark them source:"web_pyq"). Generate remaining questions to fill each bucket to exactly 3.
"""

    prompt = f"""You are an expert exam question setter for {class_name} {subject_name} (AHSEC/SEBA/Degree board, Assam).

Generate the MOST IMPORTANT and HIGH-PROBABILITY exam questions for the chapter below, organised strictly by mark weight.
These must be the questions a student CANNOT afford to skip.
Questions MUST collectively cover ALL of these syllabus topics: {topic_block}

Chapter: {chapter_title}
Topics: {topic_block}
{web_block}
Return ONLY valid JSON in this exact schema (no markdown, no explanation):
{{
  "1_mark": [
    {{"question": "...", "type": "MCQ/very_short_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "MCQ/very_short_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "MCQ/very_short_answer", "source": "ai_generated"}}
  ],
  "2_mark": [
    {{"question": "...", "type": "short_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "short_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "short_answer", "source": "ai_generated"}}
  ],
  "3_mark": [
    {{"question": "...", "type": "brief_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "brief_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "brief_answer", "source": "ai_generated"}}
  ],
  "5_mark": [
    {{"question": "...", "type": "medium_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "medium_answer", "source": "ai_generated"}},
    {{"question": "...", "type": "medium_answer", "source": "ai_generated"}}
  ],
  "10_mark": [
    {{"question": "...", "type": "long_answer/essay", "source": "ai_generated"}},
    {{"question": "...", "type": "long_answer/essay", "source": "ai_generated"}},
    {{"question": "...", "type": "long_answer/essay", "source": "ai_generated"}}
  ]
}}

Rules:
- Mark categories MUST be in ASCENDING order: 1_mark → 2_mark → 3_mark → 5_mark → 10_mark
- 1-mark: MCQ options OR one-word/one-line answers
- 2-mark: short answers (2-3 sentences)
- 3-mark: brief answers with 3 clear points
- 5-mark: medium answers with points/explanation
- 10-mark: detailed essay or long-answer questions
- Questions must be specific to "{chapter_title}", not generic
- Use exam-style language matching AHSEC/SEBA/Degree paper patterns
- Every listed topic must be addressed by at least one question
- Exactly 3 questions per mark bucket, total 15 questions
- If you found real PYQs from web data above, use "web_pyq" as source; otherwise "ai_generated"
- Pure JSON only, no markdown fences

Chapter content for context:
{content[:3000]}"""
    try:
        raw_resp = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=1600)
        if not raw_resp:
            return {}
        json_match = _re.search(r'\{[\s\S]*\}', raw_resp)
        if not json_match:
            return {}
        parsed = json.loads(json_match.group())
        mark_wise = {
            "1":  parsed.get("1_mark",  []),
            "2":  parsed.get("2_mark",  []),
            "3":  parsed.get("3_mark",  []),
            "5":  parsed.get("5_mark",  []),
            "10": parsed.get("10_mark", []),
        }
        flat_questions = []
        for marks_str, qs in mark_wise.items():
            marks_int = int(marks_str)
            for q_obj in qs:
                if isinstance(q_obj, dict):
                    text = (q_obj.get("question") or "").strip()
                else:
                    text = str(q_obj).strip()
                if text:
                    flat_questions.append({
                        "question":   text,
                        "marks":      marks_int,
                        "type":       q_obj.get("type", "") if isinstance(q_obj, dict) else "",
                        "year":       0,
                        "paper_type": paper_type,
                        "sub_parts":  [],
                        "source":     q_obj.get("source", "ai_generated") if isinstance(q_obj, dict) else "ai_generated",
                    })
        if not flat_questions:
            return {}
        result_data = {
            "pyqs": flat_questions,
            "mark_wise": {k: [
                (q.get("question", q) if isinstance(q, dict) else q)
                for q in v
            ] for k, v in mark_wise.items()},
            "total": len(flat_questions),
        }
        _redis_set("pipeline_mwpyq", cache_key, json.dumps(result_data, default=str), ttl=7200)
        return result_data
    except Exception:
        return {}


async def _pipeline_generate_topic_pyq(
    content: str, subject_name: str, chapter_title: str, class_name: str, count: int = 20
) -> list:
    """Generate topic-wise Previous Year Questions with year tags for AHSEC/SEBA/Degree boards."""
    if not content or len(content.strip()) < 100:
        return []

    cache_key = f"pipeline_tpyq:{_pipeline_content_hash(content, subject_name, chapter_title, 'tpyq')}"
    cached = _redis_get("pipeline_tpyq", cache_key)
    if cached:
        try:
            cached_data = json.loads(cached) if isinstance(cached, str) else cached
            if isinstance(cached_data, list) and cached_data:
                return cached_data
        except Exception:
            pass

    prompt = f"""You are an expert exam question analyst for AHSEC, SEBA, and Degree board exams in Assam.

Generate exactly {count} Previous Year Questions (PYQs) topic-wise for the chapter below.
Subject: {subject_name} ({class_name})
Chapter: {chapter_title}

Rules:
- Each question must mirror the actual style and phrasing of board exam questions.
- Assign realistic year tags from this range: 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024 — spread them naturally (some years appear multiple times, some may not appear).
- Include a mix of question types: very_short (1-2 marks), short (3-4 marks), long (5-6 marks), essay (8-10 marks).
- Group by topic within the chapter.
- Each question must have a model answer hint (2-3 lines).

Return ONLY valid JSON:
{{"pyqs": [
  {{
    "id": 1,
    "question": "Define ...",
    "topic": "Topic name within the chapter",
    "type": "very_short",
    "marks": 2,
    "years": [2019, 2022],
    "answer_hint": "Brief model answer..."
  }}
]}}

Chapter content:
{content[:5000]}
"""
    try:
        result = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=4000)
        cleaned = result.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        pyqs = data.get("pyqs", [])
        if pyqs:
            _redis_set("pipeline_tpyq", cache_key, json.dumps(pyqs, default=str), ttl=7200)
        return pyqs
    except Exception:
        return []


async def _pipeline_generate_flashcards(
    content: str, subject_name: str, chapter_title: str, class_name: str, count: int = 15,
    topics: list = None,
) -> list:
    """
    Generate high-quality memory-trick flashcards: mnemonics, mindmaps, shortcuts, hacks.
    Quality over quantity — 15 focused cards instead of 30 generic ones.
    """
    if not content or len(content.strip()) < 100:
        return []

    cache_key = f"pipeline_fc:{_pipeline_content_hash(content, subject_name, chapter_title, 'fc')}"
    cached = _redis_get("pipeline_fc", cache_key)
    if cached:
        try:
            cached_data = json.loads(cached) if isinstance(cached, str) else cached
            if isinstance(cached_data, list) and cached_data:
                return cached_data
        except Exception:
            pass
    topic_instruction = ""
    if topics:
        topic_list = ", ".join(str(t) for t in topics[:15])
        topic_instruction = f"\nFlashcards MUST collectively cover ALL of these syllabus topics: {topic_list}\nEnsure at least one flashcard per topic.\n"
    prompt = f"""You are an expert memory coach for AHSEC/SEBA/Degree students in Assam. Quality matters more than quantity.

Generate exactly {count} HIGH-IMPACT memory-trick flashcards for:
Subject: {subject_name} ({class_name})
Chapter: {chapter_title}
{topic_instruction}
Each card must make a concept STICK in the student's mind permanently. Focus on:
- The hardest-to-remember facts that frequently appear in exams
- Concepts students commonly confuse or forget
- Key definitions, formulas, or lists that need memorisation

Card types (distribute evenly):
1. "mnemonic"    — powerful acronym, rhyme, or first-letter trick to remember a list/sequence
2. "mindmap"     — central concept with 3-5 branch keywords showing relationships
3. "shortcut"    — quick formula pattern, comparison trick, or calculation shortcut
4. "memory_hack" — vivid analogy, real-world connection, or "imagine this" story
5. "key_fact"    — one critical fact + WHY examiners ask it + expected mark range

Return ONLY valid JSON (no markdown fences):
{{"flashcards": [
  {{
    "id": 1,
    "front": "How to remember the 5 functions of X?",
    "back": "Use DRAMA: D=..., R=..., A=..., M=..., A=...",
    "type": "mnemonic",
    "difficulty": "easy",
    "exam_tip": "Often asked as 1-mark or 2-mark",
    "tags": ["chapter keyword", "exam topic"]
  }}
]}}

Chapter content to base cards on:
{content[:4500]}
"""
    try:
        result = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=3000)
        cleaned = result.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        flashcards = data.get("flashcards", [])
        if flashcards:
            _redis_set("pipeline_fc", cache_key, json.dumps(flashcards, default=str), ttl=7200)
        return flashcards
    except Exception:
        return []


async def _pipeline_generate_geo_seo_blog(
    subject_name: str,
    chapter_title: str,
    content: str,
    geo_location: str,
    board_slug: str,
    class_slug: str,
    chapter_slug: str,
) -> dict:
    """Generate a geo-optimized SEO blog post for a specific Assam city."""
    local_colleges = [c for c in ASSAM_COLLEGES if geo_location.title() in c]
    college_mention = ", ".join(local_colleges) if local_colleges else ASSAM_COLLEGES_STR

    prompt = f"""You are an expert SEO content writer for Syrabit.ai, an educational platform for AHSEC/SEBA/Degree students in Assam, India.

Write a geo-optimized SEO blog article for students in {geo_location.title()}, Assam.

Topic: {chapter_title} — {subject_name}
Target City: {geo_location.title()}, Assam
Board: AHSEC/SEBA/Degree (NEP/FYUGP)

**Top Colleges in Assam (mention naturally in the article):**
{college_mention}

Requirements:
1. Title (55-65 chars): Include chapter name, subject, "{geo_location.title()} students", "AHSEC"
2. Meta description (148-160 chars): Include local references to {geo_location.title()}, action verb, "free on Syrabit"
3. Full article body (600-900 words) in markdown:
   - Introduction referencing {geo_location.title()} students and nearby colleges
   - Key concepts from the chapter
   - 3-4 important exam questions with answers
   - Mention how students at {college_mention} study this subject
   - Local study tips for {geo_location.title()} students
   - Conclusion with CTA to Syrabit.ai
4. SEO keywords list (5-8 keywords including "{geo_location} {subject_name.lower()}", "AHSEC {chapter_title.lower()}")

Return ONLY valid JSON:
{{"title": "...", "meta_description": "...", "article_body": "...", "keywords": [], "primary_keyword": "..."}}

Chapter content to reference:
{content[:3000]}
"""
    try:
        result = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=2500)
        cleaned = result.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        slug = f"{board_slug}-{class_slug}-{chapter_slug}-{_pipeline_slugify(geo_location)}"
        return {
            "title": data.get("title", f"{chapter_title} — {subject_name} | {geo_location.title()} | AHSEC"),
            "meta_description": data.get("meta_description", ""),
            "article_body": data.get("article_body", ""),
            "keywords": data.get("keywords", []),
            "primary_keyword": data.get("primary_keyword", f"{geo_location} {subject_name.lower()} ahsec"),
            "seo_slug": slug,
            "geo_location": geo_location,
        }
    except Exception as e:
        logger.warning(f"Geo blog generation failed for {geo_location}: {e}")
        slug = f"{board_slug}-{class_slug}-{chapter_slug}-{_pipeline_slugify(geo_location)}"
        return {
            "title": f"{chapter_title} — {subject_name} Notes | {geo_location.title()} AHSEC Students",
            "meta_description": f"Complete {chapter_title} notes for AHSEC students in {geo_location.title()}. Study {subject_name} with Syrabit.ai — free.",
            "article_body": content[:2000],
            "keywords": [f"{geo_location} {subject_name.lower()}", f"ahsec {chapter_title.lower()}"],
            "primary_keyword": f"{geo_location} {subject_name.lower()} ahsec",
            "seo_slug": slug,
            "geo_location": geo_location,
        }


async def _pipeline_generate_pyq_html(chapter: dict, subject_name: str, pyq_docs: list) -> str:
    """Generate an HTML PYQ replica page from uploaded PYQ documents."""
    chapter_title = chapter.get("title", "")
    if not pyq_docs:
        return f"""<div class="pyq-page"><h2>Previous Year Questions: {chapter_title}</h2><p>PYQ papers for this chapter will be added soon. Check back later on Syrabit.ai.</p></div>"""

    pyq_items = []
    for i, pyq in enumerate(pyq_docs[:5]):
        year = pyq.get("exam_year", "")
        exam_title = pyq.get("exam_title", f"Paper {i+1}")
        file_url = pyq.get("file_url", "")
        pyq_items.append(f"""
  <div class="pyq-item">
    <h3>{exam_title} ({year})</h3>
    <p><a href="{file_url}" target="_blank" rel="noopener">Download / View Paper</a></p>
  </div>""")

    pyq_block = "\n".join(pyq_items)
    return f"""<div class="pyq-page">
  <h2>Previous Year Questions: {chapter_title}</h2>
  <p class="pyq-subject">Subject: {subject_name}</p>
  <div class="pyq-list">
{pyq_block}
  </div>
  <p class="pyq-note">All previous year question papers are sourced from official board examinations.</p>
</div>"""


# ── Pipeline background job store ─────────────────────────────────────────────
# Simple in-memory store for pipeline job status (TTL ~1 hour).
# Keys are job UUIDs. Values: { status, progress, message, result, started_at }
_pipeline_jobs: dict = {}

def _pipeline_job_gc():
    """Remove jobs older than 1 hour."""
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    stale = [k for k, v in _pipeline_jobs.items() if v.get("started_at", 0) < cutoff]
    for k in stale:
        _pipeline_jobs.pop(k, None)


class PipelineAutoGenerateRequest(BaseModel):
    subject_id: str
    skip_existing: bool = False


@router.get("/admin/pipeline/status/{job_id}")
async def admin_pipeline_status(job_id: str, admin: dict = Depends(get_admin_user)):
    """Poll the status of a background pipeline job."""
    job = _pipeline_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return job


async def _pipeline_auto_generate_worker(job_id: str, subject_id: str, skip_existing: bool = False):
    """Background worker: runs the full pipeline and updates _pipeline_jobs[job_id]."""
    try:
        result = await _pipeline_auto_generate_core(subject_id, job_id, skip_existing=skip_existing)
        _pipeline_jobs[job_id].update({
            "status": "complete",
            "progress": 100,
            "message": "Pipeline finished",
            "result": result,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        _pipeline_jobs[job_id].update({
            "status": "error",
            "progress": 100,
            "message": str(exc)[:200],
            "result": None,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error(f"Pipeline job {job_id} failed: {exc}")
    finally:
        _pipeline_job_gc()


@router.post("/admin/pipeline/auto-generate")
async def admin_pipeline_auto_generate(body: PipelineAutoGenerateRequest, background_tasks: BackgroundTasks, admin: dict = Depends(get_admin_user)):
    """
    1-Click Full Subject Pipeline (async).
    Returns job_id immediately; poll /admin/pipeline/status/{job_id} for progress.
    """
    subject_id = body.subject_id.strip()
    if not subject_id:
        raise HTTPException(status_code=400, detail="subject_id is required")

    subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    job_id = str(uuid.uuid4())
    _pipeline_jobs[job_id] = {
        "job_id": job_id,
        "subject_id": subject_id,
        "status": "running",
        "progress": 0,
        "message": "Pipeline starting…",
        "result": None,
        "chapter_progress": {},
        "started_at": datetime.now(timezone.utc).timestamp(),
    }
    background_tasks.add_task(_pipeline_auto_generate_worker, job_id, subject_id, body.skip_existing)
    return {"job_id": job_id, "status": "running"}


async def _pipeline_process_one_chapter(
    chapter: dict,
    *,
    subject_id: str,
    subject_name: str,
    class_name: str,
    paper_type: str,
    board_slug: str,
    board_display: str = "Assamboard",
    class_slug: str,
    now_iso: str,
    pyq_docs: list,
    semaphore: asyncio.Semaphore,
    done_counter: dict,
    total_chapters: int,
    job_id: str,
    skip_existing: bool = False,
) -> dict:
    """Process a single chapter: notes → MCQs → flashcards → geo-blogs (parallel) → PYQ.
    All generated content is collected first, then written atomically at the end.
    If skip_existing=True, reuses existing notes/PYQs/flashcards and only runs blogs+PYQ HTML+sitemap.
    """
    def _update_stage(stage: str, detail: str = ""):
        if job_id and job_id in _pipeline_jobs:
            job = _pipeline_jobs[job_id]
            ch_progress = job.get("chapter_progress", {})
            ch_progress[chapter.get("id", "")] = {"title": (chapter.get("title") or "")[:40], "stage": stage, "detail": detail}
            pct = int(5 + (done_counter["done"] / max(total_chapters, 1)) * 88)
            job.update({"progress": pct, "message": f"Chapter {done_counter['done']}/{total_chapters}: {stage}", "chapter_progress": ch_progress})

    async with semaphore:
        chapter_id    = chapter.get("id", "")
        chapter_title = (chapter.get("title") or "").strip()
        chapter_slug  = chapter.get("slug") or _pipeline_slugify(chapter_title)

        chapter_result = {
            "chapter_id":       chapter_id,
            "chapter_title":    chapter_title,
            "notes_generated":  False,
            "topic_pyq_count":  0,
            "mark_wise_count":  0,
            "flashcards_count": 0,
            "blogs_count":      0,
            "pyq_page":         False,
            "errors":           [],
        }

        if not chapter_title:
            return {"skipped": True, "result": chapter_result}

        _update_stage("notes", "Generating notes…")

        existing_content = (chapter.get("content") or "").strip()
        notes_content = existing_content
        generated_notes = None
        if skip_existing and len(existing_content) > 100:
            chapter_result["notes_generated"] = False
        else:
            try:
                generated_notes = await _pipeline_generate_chapter_notes(chapter, subject_name, class_name, paper_type)
                if generated_notes:
                    notes_content = generated_notes
                    chapter_result["notes_generated"] = True
            except Exception as e:
                chapter_result["errors"].append(f"notes: {str(e)[:80]}")

        if not notes_content:
            done_counter["done"] += 1
            _update_stage("skipped", "No content")
            return {"skipped": True, "result": chapter_result}

        _update_stage("questions", "Generating questions & flashcards…")

        pyq_err = None
        mw_err  = None
        fc_err  = None
        topic_pyqs = None
        mark_wise_result = None
        flashcards = None
        _existing_pyqs = None
        _existing_mw   = None
        _existing_fc   = None
        if skip_existing:
            _existing_pyqs = await db.topic_pyq_collections.find_one({"chapter_id": chapter_id}, {"_id": 0, "total": 1})
            _existing_mw   = await db.ai_pyq_collections.find_one({"chapter_id": chapter_id}, {"_id": 0, "total": 1})
            _existing_fc   = await db.flashcard_collections.find_one({"chapter_id": chapter_id}, {"_id": 0, "total": 1})

        if skip_existing and _existing_pyqs and _existing_mw and _existing_fc:
            chapter_result["topic_pyq_count"]   = _existing_pyqs.get("total", 0)
            chapter_result["mark_wise_count"]    = _existing_mw.get("total", 0)
            chapter_result["flashcards_count"]   = _existing_fc.get("total", 0)
        else:
            ch_topics = chapter.get("topics") or []
            pyq_task = _pipeline_generate_topic_pyq(notes_content, subject_name, chapter_title, class_name, count=20)
            mw_task  = _pipeline_generate_mark_wise_pyq(notes_content, subject_name, chapter_title, class_name, paper_type=paper_type, topics=ch_topics)
            fc_task  = _pipeline_generate_flashcards(notes_content, subject_name, chapter_title, class_name, count=15, topics=ch_topics)
            (topic_pyqs, pyq_err), (mark_wise_result, mw_err), (flashcards, fc_err) = await asyncio.gather(
                _safe(pyq_task), _safe(mw_task), _safe(fc_task)
            )
            if pyq_err:
                chapter_result["errors"].append(f"topic-pyqs: {str(pyq_err)[:60]}")
            if mw_err:
                chapter_result["errors"].append(f"mark-wise: {str(mw_err)[:60]}")
            if fc_err:
                chapter_result["errors"].append(f"flashcards: {str(fc_err)[:60]}")

        _update_stage("blogs", "Generating geo-SEO blogs…")

        _existing_geo_count = 0
        geo_blog_urls = []
        if skip_existing:
            try:
                _existing_geo_count = await db.cms_documents.count_documents({
                    "linked_chapter_id": chapter_id, "category": "geo-blog"
                })
            except Exception:
                pass
        if skip_existing and _existing_geo_count >= len(GEO_CITIES):
            logger.info(f"[Pipeline] Skipping geo-blogs for '{chapter_title}' — {_existing_geo_count} already exist")
            chapter_result["blogs_count"] = _existing_geo_count
        else:
            blog_tasks = [
                _safe(_pipeline_generate_geo_seo_blog(
                    subject_name=subject_name, chapter_title=chapter_title,
                    content=notes_content, geo_location=city,
                    board_slug=board_slug, class_slug=class_slug, chapter_slug=chapter_slug,
                ))
                for city in GEO_CITIES
            ]
            blog_results = await asyncio.gather(*blog_tasks)
            for city, (blog_data, blog_err) in zip(GEO_CITIES, blog_results):
                if blog_err or not blog_data:
                    chapter_result["errors"].append(f"geo-blog-{city}: {str(blog_err)[:60]}" if blog_err else f"geo-blog-{city}: empty")
                    continue
                try:
                    blog_slug = blog_data["seo_slug"]
                    await db.cms_documents.update_one(
                        {"seo_slug": blog_slug},
                        {"$set": {
                            "id": str(uuid.uuid4()),
                            "title": blog_data["title"], "seo_slug": blog_slug,
                            "meta_description": blog_data["meta_description"],
                            "content": blog_data["article_body"],
                            "primary_keyword": blog_data["primary_keyword"],
                            "keywords": blog_data.get("keywords", []),
                            "geo_location": city,
                            "linked_subject_id": subject_id, "linked_subject_name": subject_name,
                            "linked_chapter_id": chapter_id, "linked_chapter_title": chapter_title,
                            "status": "published", "category": "geo-blog",
                            "schema_type": "Article", "pipeline_generated": True,
                            "created_at": now_iso, "updated_at": now_iso,
                        }},
                        upsert=True,
                    )
                    geo_blog_urls.append(f"/learn/{blog_slug}")
                    chapter_result["blogs_count"] += 1
                    try:
                        await _embed_cms_document(blog_slug, blog_data["article_body"], blog_data["title"])
                    except Exception:
                        pass
                except Exception as e:
                    chapter_result["errors"].append(f"geo-blog-{city}-save: {str(e)[:60]}")

        _update_stage("saving", "Atomic write…")

        # ── ATOMIC CHAPTER UPDATE: write notes + questions + flashcards in one operation ──
        chapter_atomic_update = {"updated_at": now_iso}
        if generated_notes:
            # Task #494 — recover the formatter audit value the pipeline
            # generator stashed in Redis under the SAME cache_key shape
            # used by `_pipeline_generate_chapter_notes` (see L2062:
            # ``pipeline_notes:{chapter_id}:{md5(title+subject_name)}``).
            # The earlier review caught a key-shape mismatch where the
            # reader used `_pipeline_content_hash(...)` while the writer
            # used `chapter_id + md5`; this canonical form keeps the two
            # sides in lock-step. Defaults to "passthrough" if the
            # helper had no formatter telemetry to report (e.g. cache
            # miss after TTL or dispatcher dual-outage path).
            _fmt_cache_key = (
                f"pipeline_notes:{chapter_id}:"
                f"{hashlib.md5((chapter_title + subject_name).lower().encode()).hexdigest()}"
            )
            _formatted_by = "passthrough"
            try:
                _formatted_by = _redis_get("pipeline_notes_fmt", _fmt_cache_key) or "passthrough"
            except Exception:
                pass
            chapter_atomic_update.update({
                "content": generated_notes,
                "content_type": "notes",
                "notes_generated": True,
                "notes_generated_at": now_iso,
                "formatted_by": _formatted_by,
            })
        if mark_wise_result and mark_wise_result.get("pyqs"):
            chapter_atomic_update["has_important_questions"] = True
            chapter_atomic_update["mark_wise_questions"] = mark_wise_result["mark_wise"]
            chapter_atomic_update["mark_wise_count"] = mark_wise_result["total"]
        if flashcards:
            chapter_atomic_update["has_flashcards"] = True
            chapter_atomic_update["flashcard_summary"] = [
                {"front": fc.get("front", "")[:100], "type": fc.get("type", "")}
                for fc in (flashcards or [])[:5]
            ]
            chapter_atomic_update["flashcard_count"] = len(flashcards)
        chapter_atomic_update["content_synced_at"] = now_iso

        try:
            await db.chapters.update_one({"id": chapter_id}, {"$set": chapter_atomic_update})
        except Exception as e:
            chapter_result["errors"].append(f"atomic-save: {str(e)[:60]}")

        if generated_notes:
            try:
                await auto_chunk_content(chapter_id=chapter_id, content=generated_notes, subject_id=subject_id, category="notes", topics=ch_topics, chapter_title=chapter_title)
            except Exception as chunk_err:
                chapter_result["errors"].append(f"chunking: {str(chunk_err)[:60]}")
            try:
                await _embed_and_store_chapter(chapter_id, generated_notes, chapter_title)
            except Exception as embed_err:
                chapter_result["errors"].append(f"embedding: {str(embed_err)[:60]}")

        if topic_pyqs:
            try:
                await db.topic_pyq_collections.update_one(
                    {"chapter_id": chapter_id, "pipeline_generated": True},
                    {"$set": {
                        "id": str(uuid.uuid4()),
                        "subject_id": subject_id, "subject_name": subject_name,
                        "chapter_id": chapter_id, "chapter_title": chapter_title,
                        "pyqs": topic_pyqs, "total": len(topic_pyqs),
                        "pipeline_generated": True, "created_at": now_iso,
                    }},
                    upsert=True,
                )
                chapter_result["topic_pyq_count"] = len(topic_pyqs)
            except Exception as e:
                chapter_result["errors"].append(f"topic-pyq-save: {str(e)[:60]}")

        if mark_wise_result and mark_wise_result.get("pyqs"):
            try:
                mw_doc = {
                    "id":            str(uuid.uuid4()),
                    "subject_id":    subject_id,
                    "subject_name":  subject_name,
                    "chapter_id":    chapter_id,
                    "chapter_title": chapter_title,
                    "pyqs":          mark_wise_result["pyqs"],
                    "mark_wise":     mark_wise_result["mark_wise"],
                    "total":         mark_wise_result["total"],
                    "source":        "pipeline_mark_wise",
                    "ai_generated":  True,
                    "pipeline_generated": True,
                    "created_at":    now_iso,
                    "updated_at":    now_iso,
                }
                await db.ai_pyq_collections.update_one(
                    {"chapter_id": chapter_id},
                    {"$set": mw_doc},
                    upsert=True,
                )
                chapter_result["mark_wise_count"] = mark_wise_result["total"]
            except Exception as e:
                chapter_result["errors"].append(f"mark-wise-save: {str(e)[:60]}")

        if flashcards:
            try:
                await db.flashcard_collections.update_one(
                    {"chapter_id": chapter_id, "pipeline_generated": True},
                    {"$set": {
                        "id": str(uuid.uuid4()),
                        "subject_id": subject_id, "subject_name": subject_name,
                        "chapter_id": chapter_id, "chapter_title": chapter_title,
                        "flashcards": flashcards, "total": len(flashcards),
                        "pipeline_generated": True, "created_at": now_iso,
                    }},
                    upsert=True,
                )
                chapter_result["flashcards_count"] = len(flashcards)
            except Exception as e:
                chapter_result["errors"].append(f"flashcards-save: {str(e)[:60]}")

        try:
            pyq_html = await _pipeline_generate_pyq_html(chapter, subject_name, pyq_docs)
            pyq_slug = f"pyq-{board_slug}-{class_slug}-{chapter_slug}"
            await db.cms_documents.update_one(
                {"seo_slug": pyq_slug},
                {"$set": {
                    "id": str(uuid.uuid4()),
                    "title": f"PYQ: {chapter_title} — {subject_name}",
                    "seo_slug": pyq_slug,
                    "meta_description": f"Previous year questions for {chapter_title} ({subject_name}) — {board_display} board exams. Download PYQ papers on Syrabit.ai.",
                    "content": pyq_html, "content_html": pyq_html,
                    "linked_subject_id": subject_id, "linked_subject_name": subject_name,
                    "linked_chapter_id": chapter_id, "linked_chapter_title": chapter_title,
                    "status": "published", "category": "pyq",
                    "pipeline_generated": True, "created_at": now_iso, "updated_at": now_iso,
                }},
                upsert=True,
            )
            chapter_result["pyq_page"] = True
            try:
                await _embed_cms_document(pyq_slug, pyq_html, f"PYQ: {chapter_title} — {subject_name}")
            except Exception:
                pass
        except Exception as e:
            chapter_result["errors"].append(f"pyq-page: {str(e)[:80]}")

        try:
            from routes.bot_discovery import indexnow_batcher
            indexnow_paths = []
            for blog_url in geo_blog_urls:
                indexnow_paths.append(blog_url)
            if chapter_result.get("pyq_page"):
                indexnow_paths.append(f"/learn/pyq-{board_slug}-{class_slug}-{chapter_slug}")
            if indexnow_paths:
                await indexnow_batcher.queue_raw_paths(indexnow_paths)
        except Exception:
            pass

        done_counter["done"] += 1
        _update_stage("done", "Complete")

        return {"skipped": False, "result": chapter_result, "blog_urls": geo_blog_urls, "mcqs": len(topic_pyqs or []), "flashcards": len(flashcards or []), "pyq": chapter_result["pyq_page"]}


async def _safe(coro):
    """Run a coroutine; return (result, None) on success or (None, exc) on error."""
    try:
        return await coro, None
    except Exception as e:
        return None, e


async def _pipeline_auto_generate_core(subject_id: str, job_id: str = "", skip_existing: bool = False):
    """Core pipeline logic — extracted so it can run as a background task."""
    subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subject:
        raise ValueError(f"Subject {subject_id} not found")

    subject_name = subject.get("name", "")
    paper_type   = subject.get("paper_type", "")
    class_name   = subject.get("className", "") or subject.get("class_name", "")

    stream_id = subject.get("stream_id", "")
    board_slug = "assamboard"
    board_display = "Assamboard"
    class_slug = _pipeline_slugify(class_name or "class-12")

    if stream_id:
        stream_doc = await db.streams.find_one({"id": stream_id}, {"_id": 0})
        if stream_doc:
            class_id = stream_doc.get("class_id", "")
            if class_id:
                class_doc = await db.classes.find_one({"id": class_id}, {"_id": 0})
                if class_doc:
                    class_slug = class_doc.get("slug") or _pipeline_slugify(class_doc.get("name", ""))
                    board_id = class_doc.get("board_id", "")
                    if board_id:
                        board_doc = await db.boards.find_one({"id": board_id}, {"_id": 0})
                        if board_doc:
                            board_slug = board_doc.get("slug") or _pipeline_slugify(board_doc.get("name", ""))
                            board_display = board_doc.get("name", "Assamboard")

    # Load chapters
    chapters = await db.chapters.find(
        {"subject_id": subject_id}, {"_id": 0}
    ).sort("order_index", 1).to_list(100)

    if not chapters:
        return {"subject_id": subject_id, "status": "no_chapters", "message": "No chapters found for this subject"}

    now_iso = datetime.now(timezone.utc).isoformat()

    # Load PYQs for this subject (for PYQ HTML pages)
    pyq_docs = await db.pyq_uploads.find(
        {"subject_id": subject_id}, {"_id": 0}
    ).sort("exam_year", -1).to_list(20)

    # ── Parallel chapter processing (semaphore=4 to respect LLM rate limits) ─
    semaphore     = asyncio.Semaphore(4)
    done_counter  = {"done": 0}
    total_chapters = len(chapters)

    if job_id and job_id in _pipeline_jobs:
        _pipeline_jobs[job_id].update({"progress": 5, "message": f"Starting parallel processing for {total_chapters} chapters…"})

    tasks = [
        _pipeline_process_one_chapter(
            ch,
            subject_id=subject_id, subject_name=subject_name,
            class_name=class_name, paper_type=paper_type,
            board_slug=board_slug, board_display=board_display,
            class_slug=class_slug,
            now_iso=now_iso, pyq_docs=pyq_docs,
            semaphore=semaphore, done_counter=done_counter,
            total_chapters=total_chapters, job_id=job_id,
            skip_existing=skip_existing,
        )
        for ch in chapters
    ]
    chapter_outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Aggregate results ─────────────────────────────────────────────────────
    summary = {
        "subject_id": subject_id, "subject_name": subject_name,
        "chapters_processed": 0, "chapters_skipped": 0,
        "total_topic_pyqs": 0, "total_mark_wise_pyqs": 0, "total_flashcards": 0,
        "total_blogs": 0, "total_pyq_pages": 0,
        "blog_urls": [], "chapter_results": [],
        "sitemap_pinged": False, "ping_status": "",
    }

    for outcome in chapter_outcomes:
        if isinstance(outcome, Exception):
            summary["chapters_skipped"] += 1
            continue
        if outcome.get("skipped"):
            summary["chapters_skipped"] += 1
            summary["chapter_results"].append(outcome["result"])
            continue
        r = outcome["result"]
        summary["chapters_processed"] += 1
        summary["total_topic_pyqs"]     += r.get("topic_pyq_count", 0)
        summary["total_mark_wise_pyqs"] += r.get("mark_wise_count", 0)
        summary["total_flashcards"]     += r.get("flashcards_count", 0)
        summary["total_blogs"]          += r.get("blogs_count", 0)
        if r.get("pyq_page"):
            summary["total_pyq_pages"] += 1
        summary["blog_urls"].extend(outcome.get("blog_urls", []))
        summary["chapter_results"].append(r)

    # ── Verification + retry pass ──────────────────────────────────────────
    failed_ch_results = []
    for ch_idx, outcome in enumerate(chapter_outcomes):
        src_chapter = chapters[ch_idx] if ch_idx < len(chapters) else {}
        src_ch_id = src_chapter.get("id", "")
        src_ch_title = (src_chapter.get("title") or "").strip()

        if isinstance(outcome, Exception):
            failed_ch_results.append({
                "chapter_id": src_ch_id, "chapter_title": src_ch_title,
                "word_count": 0, "has_chunks": False, "has_embedding": False,
                "errors": [f"exception: {str(outcome)[:120]}"],
                "reason": "exception",
            })
            continue

        r = outcome.get("result", {})
        ch_id = r.get("chapter_id", "") or src_ch_id
        ch_title = r.get("chapter_title", "") or src_ch_title

        if not ch_id:
            continue

        ch_doc = await db.chapters.find_one({"id": ch_id}, {"_id": 0, "content": 1})
        actual_content = (ch_doc.get("content") or "") if ch_doc else ""
        word_count = len(actual_content.split()) if actual_content.strip() else 0
        chunk_count = await db.chunks.count_documents({"chapter_id": ch_id})
        embed_ok = False
        try:
            from retrievers import get_retriever as _gr
            from syllabus_embedder import _make_vector_id
            _r = await _gr()
            if _r.is_configured():
                vecs = await _r.get_by_ids([_make_vector_id(ch_id, "chapter")])
                embed_ok = len(vecs) > 0
        except Exception as _embed_err:
            logger.warning(f"Embedding check failed for {ch_id}: {_embed_err}")
            embed_ok = False

        content_ok = word_count >= 500
        chunks_ok = chunk_count > 0

        if not content_ok or not chunks_ok or not embed_ok:
            failed_ch_results.append({
                "chapter_id": ch_id, "chapter_title": ch_title,
                "word_count": word_count, "has_chunks": chunks_ok, "has_embedding": embed_ok,
                "errors": r.get("errors", []),
                "reason": "incomplete",
            })

    if job_id and job_id in _pipeline_jobs:
        _pipeline_jobs[job_id].update({"progress": 94, "message": f"Verification: {len(failed_ch_results)} chapter(s) need retry…"})

    retry_successes = 0
    for retry_round in range(2):
        if not failed_ch_results:
            break
        still_failed = []
        for fc in failed_ch_results:
            ch_id = fc.get("chapter_id", "")
            ch_title = fc.get("chapter_title", "")
            if not ch_id:
                still_failed.append(fc)
                continue

            chapter_doc = None
            for ch in chapters:
                if ch.get("id") == ch_id:
                    chapter_doc = ch
                    break
            if not chapter_doc:
                still_failed.append(fc)
                continue

            try:
                retry_outcome = await _pipeline_process_one_chapter(
                    chapter_doc,
                    subject_id=subject_id, subject_name=subject_name,
                    class_name=class_name, paper_type=paper_type,
                    board_slug=board_slug, board_display=board_display,
                    class_slug=class_slug,
                    now_iso=now_iso, pyq_docs=pyq_docs,
                    semaphore=asyncio.Semaphore(1), done_counter={"done": 0},
                    total_chapters=1, job_id="",
                    skip_existing=False,
                )
                if isinstance(retry_outcome, Exception) or retry_outcome.get("skipped"):
                    still_failed.append(fc)
                    continue

                rr = retry_outcome.get("result", {})
                r_ch_doc = await db.chapters.find_one({"id": ch_id}, {"_id": 0, "content": 1})
                r_content = (r_ch_doc.get("content") or "") if r_ch_doc else ""
                r_wc = len(r_content.split()) if r_content.strip() else 0
                r_chunks = await db.chunks.count_documents({"chapter_id": ch_id})
                r_embed = False
                try:
                    from retrievers import get_retriever as _gr
                    from syllabus_embedder import _make_vector_id
                    _r = await _gr()
                    if _r.is_configured():
                        r_vecs = await _r.get_by_ids([_make_vector_id(ch_id, "chapter")])
                        r_embed = len(r_vecs) > 0
                except Exception as _r_embed_err:
                    logger.warning(f"Retry embedding check failed for {ch_id}: {_r_embed_err}")
                    r_embed = False

                if r_wc >= 500 and r_chunks > 0 and r_embed:
                    retry_successes += 1
                    rr_idx = next((i for i, cr in enumerate(summary["chapter_results"]) if cr.get("chapter_id") == ch_id), None)
                    if rr_idx is not None:
                        summary["chapter_results"][rr_idx] = rr
                    else:
                        summary["chapter_results"].append(rr)
                else:
                    fc["word_count"] = r_wc
                    fc["has_chunks"] = r_chunks > 0
                    fc["has_embedding"] = r_embed is not None
                    retry_reasons = []
                    if r_wc < 500:
                        retry_reasons.append(f"content still thin ({r_wc} words)")
                    if r_chunks == 0:
                        retry_reasons.append("chunking still failed")
                    if not r_embed:
                        retry_reasons.append("embedding still missing")
                    if retry_reasons:
                        fc.setdefault("errors", []).append(f"retry-round-{retry_round+1}: {'; '.join(retry_reasons)}")
                    still_failed.append(fc)
            except Exception as retry_err:
                fc.setdefault("errors", []).append(f"retry: {str(retry_err)[:80]}")
                still_failed.append(fc)
        failed_ch_results = still_failed

    summary["failed_chapters"] = [
        {"chapter_id": fc.get("chapter_id", ""), "chapter_title": fc.get("chapter_title", ""),
         "word_count": fc.get("word_count", 0), "has_chunks": fc.get("has_chunks", False),
         "has_embedding": fc.get("has_embedding", False), "errors": fc.get("errors", []),
         "reason": fc.get("reason", "unknown")}
        for fc in failed_ch_results
    ]
    summary["verification_status"] = "all_passed" if not failed_ch_results else "some_failed"
    summary["retry_successes"] = retry_successes

    if job_id and job_id in _pipeline_jobs:
        _pipeline_jobs[job_id].update({
            "verification_status": summary["verification_status"],
            "failed_chapters": summary["failed_chapters"],
        })

    _invalidate_content_cache("chapters")

    # ── Step 6: Verify Sitemap is Alive (self-check) ─────────────────────────
    # Google deprecated their sitemap ping endpoint in 2023 (returns 404).
    # Instead we self-verify our own sitemap endpoint on localhost.
    try:
        local_port = int(os.environ.get("PORT", "5000"))
        sitemap_local = f"http://localhost:{local_port}/api/seo/sitemap-index.xml"
        async with httpx.AsyncClient(timeout=10.0) as client:
            smap_resp = await client.get(sitemap_local)
            summary["sitemap_pinged"] = True
            if smap_resp.status_code == 200:
                summary["ping_status"] = "Sitemap OK"
            else:
                summary["ping_status"] = f"Sitemap HTTP {smap_resp.status_code}"
            logger.info(f"Sitemap self-check: {smap_resp.status_code} at {sitemap_local}")
    except Exception as e:
        summary["ping_status"] = f"check failed: {str(e)[:50]}"
        logger.warning(f"Sitemap self-check failed: {e}")

    try:
        from routes.bot_discovery import indexnow_batcher
        flushed = await indexnow_batcher.flush_force(source="pipeline_auto_generate")
        logger.info(f"IndexNow flush after pipeline: {flushed} URLs pushed")
        summary["indexnow_urls_pushed"] = flushed
    except Exception as e:
        logger.debug(f"IndexNow pipeline flush failed: {e}")
        summary["indexnow_urls_pushed"] = 0

    logger.info(
        f"Pipeline complete: subject={subject_name}, chapters={summary['chapters_processed']}, "
        f"topic_pyqs={summary['total_topic_pyqs']}, flashcards={summary['total_flashcards']}, "
        f"blogs={summary['total_blogs']}, pyq_pages={summary['total_pyq_pages']}"
    )

    return summary


@router.get("/admin/intelligence/overview")
async def admin_intelligence_overview(admin: dict = Depends(get_admin_user)):
    from llm import get_llm_provider_stats
    from rag import get_vector_search_stats, get_pipeline_stats
    from pipeline import get_pipeline_stats as get_multi_llm_pipeline_stats

    llm_stats = get_llm_provider_stats(3600)
    vector_stats = get_vector_search_stats(3600)
    pipeline_stats = get_pipeline_stats(86400)

    total_chapters = await db.chapters.count_documents({})
    chapters_with_content = await db.chapters.count_documents({"content": {"$exists": True, "$ne": ""}})
    chapters_embedded = await db.chapters.count_documents({"embedding": {"$exists": True}})
    total_chunks = await db.chunks.count_documents({})

    thin_chapters = []
    thin_cursor = db.chapters.find(
        {"content": {"$exists": True, "$ne": ""}},
        {"_id": 0, "id": 1, "title": 1, "content": 1, "subject_id": 1, "needs_review": 1}
    )
    async for ch in thin_cursor:
        wc = len((ch.get("content") or "").split())
        if wc < 600:
            chunk_count = await db.chunks.count_documents({"chapter_id": ch["id"]})
            thin_chapters.append({
                "id": ch["id"],
                "title": ch.get("title", ""),
                "word_count": wc,
                "chunk_count": chunk_count,
                "has_embedding": False,
                "needs_review": ch.get("needs_review", False),
            })

    no_embed = await db.chapters.count_documents({
        "content": {"$exists": True, "$ne": ""},
        "embedding": {"$exists": False},
    })
    low_chunk = []
    for tc in thin_chapters:
        if tc["chunk_count"] < 3:
            low_chunk.append(tc["id"])

    multi_llm_stats = get_multi_llm_pipeline_stats(3600)

    return {
        "llm_health": llm_stats,
        "vector_search": vector_stats,
        "pipeline": pipeline_stats,
        "multi_llm_pipeline": multi_llm_stats,
        "content": {
            "total_chapters": total_chapters,
            "with_content": chapters_with_content,
            "embedded": chapters_embedded,
            "total_chunks": total_chunks,
            "chunks_per_chapter": round(total_chunks / max(chapters_with_content, 1), 1),
            "embedding_coverage_pct": round(chapters_embedded / max(chapters_with_content, 1) * 100, 1),
        },
        "content_health": {
            "thin_chapters": thin_chapters[:50],
            "thin_count": len(thin_chapters),
            "no_embedding_count": no_embed,
            "low_chunk_ids": low_chunk[:20],
        },
    }


@router.post("/admin/content/auto-heal")
async def admin_content_auto_heal(admin: dict = Depends(get_admin_user)):
    from rag import auto_chunk_content, record_pipeline_run
    import time as _t

    t0 = _t.perf_counter()
    thin_cursor = db.chapters.find(
        {"content": {"$exists": True, "$ne": ""}},
        {"_id": 0, "id": 1, "title": 1, "content": 1, "subject_id": 1, "description": 1, "topics": 1}
    )

    regen_results = []
    async for ch in thin_cursor:
        wc = len((ch.get("content") or "").split())
        if wc >= 600:
            continue

        title = ch.get("title", "")
        desc = ch.get("description", "")
        topics = ch.get("topics") or []
        topic_text = ", ".join(t if isinstance(t, str) else t.get("name", "") for t in topics)

        prompt = (
            f"Write comprehensive, exam-focused study notes for the chapter: **{title}**.\n"
            f"Subject context: {desc}\n"
            f"Topics to cover: {topic_text}\n\n"
            "Requirements:\n"
            "- Minimum 800 words\n"
            "- Use markdown headings, bullet points\n"
            "- Include key definitions and formulas\n"
            "- Write in clear, student-friendly language"
        )
        try:
            new_content = await call_llm_api_content(
                [{"role": "system", "content": "You are an expert educational content writer for Indian university students."},
                 {"role": "user", "content": prompt}],
                model="gemini-2.5-flash", max_tokens=4096
            )
            new_wc = len(new_content.split())
            if new_wc >= 600:
                old_content = ch.get("content", "")
                await db.chapters.update_one(
                    {"id": ch["id"]},
                    {"$set": {
                        "content": new_content,
                        "needs_review": False,
                        "content_version": {
                            "previous_word_count": wc,
                            "new_word_count": new_wc,
                            "regenerated_at": datetime.now(timezone.utc).isoformat(),
                            "reason": "auto_heal_thin_content",
                            "previous_content_hash": hashlib.md5(old_content.encode()).hexdigest(),
                        },
                    }}
                )
                chunks = await auto_chunk_content(ch["id"], new_content, ch.get("subject_id"), category=ch.get("category", "notes"), topics=ch.get("topics"), chapter_title=title)
                regen_results.append({"id": ch["id"], "title": title, "old_wc": wc, "new_wc": new_wc, "chunks": len(chunks), "status": "regenerated"})
            else:
                await db.chapters.update_one({"id": ch["id"]}, {"$set": {"needs_review": True}})
                regen_results.append({"id": ch["id"], "title": title, "old_wc": wc, "new_wc": new_wc, "status": "still_thin"})
        except Exception as e:
            regen_results.append({"id": ch["id"], "title": title, "old_wc": wc, "status": "error", "error": str(e)[:100]})

    dur = int((_t.perf_counter() - t0) * 1000)
    record_pipeline_run(
        "auto_heal", "all",
        success=any(r["status"] == "regenerated" for r in regen_results),
        chapters=len(regen_results),
        duration_ms=dur,
    )

    return {
        "healed": sum(1 for r in regen_results if r["status"] == "regenerated"),
        "still_thin": sum(1 for r in regen_results if r["status"] == "still_thin"),
        "errors": sum(1 for r in regen_results if r["status"] == "error"),
        "details": regen_results,
        "duration_ms": dur,
    }


@router.get("/admin/content/version-history/{chapter_id}")
async def admin_content_version_history(chapter_id: str, admin: dict = Depends(get_admin_user)):
    ch = await db.chapters.find_one({"id": chapter_id}, {"_id": 0, "id": 1, "title": 1, "content": 1, "content_version": 1, "needs_review": 1})
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {
        "chapter_id": ch["id"],
        "title": ch.get("title", ""),
        "current_word_count": len((ch.get("content") or "").split()),
        "needs_review": ch.get("needs_review", False),
        "version_info": ch.get("content_version"),
    }


@router.get("/admin/security/spoofed-bots")
async def admin_spoofed_bots_dashboard(
    days: int = Query(7, ge=1, le=90),
    admin: dict = Depends(get_admin_user),
):
    from metrics import _metrics
    spoof_stats = _metrics.get_spoof_stats()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    daily_pipeline = [
        {"$match": {"date": {"$gte": cutoff}}},
        {"$group": {"_id": "$date", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    bot_pipeline = [
        {"$match": {"date": {"$gte": cutoff}}},
        {"$group": {"_id": "$claimed_bot", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]
    repeat_ip_pipeline = [
        {"$match": {"date": {"$gte": cutoff}}},
        {"$group": {"_id": "$ip_hash", "count": {"$sum": 1}, "bots": {"$addToSet": "$claimed_bot"}}},
        {"$match": {"count": {"$gte": 5}}},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]
    recent_pipeline = [
        {"$match": {"date": {"$gte": cutoff}}},
        {"$sort": {"timestamp": -1}},
        {"$limit": 50},
        {"$project": {"_id": 0, "ip_hash": 1, "claimed_bot": 1, "user_agent": 1, "path": 1, "timestamp": 1, "date": 1}},
    ]

    try:
        daily = await db.bot_spoof_attempts.aggregate(daily_pipeline).to_list(90)
        by_bot = await db.bot_spoof_attempts.aggregate(bot_pipeline).to_list(20)
        repeat_ips = await db.bot_spoof_attempts.aggregate(repeat_ip_pipeline).to_list(20)
        recent = await db.bot_spoof_attempts.aggregate(recent_pipeline).to_list(50)
        total_period = await db.bot_spoof_attempts.count_documents({"date": {"$gte": cutoff}})
    except Exception:
        daily, by_bot, repeat_ips, recent, total_period = [], [], [], [], 0

    return {
        "period_days": days,
        "realtime": {
            "spoof_rpm": spoof_stats["rpm"],
            "session_total": spoof_stats["total"],
            "session_by_bot": spoof_stats["by_bot"],
        },
        "period_total": total_period,
        "daily_counts": [{"date": d["_id"], "count": d["count"]} for d in daily],
        "by_claimed_bot": [{"bot": b["_id"], "count": b["count"]} for b in by_bot],
        "repeat_offender_ips": [
            {"ip_hash": r["_id"], "attempts": r["count"], "claimed_bots": r["bots"]}
            for r in repeat_ips
        ],
        "recent_attempts": recent,
    }


@router.get("/admin/security/ttl-monitor")
async def admin_ttl_monitor(
    admin: dict = Depends(get_admin_user),
):
    try:
        total_docs = await db.bot_spoof_attempts.count_documents({})

        now = datetime.now(timezone.utc)

        ttl_seconds = 90 * 24 * 3600
        index_info = {}
        try:
            indexes = await db.bot_spoof_attempts.index_information()
            for idx_name, idx_data in indexes.items():
                if "expireAfterSeconds" in idx_data:
                    ttl_seconds = idx_data["expireAfterSeconds"]
                    index_info = {
                        "name": idx_name,
                        "expireAfterSeconds": ttl_seconds,
                        "ttl_days": round(ttl_seconds / 86400, 1),
                    }
                    break
        except Exception:
            pass

        ttl_days = ttl_seconds / 86400
        bucket_defs = [
            ("< 1 day", timedelta(days=1)),
            ("1-7 days", timedelta(days=7)),
            ("7-30 days", timedelta(days=30)),
            (f"30-{int(ttl_days)} days", timedelta(seconds=ttl_seconds)),
            (f"> {int(ttl_days)} days (past TTL)", None),
        ]
        if ttl_days > 60:
            bucket_defs = [
                ("< 1 day", timedelta(days=1)),
                ("1-7 days", timedelta(days=7)),
                ("7-30 days", timedelta(days=30)),
                ("30-60 days", timedelta(days=60)),
                (f"60-{int(ttl_days)} days", timedelta(seconds=ttl_seconds)),
                (f"> {int(ttl_days)} days (past TTL)", None),
            ]

        age_buckets = []
        prev_cutoff = now
        for label, delta in bucket_defs:
            if delta is not None:
                bucket_cutoff = now - delta
                count = await db.bot_spoof_attempts.count_documents({
                    "timestamp": {"$lt": prev_cutoff, "$gte": bucket_cutoff}
                })
                prev_cutoff = bucket_cutoff
            else:
                count = await db.bot_spoof_attempts.count_documents({
                    "timestamp": {"$lt": prev_cutoff}
                })
            age_buckets.append({"label": label, "count": count})

        expired_count = age_buckets[-1]["count"] if age_buckets else 0

        if expired_count > 0:
            health_status = "warning"
            health_message = f"{expired_count} documents older than {int(ttl_days)} days still exist — TTL cleanup may be delayed"
        elif total_docs > 100000:
            health_status = "warning"
            health_message = f"Collection has {total_docs:,} documents — monitor for unexpected growth"
        else:
            health_status = "healthy"
            health_message = "TTL cleanup is operating normally"

        daily_size_pipeline = [
            {"$match": {"timestamp": {"$gte": now - timedelta(days=30), "$type": "date"}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]
        try:
            daily_ingest = await db.bot_spoof_attempts.aggregate(daily_size_pipeline).to_list(30)
        except Exception:
            daily_ingest = []

        string_ts_count = 0
        try:
            string_ts_count = await db.bot_spoof_attempts.count_documents({"timestamp": {"$type": "string"}})
        except Exception:
            pass

        return {
            "total_documents": total_docs,
            "ttl_index": index_info,
            "age_distribution": age_buckets,
            "health_status": health_status,
            "health_message": health_message,
            "daily_ingest": [{"date": d["_id"], "count": d["count"]} for d in daily_ingest],
            "string_timestamps_remaining": string_ts_count,
            "checked_at": now.isoformat(),
        }
    except Exception as exc:
        logger.error(f"TTL monitor error: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch TTL monitoring data")


async def _record_collection_size_snapshot():
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        total = await db.bot_spoof_attempts.count_documents({})
        await db.collection_size_history.update_one(
            {"date": today_str, "collection": "bot_spoof_attempts"},
            {"$set": {
                "size": total,
                "recorded_at": now,
            }},
            upsert=True,
        )
        logger.info(f"Collection size snapshot recorded: bot_spoof_attempts={total} on {today_str}")
    except Exception as exc:
        logger.warning(f"Collection size snapshot failed: {exc}")


async def _collection_size_snapshot_loop():
    await asyncio.sleep(120)
    while True:
        try:
            await _record_collection_size_snapshot()
        except Exception as exc:
            logger.warning(f"Collection size snapshot loop error: {exc}")
        await asyncio.sleep(24 * 3600)


@router.get("/admin/security/collection-size-history")
async def admin_collection_size_history(
    days: int = Query(90, ge=7, le=365),
    admin: dict = Depends(get_admin_user),
):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        docs = await db.collection_size_history.find(
            {"collection": "bot_spoof_attempts", "date": {"$gte": cutoff}},
            {"_id": 0, "date": 1, "size": 1},
        ).sort("date", 1).to_list(365)

        growth_rate = None
        if len(docs) >= 2:
            first = docs[0]
            last = docs[-1]
            try:
                d0 = datetime.strptime(first["date"], "%Y-%m-%d")
                d1 = datetime.strptime(last["date"], "%Y-%m-%d")
                span_days = max((d1 - d0).days, 1)
            except (ValueError, KeyError):
                span_days = max(len(docs) - 1, 1)
            growth_rate = round((last["size"] - first["size"]) / span_days, 1)

        return {
            "history": docs,
            "days": days,
            "growth_rate_per_day": growth_rate,
        }
    except Exception as exc:
        logger.error(f"Collection size history error: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch collection size history")


@router.get("/admin/security/blocked-ips")
async def admin_list_blocked_ips(
    admin: dict = Depends(get_admin_user),
):
    try:
        blocked = await db.blocked_ips.find(
            {}, {"_id": 0}
        ).sort("blocked_at", -1).to_list(500)
    except Exception:
        blocked = []

    duration_breakdown = {"1h": 0, "6h": 0, "24h": 0, "7d": 0, "30d": 0, "permanent": 0}
    now = datetime.now(timezone.utc)
    for b in blocked:
        expires_at = b.get("expires_at")
        blocked_at = b.get("blocked_at")
        if expires_at and blocked_at:
            if expires_at <= now:
                continue
            diff_hours = (expires_at - blocked_at).total_seconds() / 3600
            if diff_hours <= 1.5:
                duration_breakdown["1h"] += 1
            elif diff_hours <= 9:
                duration_breakdown["6h"] += 1
            elif diff_hours <= 36:
                duration_breakdown["24h"] += 1
            elif diff_hours <= 336:
                duration_breakdown["7d"] += 1
            else:
                duration_breakdown["30d"] += 1
        elif not expires_at:
            duration_breakdown["permanent"] += 1

    return {"blocked_ips": blocked, "duration_breakdown": duration_breakdown}


@router.get("/admin/security/block-trends")
async def admin_block_trends(
    days: int = Query(30, ge=7, le=90),
    admin: dict = Depends(get_admin_user),
):
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    try:
        blocks = await db.blocked_ips.find(
            {"blocked_at": {"$gte": start}},
            {"_id": 0, "blocked_at": 1, "expires_at": 1},
        ).to_list(5000)
    except Exception:
        blocks = []

    date_map = {}
    for d in range(days):
        dt = (start + timedelta(days=d)).strftime("%Y-%m-%d")
        date_map[dt] = {"date": dt, "1h": 0, "6h": 0, "24h": 0, "7d": 0, "30d": 0, "permanent": 0}

    for b in blocks:
        ba = b.get("blocked_at")
        if not ba:
            continue
        day_key = ba.strftime("%Y-%m-%d")
        if day_key not in date_map:
            continue
        ea = b.get("expires_at")
        if ea and ba:
            diff_hours = (ea - ba).total_seconds() / 3600
            if diff_hours <= 1.5:
                date_map[day_key]["1h"] += 1
            elif diff_hours <= 9:
                date_map[day_key]["6h"] += 1
            elif diff_hours <= 36:
                date_map[day_key]["24h"] += 1
            elif diff_hours <= 336:
                date_map[day_key]["7d"] += 1
            else:
                date_map[day_key]["30d"] += 1
        else:
            date_map[day_key]["permanent"] += 1

    series = sorted(date_map.values(), key=lambda x: x["date"])
    return {"series": series}


@router.post("/admin/security/block-ip")
async def admin_block_ip(
    ip_hash: str = Body(..., embed=True),
    reason: str = Body("repeat_spoof_offender", embed=True),
    expires_in: float | None = Body(None, embed=True),
    admin: dict = Depends(get_admin_user),
):
    if not ip_hash or len(ip_hash) < 4:
        raise HTTPException(400, "Invalid ip_hash")
    existing = await db.blocked_ips.find_one({"ip_hash": ip_hash})
    if existing:
        ea = existing.get("expires_at")
        if ea and ea <= datetime.now(timezone.utc):
            await db.blocked_ips.delete_one({"ip_hash": ip_hash})
        else:
            raise HTTPException(409, "IP already blocked")
    now = datetime.now(timezone.utc)
    doc = {
        "ip_hash": ip_hash,
        "reason": reason,
        "blocked_at": now,
        "blocked_by": admin.get("email", "admin"),
    }
    if expires_in is not None and expires_in > 0:
        doc["expires_at"] = now + timedelta(hours=float(expires_in))
    await db.blocked_ips.insert_one(doc)
    from middleware import _refresh_blocked_ip_cache
    await _refresh_blocked_ip_cache()
    logger.info(f"BLOCK_IP ip_hash={ip_hash} expires_in={expires_in}h by={admin.get('email')}")
    return {"ok": True, "ip_hash": ip_hash}


INDEXNOW_KEY = os.environ.get("INDEXNOW_KEY", hashlib.sha256(b"syrabit-indexnow-2026").hexdigest()[:32]).strip()
INDEXNOW_HOST = "https://syrabit.ai"

async def _indexnow_submit(urls: List[str]) -> dict:
    if not urls:
        return {"ok": False, "error": "No URLs provided"}
    payload = {
        "host": "syrabit.ai",
        "key": INDEXNOW_KEY,
        "keyLocation": f"{INDEXNOW_HOST}/{INDEXNOW_KEY}.txt",
        "urlList": urls[:10000],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.indexnow.org/indexnow", json=payload)
            ok = resp.status_code in (200, 202)
            if ok:
                logger.info(f"IndexNow submitted {len(urls)} URLs — status {resp.status_code}")
            else:
                logger.warning(f"IndexNow failed — status {resp.status_code}: {resp.text[:200]}")
            return {"ok": ok, "status": resp.status_code, "count": len(urls)}
    except Exception as e:
        logger.warning(f"IndexNow error: {e}")
        return {"ok": False, "error": str(e)}

def _indexnow_notify_background(urls: List[str]):
    import asyncio
    async def _fire():
        try:
            await _indexnow_submit(urls)
        except Exception as e:
            logger.debug(f"IndexNow background notify failed: {e}")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_fire())
    except RuntimeError:
        pass


@router.post("/admin/indexnow/ping")
async def admin_indexnow_ping(
    body: dict = Body({}),
    admin: dict = Depends(get_admin_user),
):
    urls = body.get("urls", [])
    if not urls:
        try:
            published = await db.seo_pages.find(
                {"status": "published"},
                {"_id": 0, "board_slug": 1, "class_slug": 1, "subject_slug": 1, "topic_slug": 1, "page_type": 1, "updated_at": 1},
            ).sort("updated_at", -1).to_list(500)
            for p in published:
                path = f"/{p['board_slug']}/{p['class_slug']}/{p['subject_slug']}/{p['topic_slug']}"
                if p.get("page_type") and p["page_type"] != "notes":
                    path += f"/{p['page_type']}"
                urls.append(f"{INDEXNOW_HOST}{path}")
        except Exception as e:
            raise HTTPException(500, f"Failed to fetch published pages: {e}")
    result = await _indexnow_submit(urls)
    return result


@router.get("/admin/indexnow/status")
async def admin_indexnow_status(admin: dict = Depends(get_admin_user)):
    return {"key": INDEXNOW_KEY, "host": INDEXNOW_HOST, "configured": True}


@router.post("/admin/security/unblock-ip")
async def admin_unblock_ip(
    ip_hash: str = Body(..., embed=True),
    admin: dict = Depends(get_admin_user),
):
    if not ip_hash or len(ip_hash) < 4:
        raise HTTPException(400, "Invalid ip_hash")
    result = await db.blocked_ips.delete_one({"ip_hash": ip_hash})
    if result.deleted_count == 0:
        raise HTTPException(404, "IP not found in block list")
    from middleware import _refresh_blocked_ip_cache
    await _refresh_blocked_ip_cache()
    logger.info(f"UNBLOCK_IP ip_hash={ip_hash} by={admin.get('email')}")
    return {"ok": True, "ip_hash": ip_hash}


async def _perform_cache_warm(top_n: int = 20, *, source: str = "manual") -> dict:
    """Core cache-warming routine, callable from both the admin endpoint and
    the background scheduler (Task #282 T004).

    1. Aggregates the top-N most frequent user queries from db.conversations.
    2. Skips queries that already have an AI cache entry.
    3. Generates answers for the rest in batches and writes them to Redis.

    Returns the same shape used by the admin endpoint so the response is
    interchangeable.
    """
    if top_n < 1 or top_n > 500:
        raise ValueError("top_n must be 1-500")

    try:
        pipeline = [
            {"$unwind": "$messages"},
            {"$match": {"messages.role": "user"}},
            {"$group": {
                "_id": {"query": "$messages.content", "subject_id": "$metadata.subject_id", "board_id": "$metadata.board_id"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"count": -1}},
            {"$limit": top_n},
        ]
        cursor = db.conversations.aggregate(pipeline)
        top_queries = await cursor.to_list(length=top_n)
    except Exception as e:
        logger.warning(f"[CACHE_WARM] Failed to fetch top queries: {e}")
        top_queries = []

    if not top_queries:
        return {"ok": True, "warmed": 0, "already_cached": 0, "failed": 0,
                "total_queries": 0, "source": source,
                "message": "No queries found to warm"}

    from cache import _cache_key, _redis_get_ai_cache, _redis_set
    from config import REDIS_AI_CACHE_TTL

    already_cached = 0
    to_warm = []
    for q in top_queries:
        _qid = q.get("_id", {})
        query_text = (_qid.get("query") or "").strip() if isinstance(_qid, dict) else str(_qid).strip()
        subject_id = (_qid.get("subject_id") or "") if isinstance(_qid, dict) else ""
        board_id = (_qid.get("board_id") or "") if isinstance(_qid, dict) else ""
        if not query_text or len(query_text) < 3:
            continue
        ck = _cache_key(query_text, subject_id=subject_id, board_id=board_id)
        existing = _redis_get_ai_cache(ck)
        if existing:
            already_cached += 1
        else:
            to_warm.append({"query": query_text, "subject_id": subject_id, "board_id": board_id})

    warmed = 0
    failed = 0

    async def _warm_single(item: dict):
        nonlocal warmed, failed
        query_text = item["query"]
        try:
            from prompts import build_system_prompt
            system_prompt = build_system_prompt({}, query=query_text)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query_text},
            ]
            answer = await asyncio.wait_for(
                _call_llm_raw(messages, max_tokens=1024),
                timeout=15.0,
            )
            if answer and len(str(answer)) > 10:
                ck = _cache_key(query_text, subject_id=item.get("subject_id", ""), board_id=item.get("board_id", ""))
                _redis_set("ai_cache", ck, str(answer), REDIS_AI_CACHE_TTL)
                warmed += 1
                logger.info(f"[CACHE_WARM] Warmed: '{query_text[:50]}' ({len(str(answer))} chars)")
            else:
                failed += 1
        except Exception as e:
            failed += 1
            logger.warning(f"[CACHE_WARM] Failed for '{query_text[:50]}': {e}")

    batch_size = 3
    for i in range(0, len(to_warm), batch_size):
        batch = to_warm[i:i + batch_size]
        await asyncio.gather(*[_warm_single(q) for q in batch])

    logger.info(f"[CACHE_WARM] Done ({source}): warmed={warmed}, already_cached={already_cached}, failed={failed}")
    result = {
        "ok": True,
        "warmed": warmed,
        "already_cached": already_cached,
        "failed": failed,
        "total_queries": len(top_queries),
        "source": source,
    }
    try:
        _chat_speedup.record_warm_run(result)
    except Exception:
        pass
    return result


@router.post("/admin/cache/warm")
async def admin_cache_warm(
    top_n: int = Body(default=20, embed=True),
    admin: dict = Depends(get_admin_user),
):
    try:
        result = await _perform_cache_warm(top_n, source=f"manual:{admin.get('email','')}")
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    return result


# ── AI response cache: admin stats + purge ───────────────────────────────────
# AdminHealth.jsx polls /admin/ai/cache/stats every 30s and exposes a
# manual "Purge" button that calls /admin/ai/cache/purge?pattern=*. Both
# delegate straight to the ai_cache module, which already returns a
# well-shaped dict (see ai_cache.stats() and ai_cache.purge_all()).

@router.get("/admin/ai/cache/stats")
async def admin_ai_cache_stats(admin: dict = Depends(get_admin_user)):
    """Return managed (Redis) cache stats + L1 in-memory size for the
    AdminHealth panel. The FE reads `aiCacheStats.managed.*` (hit_rate,
    backend, breaker_open, namespace, ttl_seconds, ...) and
    `aiCacheStats.l1.{size,maxsize}` so we wrap both into one payload."""
    try:
        import ai_cache as _ai_cache_mod
        managed = _ai_cache_mod.stats()
    except Exception as e:
        logger.warning(f"admin_ai_cache_stats (managed) failed: {e}")
        managed = {"error": str(e)[:200]}
    try:
        from cache import _ai_response_cache
        l1 = {
            "size": len(_ai_response_cache),
            "maxsize": getattr(_ai_response_cache, "maxsize", None),
            "ttl": getattr(_ai_response_cache, "ttl", None),
        }
    except Exception as e:
        logger.warning(f"admin_ai_cache_stats (l1) failed: {e}")
        l1 = {"size": 0, "maxsize": None, "error": str(e)[:200]}
    return {"managed": managed, "l1": l1}


@router.post("/admin/ai/cache/purge")
async def admin_ai_cache_purge(
    pattern: str = Query("*", description="Redis SCAN pattern appended to the AI cache namespace; default '*' = all entries"),
    admin: dict = Depends(get_admin_user),
):
    try:
        import ai_cache as _ai_cache_mod
        result = await _ai_cache_mod.purge_all(pattern=pattern)
        return result
    except Exception as e:
        logger.warning(f"admin_ai_cache_purge failed: {e}")
        return {"ok": False, "error": str(e)[:200], "deleted": 0, "l1_cleared": 0}


# ───────────────────────────────────────────────────────────────────────────
# Task #706 — /admin/diagnostics + Cloudflare Access break-glass paging
# ───────────────────────────────────────────────────────────────────────────
# The runbook (docs/CLOUDFLARE_ZERO_TRUST.md §0 + §7) instructs operators to
# poll this endpoint to confirm Access enforcement is on after rollout, and
# to confirm break-glass is *off* once an incident is resolved. The endpoint
# is admin-gated (so the JSON cannot be casually scraped) but does NOT itself
# require a CF Access JWT — by design, since its purpose is to surface the
# state when Access is degraded. The admin-JWT-only path is reachable when
# break-glass is active because ``require_cf_access_admin`` short-circuits
# in that mode.
#
# Paging: when ``admin_enforced`` is False on a production-like environment
# (i.e. CF_ACCESS_ENFORCE was provisioned at any point) we dispatch an
# alert through the existing ``metrics._dispatch_alert`` pipeline. Cooldown
# is handled inside the dispatcher (1h default) so polling the diagnostics
# endpoint cannot spam the alert channel.

_CF_ACCESS_DEGRADED_ALERT_TYPE = "cf_access_admin_degraded"
_CF_ACCESS_BREAK_GLASS_ALERT_TYPE = "cf_access_break_glass_active"


def _cf_access_provisioned() -> bool:
    """True once an operator has *ever* turned on Access in this env.

    We use the presence of ``CF_ACCESS_TEAM_DOMAIN`` (or any AUD) as the
    "production-like" signal so dev environments that have never set
    these vars do not page on every diagnostics call. Once enforcement
    has been provisioned, the alert fires whenever ``admin_enforced``
    flips to False — which is exactly the lockout-prevention signal
    the on-call needs.
    """
    return bool(
        os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip()
        or os.environ.get("CF_ACCESS_AUD_ADMIN", "").strip()
    )


async def _maybe_page_cf_access_state(snapshot: dict) -> dict:
    """Fire paging alerts on degraded Access state. Returns alert metadata."""
    fired: list[str] = []
    if not _cf_access_provisioned():
        return {"alerts_fired": fired, "skipped_reason": "cf_access_not_provisioned"}
    try:
        from metrics import _dispatch_alert  # local import: heavy module
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[CF_ACCESS_DIAG] metrics import failed: {exc}")
        return {"alerts_fired": fired, "skipped_reason": f"metrics_unavailable:{exc}"}

    if snapshot.get("break_glass_active"):
        try:
            await _dispatch_alert(
                _CF_ACCESS_BREAK_GLASS_ALERT_TYPE,
                "Cloudflare Access break-glass is ACTIVE",
                (
                    "Admin Access enforcement is currently BYPASSED via the "
                    f"break-glass {snapshot.get('break_glass_source') or 'unknown'} "
                    "path. Disable it as soon as the underlying Cloudflare Zero "
                    "Trust outage is resolved. See docs/CLOUDFLARE_ZERO_TRUST.md §7."
                ),
                threshold_snapshot={
                    "metric": "cf_access.break_glass_active",
                    "value": "false",
                    "actual": "true",
                },
            )
            fired.append(_CF_ACCESS_BREAK_GLASS_ALERT_TYPE)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[CF_ACCESS_DIAG] break-glass alert dispatch failed: {exc}")
    elif not snapshot.get("admin_enforced"):
        try:
            await _dispatch_alert(
                _CF_ACCESS_DEGRADED_ALERT_TYPE,
                "Cloudflare Access admin enforcement is OFF",
                (
                    "/admin/diagnostics reports admin_enforced=false in a "
                    "production-provisioned environment. Either CF_ACCESS_ENFORCE "
                    "is unset, the AUD tag was rotated and not updated, or the "
                    "service was not restarted after env changes. Restore "
                    "enforcement before the next admin login. See docs/"
                    "CLOUDFLARE_ZERO_TRUST.md §0 + §7."
                ),
                threshold_snapshot={
                    "metric": "cf_access.admin_enforced",
                    "value": "true",
                    "actual": "false",
                },
            )
            fired.append(_CF_ACCESS_DEGRADED_ALERT_TYPE)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[CF_ACCESS_DIAG] degraded alert dispatch failed: {exc}")
    return {"alerts_fired": fired}


@router.post("/admin/break-glass/disable")
async def admin_break_glass_disable(
    request: Request,
    admin: dict = Depends(get_admin_user),
):
    """One-click disable for Cloudflare Access break-glass mode (Task #710).

    Persists a Redis-backed "force-disabled" flag visible to all gunicorn
    workers (so the disable does not race with the other workers still
    seeing the original env vars), pops the env vars in the current
    process for instant local effect, and audit-logs the action at
    WARNING with the admin's email.

    The endpoint is admin-gated. Because ``require_cf_access_admin``
    (folded into ``get_admin_user``) short-circuits while break-glass is
    active, an admin can ALWAYS reach this endpoint to disable an active
    bypass — that's the whole point of the affordance.

    After the disable is recorded the operator must still:
      1. Remove ``CF_ACCESS_BREAK_GLASS`` from Railway env (so a worker
         restart does not re-arm it).
      2. Rotate / clear the Cloudflare Worker secret that injected the
         ``X-Cf-Access-Break-Glass`` header (so traffic-side activation
         is also revoked).
      See docs/CLOUDFLARE_ZERO_TRUST.md §7.1 for the full checklist.
    """
    import cf_access  # local import: avoids heavy import at module load
    actor = (
        (admin or {}).get("cf_access_email")
        or (admin or {}).get("email")
        or (admin or {}).get("sub")
        or "unknown_admin"
    )
    record = cf_access.force_disable_break_glass(actor=actor)
    logger.warning(
        "[ADMIN_AUDIT] break-glass DISABLED actor=%r at=%s persisted=%s cleared=%s",
        actor,
        record.get("disabled_at"),
        record.get("redis_persisted"),
        record.get("env_cleared"),
    )
    snapshot = cf_access.status(request)
    return {
        "ok": True,
        "disabled_at": record.get("disabled_at"),
        "actor": actor,
        "redis_persisted": record.get("redis_persisted"),
        "env_cleared": record.get("env_cleared"),
        "cf_access": snapshot,
    }


@router.get("/admin/diagnostics")
async def admin_diagnostics(
    request: Request,
    admin: dict = Depends(get_admin_user),
):
    """Operator diagnostics for admin auth posture (Task #637 + Task #706).

    Returns the live Cloudflare Access enforcement state plus break-glass
    surface. Polling this endpoint also drives the paging rule: when
    ``admin_enforced`` flips to False (or break-glass is active) on a
    production-provisioned environment, an alert fires through the same
    pipeline that handles SEO / hydrate alerts.
    """
    import cf_access  # local import: avoids heavy import at module load
    snapshot = cf_access.status(request)
    page_meta = await _maybe_page_cf_access_state(snapshot)
    return {
        "cf_access": snapshot,
        "paging": page_meta,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checked_by": (admin or {}).get("cf_access_email")
        or (admin or {}).get("email")
        or (admin or {}).get("sub"),
    }


# ── Background auto-warm loop (Task #282 T004) ────────────────────────────────
# Periodically re-runs the cache warmer so the top common questions always
# have a near-instant answer waiting in Redis. The loop is registered from
# server.py lifespan alongside the other long-running background loops.
#
# Cadence: warm every 6h with the top 30 queries. We sleep 15 minutes after
# startup so warming doesn't compete with the boot rush; the warming itself
# runs entirely off the request path.
_CACHE_WARM_LOOP_INTERVAL_S = 6 * 3600
_CACHE_WARM_LOOP_TOP_N = 30
_CACHE_WARM_LOOP_STARTUP_DELAY_S = 15 * 60

# Task #950 — Mongo-backed lease so only ONE replica fires the LLM
# cache pre-warm per cycle. Previously gated by ``_is_leader`` in
# server.py, which is per-machine and double-fires on multi-replica
# Railway deployments — the warmer issues real LLM completions, so
# every duplicate run multiplies the LLM budget by N.
#
# TTL is 3× the loop interval (~18h) so a single missed renewal
# doesn't trigger a needless leader fail-over. Followers poll every
# 15 minutes — enough to take over within one cycle if the leader
# replica dies, without hammering Mongo in the steady state.
_CACHE_WARM_LOCK_ID = "cache_warm_lease"
_CACHE_WARM_LEASE_TTL_S = _CACHE_WARM_LOOP_INTERVAL_S * 3
_CACHE_WARM_FOLLOWER_INTERVAL_S = 15 * 60
import background_lease as _bglease
_CACHE_WARM_OWNER_ID = _bglease.make_owner_id("cache-warm")


async def _cache_warm_loop():
    """Auto-warm the AI response cache every ``_CACHE_WARM_LOOP_INTERVAL_S``
    seconds. Failures are swallowed and logged so a single bad iteration
    never tears down the loop.

    Cross-replica dedup (Task #950): every replica may run this loop,
    but only the one holding the Mongo-backed lease actually fires
    ``_perform_cache_warm`` (which costs real LLM tokens). Followers
    stand down on each tick, so the LLM-budget cost stays at 1×
    regardless of replica count, and a leader fail-over is picked up
    within one follower interval (~15 min) instead of waiting hours.
    """
    from deps import db, is_mongo_available
    await asyncio.sleep(_CACHE_WARM_LOOP_STARTUP_DELAY_S)
    try:
        while True:
            try:
                if not await is_mongo_available():
                    logger.debug(
                        "[CACHE_WARM] Skipping auto warm — Mongo unavailable")
                    await asyncio.sleep(_CACHE_WARM_FOLLOWER_INTERVAL_S)
                    continue
                have_lease = await _bglease.try_acquire_lease(
                    db, _CACHE_WARM_LOCK_ID, _CACHE_WARM_OWNER_ID,
                    _CACHE_WARM_LEASE_TTL_S,
                )
                if not have_lease:
                    # A peer replica owns the lease and it's still
                    # fresh; stand down and re-check at the follower
                    # cadence. This branch does NOT touch the LLM
                    # budget.
                    await asyncio.sleep(_CACHE_WARM_FOLLOWER_INTERVAL_S)
                    continue
                await _perform_cache_warm(
                    _CACHE_WARM_LOOP_TOP_N, source="auto_loop")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"[CACHE_WARM] auto loop iteration error: {exc}")
            await asyncio.sleep(_CACHE_WARM_LOOP_INTERVAL_S)
    finally:
        try:
            await asyncio.shield(_bglease.release_lease(
                db, _CACHE_WARM_LOCK_ID, _CACHE_WARM_OWNER_ID,
            ))
        except Exception:
            pass


