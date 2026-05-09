"""Syrabit.ai — Utility functions: bot detection, device type, country, keywords, etc."""
import re, time as _time_mod, logging, uuid
import dns.resolver
import dns.reversename
from typing import Optional
from threading import Lock
from datetime import datetime, timezone, timedelta
import httpx
from config import SLOW_QUERY_THRESHOLD_MS
from deps import db, is_mongo_available

logger = logging.getLogger(__name__)

__all__ = [
    "_BOT_PATTERNS", "_SEARCH_BOT_UA_RE", "_ABUSIVE_SCRAPER_UA_RE",
    "_TRAINING_SCRAPER_UA_RE",
    "_SlowQueryTimer", "_do_rdns_verify",
    "_extract_keywords", "_get_device_type",
    "_ip_country_cache", "_is_bot", "_resolve_country", "_slow_query",
    "get_library_analytics", "track_library_event",
    "slugify_title", "verify_bot_ip",
]


def slugify_title(title: str) -> str:
    if not title:
        return ""
    import unicodedata
    normalized = unicodedata.normalize("NFKC", title.strip())
    slug = re.sub(r'[^\w\u0300-\u036f\u0980-\u09ff\u0900-\u097f]+', '-', normalized.lower(), flags=re.UNICODE).strip('-')
    slug = re.sub(r'-{2,}', '-', slug)
    if not slug or slug == '-':
        slug = re.sub(r'[^a-z0-9]+', '-', normalized.lower()).strip('-')
    return slug

try:
    from user_agents import parse as _parse_ua
except ImportError:
    _parse_ua = None

_SLOW_QUERY_LABEL_OVERRIDES = {
    "library_bundle": 2000.0,
}

class _SlowQueryTimer:
    __slots__ = ("_label", "_t0", "_threshold")
    def __init__(self, label: str, threshold_ms: Optional[float] = None):
        self._label = label
        self._t0 = 0.0
        self._threshold = (
            threshold_ms
            if threshold_ms is not None
            else _SLOW_QUERY_LABEL_OVERRIDES.get(label, SLOW_QUERY_THRESHOLD_MS)
        )
    async def __aenter__(self):
        self._t0 = _time_mod.time()
        return self
    async def __aexit__(self, *exc):
        elapsed_ms = (_time_mod.time() - self._t0) * 1000
        if elapsed_ms > self._threshold:
            logger.warning(f"SLOW_QUERY {self._label} took {elapsed_ms:.0f}ms (threshold={self._threshold}ms)")

def _slow_query(label: str, threshold_ms: Optional[float] = None) -> _SlowQueryTimer:
    return _SlowQueryTimer(label, threshold_ms=threshold_ms)

def _extract_keywords(query: str) -> list:
    """Extract meaningful search keywords, removing stop-words."""
    stop_words = {
        "what", "which", "when", "where", "that", "this", "with", "from",
        "have", "will", "your", "some", "they", "been", "more", "also",
        "into", "than", "then", "there", "about", "give", "explain", "the",
        "and", "for", "are", "how", "why", "who", "can", "its", "was",
        "let", "define", "describe", "state", "write", "list",
    }
    raw = [w.strip('?.,!;:()[]"\'').lower() for w in query.split()]
    return [w for w in raw if len(w) >= 3 and w not in stop_words][:8]


# ─────────────────────────────────────────────
# LIBRARY ANALYTICS TRACKING
# ─────────────────────────────────────────────

async def track_library_event(
    event_type: str,
    subject_id: str = None,
    chapter_id: str = None,
    user_id: str = None,
    search_query: str = None,
    metadata: dict = None
):
    """
    Track library user interactions for analytics.
    
    Event types:
    - 'search': User searched in library
    - 'subject_view': User opened a subject
    - 'chapter_view': User viewed a chapter
    - 'ask_ai_click': User clicked Ask AI button
    - 'document_open': User opened document viewer
    """
    try:
        event = {
            "id": str(uuid.uuid4()),
            "event_type": event_type,
            "subject_id": subject_id,
            "chapter_id": chapter_id,
            "user_id": user_id,
            "search_query": search_query,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await db.analytics.insert_one(event)
        logger.debug(f"📊 Analytics tracked: {event_type} | subject: {subject_id}")
    except Exception as e:
        logger.error(f"Analytics tracking failed: {e}")


async def get_library_analytics(days: int = 30):
    """Get library analytics summary"""
    if not await is_mongo_available():
        return {"period_days": days, "top_searches": [], "most_viewed_subjects": [], "most_ask_ai_subjects": [], "document_opens": 0, "events_by_type": {}}
    try:
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        start_iso = start_date.isoformat()
        
        search_pipeline = [
            {"$match": {"event_type": "search", "timestamp": {"$gte": start_iso}}},
            {"$group": {"_id": "$search_query", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        top_searches = await db.analytics.aggregate(search_pipeline).to_list(10)
        
        subject_view_pipeline = [
            {"$match": {"event_type": "subject_view", "timestamp": {"$gte": start_iso}, "subject_id": {"$ne": None}}},
            {"$group": {"_id": "$subject_id", "view_count": {"$sum": 1}}},
            {"$sort": {"view_count": -1}},
            {"$limit": 10}
        ]
        top_subjects_raw = await db.analytics.aggregate(subject_view_pipeline).to_list(10)
        
        if top_subjects_raw:
            subject_ids = [item["_id"] for item in top_subjects_raw]
            subjects = await db.subjects.find({"id": {"$in": subject_ids}}, {"_id": 0, "id": 1, "name": 1, "description": 1}).to_list(20)
            subject_map = {s["id"]: s for s in subjects}
            top_subjects = []
            for item in top_subjects_raw:
                subj = subject_map.get(item["_id"])
                if subj:
                    top_subjects.append({"subject_id": item["_id"], "name": subj["name"], "view_count": item["view_count"]})
        else:
            top_subjects = []
        
        ask_ai_pipeline = [
            {"$match": {"event_type": "ask_ai_click", "timestamp": {"$gte": start_iso}, "subject_id": {"$ne": None}}},
            {"$group": {"_id": "$subject_id", "ask_count": {"$sum": 1}}},
            {"$sort": {"ask_count": -1}},
            {"$limit": 10}
        ]
        top_ask_ai_raw = await db.analytics.aggregate(ask_ai_pipeline).to_list(10)
        
        if top_ask_ai_raw:
            ask_subject_ids = [item["_id"] for item in top_ask_ai_raw]
            ask_subjects = await db.subjects.find({"id": {"$in": ask_subject_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(20)
            ask_subject_map = {s["id"]: s["name"] for s in ask_subjects}
            top_ask_ai = []
            for item in top_ask_ai_raw:
                name = ask_subject_map.get(item["_id"], "Unknown")
                top_ask_ai.append({"subject_id": item["_id"], "name": name, "ask_count": item["ask_count"]})
        else:
            top_ask_ai = []
        
        doc_open_count = await db.analytics.count_documents({"event_type": "document_open", "timestamp": {"$gte": start_iso}})
        
        event_type_pipeline = [
            {"$match": {"timestamp": {"$gte": start_iso}}},
            {"$group": {"_id": "$event_type", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        events_by_type = await db.analytics.aggregate(event_type_pipeline).to_list(20)
        
        return {
            "period_days": days,
            "top_searches": [{"query": item["_id"], "count": item["count"]} for item in top_searches if item["_id"]],
            "most_viewed_subjects": top_subjects,
            "most_ask_ai_subjects": top_ask_ai,
            "document_opens": doc_open_count,
            "events_by_type": {item["_id"]: item["count"] for item in events_by_type},
        }
    except Exception:
        return {"period_days": days, "top_searches": [], "most_viewed_subjects": [], "most_ask_ai_subjects": [], "document_opens": 0, "events_by_type": {}}


# ── Bot/crawler User-Agent patterns (canonical source) ────────────────────────
# Task #9 — canonical bot registry lives at `infra/bot-rules.yaml`.
# CI guard `scripts/check_bot_rules_drift.py` enforces that every token
# from the verified_search + citation_ai + training_ai buckets appears
# in the regex below, and every abusive-bucket token appears in
# `_ABUSIVE_SCRAPER_UA_RE`. Adding a bot to the YAML without updating
# this regex (or vice versa) fails the drift gate.
_SEARCH_BOT_UA_RE = re.compile(
    r"googlebot|google-extended|googleother|google-inspectiontool|"
    r"bingbot|yandexbot|yandex|duckduckbot|slurp|baiduspider|"
    r"facebookexternalhit|twitterbot|linkedinbot|telegrambot|whatsapp|"
    r"applebot|applebot-extended|ia_archiver|msnbot|"
    r"gptbot|oai-searchbot|chatgpt-user|claudebot|claude-web|perplexitybot|"
    r"perplexity-user|anthropic-ai|meta-externalagent|"
    r"youbot|"  # You.com search/answer engine — cites sources, drives referral traffic
    # Task #9 — long-tail verified search bots (Allow in robots.txt;
    # cf.verifiedBot or KV-cached rDNS is the trust gate at the edge)
    r"petalbot|yeti|mojeekbot|seznambot|"
    # Task #9 — training-AI crawlers (still recognised here so the
    # union counts them in analytics; the worker hard-403s the bucket
    # via AI_BOT_UA before they reach the origin)
    r"ccbot|cohere-ai|bytespider|amazonbot|diffbot|"
    r"rogerbot|embedly|quora link preview|showyoubot|"
    r"outbrain|pinterest/0\.|developers\.google\.com/\+/web/snippet|slackbot|"
    r"vkshare|w3c_validator|redditbot|googleweblight",
    re.IGNORECASE,
)

# `gptbot` lives in `_SEARCH_BOT_UA_RE` above (it's a discovery+training
# crawler that hits the same edge endpoints as Googlebot). The list below
# is for crawlers we treat as "training-only / low-SEO-value" — kept
# separate so future logic can choose to throttle one bucket without
# the other. The unified `_BOT_PATTERNS` re-unions both.
# NOTE: YouBot (You.com) was removed from this list — it is a search and
# answer engine that cites sources and drives referral traffic, so it is
# now classified as a search bot in `_SEARCH_BOT_UA_RE` above.
_TRAINING_SCRAPER_UA_RE = re.compile(
    r"ccbot|cohere-ai|bytespider|petalbot|"
    r"facebookbot|amazonbot|diffbot|img2dataset|omgili|"
    r"dotbot|mj12bot",
    re.IGNORECASE,
)

_ABUSIVE_SCRAPER_UA_RE = re.compile(
    r"scrapy|wget|curl|python-requests|go-http-client|java/|okhttp|"
    r"ahrefsbot|semrushbot|nmap|masscan|zgrab|heritrix|"
    # Task #9 — generic SEO/scraper bots; see infra/bot-rules.yaml
    r"dotbot|mj12bot",
    re.IGNORECASE,
)

_BOT_PATTERNS = re.compile(
    _SEARCH_BOT_UA_RE.pattern + r"|" + _TRAINING_SCRAPER_UA_RE.pattern + r"|" + _ABUSIVE_SCRAPER_UA_RE.pattern,
    re.IGNORECASE,
)

_RDNS_BOT_DOMAINS: dict[str, list[str]] = {
    "googlebot": [".googlebot.com", ".google.com"],
    "google-extended": [".googlebot.com", ".google.com"],
    "googleother": [".googlebot.com", ".google.com"],
    "google-inspectiontool": [".googlebot.com", ".google.com"],
    "bingbot": [".search.msn.com"],
    "msnbot": [".search.msn.com"],
    "yandexbot": [".yandex.ru", ".yandex.net", ".yandex.com"],
    "yandex": [".yandex.ru", ".yandex.net", ".yandex.com"],
    "applebot": [".applebot.apple.com"],
    "applebot-extended": [".applebot.apple.com"],
    "baiduspider": [".baidu.com", ".baidu.jp"],
    "duckduckbot": [".duckduckgo.com"],
    "slurp": [".crawl.yahoo.net"],
    # OpenAI publishes verified IP ranges at:
    #   https://openai.com/gptbot.json
    #   https://openai.com/searchbot.json
    #   https://openai.com/chatgpt-user.json
    # PTR records resolve to *.openai.com for all three crawlers.
    "gptbot": [".openai.com"],
    "oai-searchbot": [".openai.com"],
    "chatgpt-user": [".openai.com"],
    # Perplexity: https://perplexity.ai/perplexitybot.json
    "perplexitybot": [".perplexity.ai"],
    "perplexity-user": [".perplexity.ai"],
    # Anthropic: published in https://www.anthropic.com/.well-known/
    "claudebot": [".anthropic.com", ".claude.ai"],
    "claude-web": [".anthropic.com", ".claude.ai"],
    "anthropic-ai": [".anthropic.com", ".claude.ai"],
    # Meta crawler PTRs land in fbsv.net / facebook.com.
    "meta-externalagent": [".fbsv.net", ".facebook.com"],
    # ByteDance / TikTok crawler.
    "bytespider": [".bytedance.com", ".tiktokv.com"],
    # Amazon (Alexa / Q training) — PTRs end in compute.amazonaws.com,
    # but Amazon does not publish a stable suffix yet, so we leave
    # rDNS verification disabled to avoid false negatives.
}

_bot_verify_cache: dict[str, tuple[bool, float]] = {}
_bot_verify_lock = Lock()
_BOT_VERIFY_TTL = 3600


def _identify_bot_key(ua: str) -> str | None:
    ua_lower = ua.lower()
    for bot_key in _RDNS_BOT_DOMAINS:
        if bot_key in ua_lower:
            return bot_key
    return None


def _do_rdns_verify(ip: str, bot_key: str) -> bool:
    expected_domains = _RDNS_BOT_DOMAINS.get(bot_key)
    if not expected_domains:
        return False
    try:
        rev_name = dns.reversename.from_address(ip)
        answers = dns.resolver.resolve(rev_name, "PTR", lifetime=3)
        hostname = str(answers[0]).rstrip(".")
        if not any(hostname.lower().endswith(d) for d in expected_domains):
            return False
        fwd_answers = dns.resolver.resolve(hostname, "A", lifetime=3)
        fwd_ips = {str(rdata) for rdata in fwd_answers}
        if ip in fwd_ips:
            return True
        try:
            fwd6 = dns.resolver.resolve(hostname, "AAAA", lifetime=3)
            fwd_ips.update(str(rdata) for rdata in fwd6)
        except Exception:
            pass
        return ip in fwd_ips
    except Exception:
        return False


def verify_bot_ip(ip: str, ua: str) -> bool:
    if not ip or ip in ("127.0.0.1", "::1", "unknown"):
        return False
    bot_key = _identify_bot_key(ua)
    if not bot_key:
        return False
    cache_key = f"{ip}:{bot_key}"
    now = _time_mod.time()
    with _bot_verify_lock:
        cached = _bot_verify_cache.get(cache_key)
        if cached and (now - cached[1]) < _BOT_VERIFY_TTL:
            return cached[0]
    result = _do_rdns_verify(ip, bot_key)
    with _bot_verify_lock:
        _bot_verify_cache[cache_key] = (result, now)
        if len(_bot_verify_cache) > 10000:
            cutoff = now - _BOT_VERIFY_TTL
            expired = [k for k, (_, t) in _bot_verify_cache.items() if t < cutoff]
            for k in expired:
                _bot_verify_cache.pop(k, None)
    return result


def _is_bot(user_agent: str) -> bool:
    if not user_agent:
        return False
    return bool(_BOT_PATTERNS.search(user_agent))

def _get_device_type(user_agent: str) -> str:
    if not user_agent:
        return 'desktop'
    if _parse_ua:
        try:
            ua = _parse_ua(user_agent)
            if ua.is_mobile:
                return 'mobile'
            if ua.is_tablet:
                return 'tablet'
            return 'desktop'
        except Exception:
            pass
    ua_lower = user_agent.lower()
    if any(k in ua_lower for k in ('mobile', 'android', 'iphone', 'ipod', 'windows phone')):
        return 'mobile'
    if any(k in ua_lower for k in ('ipad', 'tablet')):
        return 'tablet'
    return 'desktop'

_ip_country_cache: dict = {}

async def _resolve_country(ip: str) -> str:
    """Resolve IP to country code using ip-api.com free tier."""
    if not ip or ip in ('127.0.0.1', '::1', 'unknown'):
        return ''
    if ip in _ip_country_cache:
        return _ip_country_cache[ip]
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f'http://ip-api.com/json/{ip}?fields=countryCode,status')
            data = r.json()
            if data.get('status') == 'success':
                country = data.get('countryCode', '')
                _ip_country_cache[ip] = country
                if len(_ip_country_cache) > 2000:
                    oldest = list(_ip_country_cache.keys())[:500]
                    for k in oldest:
                        _ip_country_cache.pop(k, None)
                return country
    except Exception:
        pass
