#!/usr/bin/env python3
"""
Syrabit Full-Stack Live Deployment Test
========================================
Hits the REAL production syrabit.ai deployment — no localhost, no mocks.
Bypasses CDN/Redis/app caches at every layer via:
  - Cache-Control: no-cache, no-store headers
  - Randomised ?_t=<epoch_ms> cache-bust query param on every request
  - CF zone cache purge attempt at start

Credentials read from environment (Replit secrets + shared env vars).

Usage:
  python3 scripts/live_deploy_test.py
  python3 scripts/live_deploy_test.py --section chat
  python3 scripts/live_deploy_test.py --section mongodb

Sections: all | frontend | api | mongodb | chat | vertex | cloudflare | github | sentry
"""

import argparse
import asyncio
import json
import os
import ssl
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

# ── colour helpers ────────────────────────────────────────────────────────────
R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"
M = "\033[95m"; C = "\033[96m"; W = "\033[97m"; BOLD = "\033[1m"; X = "\033[0m"

def ok(msg):  print(f"  {G}✓{X} {msg}")
def fail(msg): print(f"  {R}✗{X} {msg}")
def warn(msg): print(f"  {Y}⚠{X} {msg}")
def info(msg): print(f"  {B}·{X} {msg}")
def head(msg): print(f"\n{BOLD}{C}{'─'*60}{X}\n{BOLD}{C}  {msg}{X}\n{BOLD}{C}{'─'*60}{X}")

# ── environment ───────────────────────────────────────────────────────────────
PROD_API   = "https://api.syrabit.ai"
PROD_FRONT = "https://syrabit.ai"
PROD_CHAT  = f"{PROD_API}/api/v1/chat/stream"
BUST       = lambda: int(time.time() * 1000)   # cache-buster value

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma":        "no-cache",
    "Expires":       "0",
}

def env(key, default=None):
    return os.environ.get(key, default)

CF_ZONE_ID      = env("CF_ZONE_ID")
CF_ACCOUNT_ID   = env("CF_ACCOUNT_ID")
CF_KV_TOKEN     = env("CLOUDFLARE_KV_API_TOKEN")
CF_KV_NS        = env("CLOUDFLARE_KV_NAMESPACE_ID")
CF_API_TOKEN    = env("CF_API_TOKEN")            # broader token if set
GITHUB_TOKEN    = env("GITHUB_TOKEN")
MONGODB_URI     = env("MONGODB_URI")
SENTRY_DSN      = env("SENTRY_DSN")
GCP_CREDS_JSON  = env("GOOGLE_APPLICATION_CREDENTIALS_JSON")
VERTEX_PROJECT  = env("VERTEX_PROJECT_ID", "blissful-acumen-495019-t6")
VERTEX_LOCATION = env("VERTEX_LOCATION", "us-central1")
VERTEX_MODEL    = env("VERTEX_GEMINI_MODEL", "gemini-2.5-flash")
ADMIN_EMAIL     = env("ADMIN_EMAIL")
ADMIN_PASSWORD  = env("ADMIN_PASSWORD")

# Cached auth JWT — populated by _get_jwt() before chat section runs
_JWT_TOKEN: Optional[str] = None

async def _get_jwt() -> Optional[str]:
    """Try to obtain a JWT via admin login. Returns None if creds not set or login fails."""
    global _JWT_TOKEN
    if _JWT_TOKEN:
        return _JWT_TOKEN
    if not (ADMIN_EMAIL and ADMIN_PASSWORD):
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
            r = await c.post(
                f"{PROD_API}/api/v1/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                headers={"Content-Type": "application/json"},
            )
        if r.status_code == 200:
            data = r.json()
            token = data.get("access_token") or data.get("token")
            if token:
                _JWT_TOKEN = token
                return token
    except Exception:
        pass
    return None

# Summary accumulator
results: list[dict] = []

def record(section: str, name: str, status: str, ms: float, detail: str = ""):
    results.append({"section": section, "name": name,
                    "status": status, "ms": round(ms), "detail": detail})

# ── HTTP client (shared, no SSL verify issues) ────────────────────────────────
def make_client(timeout=20) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0),
        follow_redirects=True,
        headers=NO_CACHE_HEADERS,
    )

# ═════════════════════════════════════════════════════════════════════════════
# 1. CLOUDFLARE CACHE PURGE
# ═════════════════════════════════════════════════════════════════════════════
async def section_cloudflare_purge():
    head("1. Cloudflare Cache Purge")
    t0 = time.time()

    if not CF_ZONE_ID:
        warn("CF_ZONE_ID not set — skipping purge")
        return

    # Try the broader CF_API_TOKEN first, then KV token
    for token_name, token in [("CF_API_TOKEN", CF_API_TOKEN), ("CLOUDFLARE_KV_API_TOKEN", CF_KV_TOKEN)]:
        if not token:
            continue
        async with make_client(10) as c:
            resp = await c.post(
                f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/purge_cache",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"purge_everything": True},
            )
        data = resp.json()
        elapsed = (time.time() - t0) * 1000
        if data.get("success"):
            ok(f"Zone cache purged via {token_name} ({elapsed:.0f}ms)")
            record("cloudflare", "cache_purge", "ok", elapsed, "purge_everything=true")
            return
        else:
            errs = data.get("errors", [])
            warn(f"{token_name}: purge failed — {errs}")

    # Fallback: URL-based targeted purge of key assets
    urls_to_purge = [
        "https://syrabit.ai/",
        "https://syrabit.ai/chat",
        "https://api.syrabit.ai/health",
    ]
    token = CF_KV_TOKEN or CF_API_TOKEN
    if token:
        async with make_client(10) as c:
            resp = await c.post(
                f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/purge_cache",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"files": urls_to_purge},
            )
        data = resp.json()
        elapsed = (time.time() - t0) * 1000
        if data.get("success"):
            ok(f"Targeted URL purge succeeded ({elapsed:.0f}ms) — {len(urls_to_purge)} URLs")
            record("cloudflare", "cache_purge", "ok", elapsed, "targeted")
        else:
            warn(f"Targeted purge also failed: {data.get('errors')} — using request-level no-cache bypass")
            record("cloudflare", "cache_purge", "warn", elapsed, "token lacks purge scope; using no-cache headers")
    else:
        warn("No CF token available — using request-level no-cache bypass")
        record("cloudflare", "cache_purge", "warn", 0, "no token")

# ═════════════════════════════════════════════════════════════════════════════
# 2. FRONTEND HEALTH
# ═════════════════════════════════════════════════════════════════════════════
async def section_frontend():
    head("2. Frontend Health (syrabit.ai)")
    checks = [
        ("Homepage",      f"{PROD_FRONT}/?_t={BUST()}",          200),
        ("Chat page",     f"{PROD_FRONT}/chat?_t={BUST()}",       200),
        ("Learn page",    f"{PROD_FRONT}/learn?_t={BUST()}",      200),
        ("Robots.txt",    f"{PROD_FRONT}/robots.txt?_t={BUST()}", 200),
        ("Sitemap",       f"{PROD_FRONT}/sitemap.xml?_t={BUST()}",200),
        ("WWW redirect",  f"https://www.syrabit.ai/?_t={BUST()}", 200),
    ]
    async with make_client(15) as c:
        for name, url, expect in checks:
            t0 = time.time()
            try:
                resp = await c.get(url)
                ms = (time.time() - t0) * 1000
                cf_cache = resp.headers.get("cf-cache-status", "N/A")
                server   = resp.headers.get("server", "?")
                via_cf   = "cloudflare" in server.lower() or resp.headers.get("cf-ray")
                if resp.status_code == expect:
                    ok(f"{name}: {resp.status_code} ({ms:.0f}ms)  cf-cache={cf_cache}  via_cf={via_cf}")
                    record("frontend", name, "ok", ms, f"cf-cache={cf_cache}")
                else:
                    fail(f"{name}: expected {expect} got {resp.status_code} ({ms:.0f}ms)")
                    record("frontend", name, "fail", ms, f"status={resp.status_code}")
            except Exception as e:
                ms = (time.time() - t0) * 1000
                fail(f"{name}: {e} ({ms:.0f}ms)")
                record("frontend", name, "error", ms, str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 3. API ENDPOINT HEALTH
# ═════════════════════════════════════════════════════════════════════════════
async def section_api():
    head("3. Backend API Endpoints (api.syrabit.ai)")
    endpoints = [
        ("GET",  "/health",                         200, None),
        ("GET",  f"/health?_t={BUST()}",            200, None),
        ("GET",  f"/api/v1/health?_t={BUST()}",     200, None),
        ("GET",  f"/api/v1/content/library-bundle?slim=1&_t={BUST()}", 200, None),
        ("GET",  f"/api/v1/config/trustpilot?_t={BUST()}",             200, None),
        ("GET",  f"/api/v1/users/me?_t={BUST()}",                      401, None),  # expect 401 (unauthenticated)
        ("POST", f"/api/v1/auth/signup?_t={BUST()}",                    422, {"email":"","password":""}),  # expect 422 (bad payload)
        ("GET",  f"/sitemap-subjects.xml?_t={BUST()}",                 200, None),
    ]
    async with make_client(15) as c:
        for method, path, expect, body in endpoints:
            url = f"{PROD_API}{path}"
            t0 = time.time()
            try:
                if method == "GET":
                    resp = await c.get(url)
                else:
                    resp = await c.post(url, json=body)
                ms = (time.time() - t0) * 1000
                if resp.status_code == expect:
                    ok(f"{method} {path.split('?')[0]}: {resp.status_code} ({ms:.0f}ms)")
                    record("api", path.split("?")[0], "ok", ms, f"status={resp.status_code}")
                else:
                    fail(f"{method} {path.split('?')[0]}: expected {expect} got {resp.status_code} ({ms:.0f}ms)")
                    body_preview = resp.text[:120]
                    record("api", path.split("?")[0], "fail", ms,
                           f"expected={expect} got={resp.status_code}: {body_preview}")
            except Exception as e:
                ms = (time.time() - t0) * 1000
                fail(f"{method} {path.split('?')[0]}: {e} ({ms:.0f}ms)")
                record("api", path.split("?")[0], "error", ms, str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 4. MONGODB DIRECT CONNECTION
# ═════════════════════════════════════════════════════════════════════════════
async def section_mongodb():
    head("4. MongoDB Direct Connection")
    if not MONGODB_URI:
        fail("MONGODB_URI not set"); record("mongodb", "connection", "error", 0, "no URI"); return

    try:
        import motor.motor_asyncio as motor
    except ImportError:
        try:
            import pymongo
            t0 = time.time()
            client = pymongo.MongoClient(MONGODB_URI, serverSelectionTimeoutMS=8000,
                                         connectTimeoutMS=8000, socketTimeoutMS=8000)
            client.admin.command("ping")
            ms = (time.time() - t0) * 1000
            ok(f"MongoDB ping (pymongo): {ms:.0f}ms")
            record("mongodb", "ping", "ok", ms)

            # Check collection counts
            db_name = env("MONGODB_DB_NAME", "syrabit_prod")
            db = client[db_name]
            for coll in ["chapters", "users", "chats", "knowledge_objects"]:
                try:
                    count = db[coll].estimated_document_count()
                    ok(f"  {coll}: {count:,} documents")
                    record("mongodb", f"collection_{coll}", "ok", 0, f"count={count}")
                except Exception as e:
                    warn(f"  {coll}: {e}")
            client.close()
            return
        except Exception as e:
            fail(f"pymongo import/connect failed: {e}")
            record("mongodb", "connection", "error", 0, str(e))
            return

    # motor async path
    t0 = time.time()
    try:
        client = motor.AsyncIOMotorClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
        )
        await client.admin.command("ping")
        ms = (time.time() - t0) * 1000
        ok(f"MongoDB ping (motor): {ms:.0f}ms")
        record("mongodb", "ping", "ok", ms)

        db_name = env("MONGODB_DB_NAME", "syrabit_prod")
        db = client[db_name]
        for coll in ["chapters", "users", "chats", "knowledge_objects"]:
            try:
                t1 = time.time()
                count = await db[coll].estimated_document_count()
                coll_ms = (time.time() - t1) * 1000
                ok(f"  {coll}: {count:,} documents ({coll_ms:.0f}ms)")
                record("mongodb", f"collection_{coll}", "ok", coll_ms, f"count={count}")
            except Exception as e:
                warn(f"  {coll}: {e}")
                record("mongodb", f"collection_{coll}", "warn", 0, str(e))

        # Check for recent chat activity
        try:
            t1 = time.time()
            recent = await db["chats"].find_one({}, sort=[("created_at", -1)])
            chat_ms = (time.time() - t1) * 1000
            if recent:
                created = recent.get("created_at", "?")
                ok(f"  Latest chat: created_at={str(created)[:19]} ({chat_ms:.0f}ms)")
            else:
                warn("  No chats found")
        except Exception as e:
            warn(f"  Latest chat query failed: {e}")

        client.close()
    except Exception as e:
        ms = (time.time() - t0) * 1000
        fail(f"MongoDB connection error ({ms:.0f}ms): {e}")
        record("mongodb", "connection", "error", ms, str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 5. CHAT SPEED TEST (the critical one)
# ═════════════════════════════════════════════════════════════════════════════
async def _stream_chat(payload: dict, label: str, section: str, jwt: Optional[str] = None):
    url = f"{PROD_CHAT}?_t={BUST()}"
    headers = {
        **NO_CACHE_HEADERS,
        "Content-Type": "application/json",
        "Origin": "https://syrabit.ai",
    }
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    else:
        safe_label = label.encode('ascii', 'ignore').decode().replace(' ', '_').replace('-', '')[:16]
        headers["x-anon-id"] = f"speedtest-{safe_label}-{int(time.time())}"
    t_start = time.time()
    t_first_chunk = None
    full_text = ""
    model = "?"
    route_trace = {}
    chunks_received = 0
    error_msg = None
    server_latency_ms = None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=5.0),
            follow_redirects=True,
            headers=headers,
        ) as c:
            async with c.stream("POST", url, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    ms = (time.time() - t_start) * 1000
                    if resp.status_code == 429:
                        # Rate limiter is working correctly — WARN not FAIL
                        warn(f"{label}: rate limited (429) — anon IP quota hit; "
                             f"set ADMIN_EMAIL+ADMIN_PASSWORD for auth bypass  ({ms:.0f}ms)")
                        record(section, label, "warn", ms, "HTTP 429 — rate limit active")
                    else:
                        fail(f"{label}: HTTP {resp.status_code}: {body[:200]}")
                        record(section, label, "fail", ms, f"HTTP {resp.status_code}")
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if not raw:
                        continue
                    try:
                        evt = json.loads(raw)
                    except Exception:
                        continue

                    if "error" in evt:
                        error_msg = evt["error"]
                        break

                    content = evt.get("content", "")
                    if content and t_first_chunk is None:
                        t_first_chunk = time.time()

                    if content:
                        full_text += content
                        chunks_received += 1

                    if evt.get("done"):
                        model       = evt.get("model", "?")
                        route_trace = evt.get("route_trace", {})
                        server_latency_ms = evt.get("latency_ms")
                        break

    except Exception as e:
        ms = (time.time() - t_start) * 1000
        fail(f"{label}: exception ({ms:.0f}ms): {e}")
        record(section, label, "error", ms, str(e))
        return

    t_end = time.time()
    ttfb_ms   = (t_first_chunk - t_start) * 1000 if t_first_chunk else -1
    total_ms  = (t_end - t_start) * 1000
    words     = len(full_text.split()) if full_text else 0
    chars     = len(full_text)

    fallback  = route_trace.get("fallback", False)
    decision  = route_trace.get("decision", "?")

    if error_msg:
        fail(f"{label}: AI error — {error_msg}  ({total_ms:.0f}ms total)")
        record(section, label, "fail", total_ms, error_msg)
        return

    ttfb_str    = f"{ttfb_ms:.0f}ms" if ttfb_ms >= 0 else "no-content"
    srv_lat_str = f"  server_latency={server_latency_ms}ms" if server_latency_ms else ""
    fallback_str = f" {Y}⚡fallback={fallback}{X}" if fallback else ""

    # Target: <3 s TTFB now that thinkingBudget:0 is active
    TTFB_TARGET = 3000
    ttfb_ok  = ttfb_ms >= 0 and ttfb_ms < TTFB_TARGET
    ttfb_col = G if ttfb_ok else Y

    print(f"  {G}✓{X} {label}")
    print(f"      TTFB:   {BOLD}{ttfb_col}{ttfb_str}{X}{srv_lat_str}")
    print(f"      Total:  {BOLD}{total_ms:.0f}ms{X}")
    print(f"      Model:  {model}  route={decision}{fallback_str}")
    print(f"      Answer: {words} words / {chars} chars / {chunks_received} chunks")
    if full_text:
        preview = full_text[:120].replace("\n", " ")
        print(f"      Preview: {C}{preview}…{X}")

    status = "ok" if ttfb_ok else "warn"
    record(section, label, status, total_ms,
           f"TTFB={ttfb_str} model={model} fallback={fallback} words={words}")

async def section_chat():
    head("5. Chat Speed Test (live production)")
    tests = [
        {"message": "What is osmosis?", "model": "default",
         "response_lang": "en", "lang": "en"},
        {"message": "Explain Newton's first law of motion with an example.",
         "model": "default", "response_lang": "en", "lang": "en"},
        {"message": "What is photosynthesis? Keep it brief.",
         "model": "default", "response_lang": "en", "lang": "en"},
        {"message": "অসমোছিছ কি?", "model": "default",
         "response_lang": "as", "lang": "as"},
    ]
    labels = [
        "EN — short (osmosis)",
        "EN — medium (Newton's law)",
        "EN — brief instruction",
        "AS — Assamese (অসমোছিছ)",
    ]
    jwt = await _get_jwt()
    if jwt:
        info(f"Authenticated as {ADMIN_EMAIL} — rate limits bypassed")
    else:
        warn("No ADMIN_EMAIL/ADMIN_PASSWORD set — chat runs as anon (may hit rate limits)")
    for payload, label in zip(tests, labels):
        await _stream_chat(payload, label, "chat", jwt=jwt)
        await asyncio.sleep(1.0)   # 1s gap between requests to ease rate limiting

# ═════════════════════════════════════════════════════════════════════════════
# 6. VERTEX AI DIRECT
# ═════════════════════════════════════════════════════════════════════════════
async def section_vertex():
    head("6. Vertex AI Direct (service account)")
    if not GCP_CREDS_JSON:
        fail("GOOGLE_APPLICATION_CREDENTIALS_JSON not set"); return

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GRequest
        creds_info = json.loads(GCP_CREDS_JSON)
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        t0 = time.time()
        creds.refresh(GRequest())
        token_ms = (time.time() - t0) * 1000
        ok(f"OAuth2 token refreshed in {token_ms:.0f}ms")
        record("vertex", "oauth_token", "ok", token_ms)
    except Exception as e:
        fail(f"Token refresh failed: {e}")
        record("vertex", "oauth_token", "error", 0, str(e))
        return

    url = (f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1"
           f"/projects/{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}"
           f"/publishers/google/models/{VERTEX_MODEL}:generateContent")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Say 'hello' in one word."}]}],
        "generationConfig": {"maxOutputTokens": 5, "temperature": 0},
    }
    if "2.5" in VERTEX_MODEL:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}

    t0 = time.time()
    try:
        async with make_client(20) as c:
            resp = await c.post(url,
                headers={"Authorization": f"Bearer {creds.token}",
                         "Content-Type": "application/json"},
                json=payload)
        ms = (time.time() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            text = (data.get("candidates", [{}])[0]
                        .get("content", {}).get("parts", [{}])[0]
                        .get("text", "?"))
            ok(f"Vertex generateContent: {ms:.0f}ms  model={VERTEX_MODEL}  reply={repr(text)}")
            record("vertex", "generate", "ok", ms, f"model={VERTEX_MODEL} reply={text}")
        else:
            fail(f"Vertex HTTP {resp.status_code} ({ms:.0f}ms): {resp.text[:200]}")
            record("vertex", "generate", "fail", ms,
                   f"HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        ms = (time.time() - t0) * 1000
        fail(f"Vertex call failed ({ms:.0f}ms): {e}")
        record("vertex", "generate", "error", ms, str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 7. CLOUDFLARE KV & ZONE INFO
# ═════════════════════════════════════════════════════════════════════════════
async def section_cloudflare():
    head("7. Cloudflare KV & Zone Health")
    token = CF_KV_TOKEN or CF_API_TOKEN
    if not token:
        warn("No CF token — skipping"); return

    # Zone analytics (real-time traffic indicator)
    if CF_ZONE_ID:
        t0 = time.time()
        try:
            async with make_client(10) as c:
                resp = await c.get(
                    f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ms = (time.time() - t0) * 1000
            data = resp.json()
            if data.get("success"):
                zone = data["result"]
                status  = zone.get("status", "?")
                name    = zone.get("name", "?")
                plan    = zone.get("plan", {}).get("name", "?")
                ok(f"Zone: {name}  status={status}  plan={plan}  ({ms:.0f}ms)")
                record("cloudflare", "zone_info", "ok", ms, f"status={status} plan={plan}")
            else:
                warn(f"Zone query: {data.get('errors')} ({ms:.0f}ms)")
                record("cloudflare", "zone_info", "warn", ms, str(data.get("errors")))
        except Exception as e:
            fail(f"Zone info: {e}")

    # KV namespace list
    if CF_ACCOUNT_ID and CF_KV_NS:
        t0 = time.time()
        try:
            async with make_client(10) as c:
                resp = await c.get(
                    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
                    f"/storage/kv/namespaces/{CF_KV_NS}/keys?limit=5",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ms = (time.time() - t0) * 1000
            data = resp.json()
            if data.get("success"):
                keys = [k["name"] for k in data.get("result", [])]
                ok(f"KV namespace: {len(keys)} sample keys ({ms:.0f}ms): {keys[:3]}")
                record("cloudflare", "kv_keys", "ok", ms, f"sample={keys[:3]}")
            else:
                warn(f"KV list: {data.get('errors')} ({ms:.0f}ms)")
                record("cloudflare", "kv_keys", "warn", ms, str(data.get("errors")))
        except Exception as e:
            fail(f"KV list: {e}")
            record("cloudflare", "kv_keys", "error", 0, str(e))
    elif not CF_KV_NS:
        warn("CLOUDFLARE_KV_NAMESPACE_ID not set — skipping KV check")

    # Check CF Worker via production edge
    t0 = time.time()
    try:
        async with make_client(10) as c:
            resp = await c.get(f"https://api.syrabit.ai/health?_t={BUST()}")
        ms = (time.time() - t0) * 1000
        cf_ray = resp.headers.get("cf-ray", "no-ray")
        via    = resp.headers.get("via", "?")
        server = resp.headers.get("server", "?")
        ok(f"Edge request: {resp.status_code} ({ms:.0f}ms)  cf-ray={cf_ray}  server={server}")
        record("cloudflare", "edge_request", "ok", ms, f"cf-ray={cf_ray}")
    except Exception as e:
        fail(f"Edge request failed: {e}")
        record("cloudflare", "edge_request", "error", 0, str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 8. GITHUB DEPLOYMENT STATUS
# ═════════════════════════════════════════════════════════════════════════════
async def section_github():
    head("8. GitHub Deployment Status")
    if not GITHUB_TOKEN:
        warn("GITHUB_TOKEN not set — skipping"); return

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = "https://api.github.com"

    REPO = "founder24/-kalukaliya"
    t0 = time.time()
    try:
        async with make_client(10) as c:
            resp = await c.get(f"{base}/repos/{REPO}", headers=headers)
        ms = (time.time() - t0) * 1000
        repo = resp.json()
        if resp.status_code == 200:
            ok(f"Repo {REPO} — pushed_at={repo.get('pushed_at','?')[:16]}  branch={repo.get('default_branch','?')} ({ms:.0f}ms)")
            record("github", "repo_access", "ok", ms, f"pushed={repo.get('pushed_at','?')[:16]}")
        else:
            fail(f"Repo access: {repo.get('message','?')} ({ms:.0f}ms)")
            record("github", "repo_access", "fail", ms, repo.get("message","?"))
            return

        # Latest workflow runs — only flag the MOST RECENT run per workflow name
        t1 = time.time()
        async with make_client(10) as c:
            r2 = await c.get(f"{base}/repos/{REPO}/actions/runs?per_page=10", headers=headers)
        runs_ms = (time.time() - t1) * 1000
        runs = r2.json().get("workflow_runs", [])
        total_runs = r2.json().get("total_count", 0)
        info(f"Total workflow runs: {total_runs}")

        # Deduplicate: keep only the first (most recent) occurrence of each workflow name
        seen_workflows: set = set()
        for run in runs[:10]:
            name    = run.get("name", "?")
            concl   = run.get("conclusion", "in_progress")
            created = run.get("created_at", "?")[:16]
            branch  = run.get("head_branch", "?")
            run_id  = run.get("id")
            is_latest = name not in seen_workflows
            seen_workflows.add(name)

            icon = G+"✓"+X if concl=="success" else (Y+"·"+X if concl in ("skipped","cancelled","in_progress") else R+"✗"+X)
            tag  = "" if is_latest else f"  {B}(historical){X}"
            print(f"    {icon} {name}: {concl} @ {created} [{branch}]{tag}")

            if is_latest:
                # Only record pass/fail/warn for the most recent run of each workflow
                status = "ok" if concl == "success" else ("warn" if concl in ("skipped", "cancelled", "in_progress") else "fail")
                record("github", f"workflow_{name}", status, runs_ms, f"{concl} @ {created}")

            # Drill into failed run jobs (only for most recent, or first failure in list)
            if concl == "failure" and is_latest:
                async with make_client(10) as c:
                    r3 = await c.get(f"{base}/repos/{REPO}/actions/runs/{run_id}/jobs", headers=headers)
                jobs = r3.json().get("jobs", [])
                for j in jobs:
                    j_concl = j.get("conclusion","?")
                    j_icon  = G+"✓"+X if j_concl=="success" else (Y+"·"+X if j_concl in ("skipped","cancelled") else R+"✗"+X)
                    print(f"        {j_icon} Job: {j['name']} — {j_concl}")
                    if j_concl == "failure":
                        for step in j.get("steps", []):
                            if step.get("conclusion") == "failure":
                                print(f"            {R}✗{X} FAILED STEP: {step['name']}")
                                record("github", f"step_{step['name'][:40]}", "fail", 0,
                                       f"job={j['name']}")

    except Exception as e:
        ms = (time.time() - t0) * 1000
        fail(f"GitHub API error ({ms:.0f}ms): {e}")
        record("github", "api_access", "error", ms, str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 9. SENTRY
# ═════════════════════════════════════════════════════════════════════════════
async def section_sentry():
    head("9. Sentry DSN Reachability")
    if not SENTRY_DSN:
        warn("SENTRY_DSN not set — skipping"); return

    # Parse DSN: https://<key>@<host>/project_id
    try:
        import urllib.parse as up
        parsed = up.urlparse(SENTRY_DSN)
        sentry_host = parsed.hostname
        project_id  = parsed.path.strip("/")
        info(f"DSN host={sentry_host}  project={project_id}")

        t0 = time.time()
        async with make_client(8) as c:
            # Sentry ingest health check — POST to envelope endpoint with empty body
            # Returns 400 (bad envelope) when reachable, connection error when not
            resp = await c.post(
                f"https://{sentry_host}/api/{project_id}/envelope/",
                headers={"Content-Type": "application/x-sentry-envelope",
                         "X-Sentry-Auth": f"Sentry sentry_key={parsed.username}"},
                content=b"",
            )
        ms = (time.time() - t0) * 1000
        if resp.status_code in (200, 400, 401, 403, 405):
            ok(f"Sentry ingest reachable ({ms:.0f}ms)  status={resp.status_code}  host={sentry_host}")
            record("sentry", "dsn_reachable", "ok", ms, f"status={resp.status_code}")
        else:
            warn(f"Sentry returned {resp.status_code} ({ms:.0f}ms)")
            record("sentry", "dsn_reachable", "warn", ms, f"status={resp.status_code}")
    except Exception as e:
        fail(f"Sentry check failed: {e}")
        record("sentry", "dsn_reachable", "error", 0, str(e))

# ═════════════════════════════════════════════════════════════════════════════
# 10. FULL SUMMARY
# ═════════════════════════════════════════════════════════════════════════════
def print_summary():
    head("SUMMARY")
    total = len(results)
    oks   = [r for r in results if r["status"] == "ok"]
    warns = [r for r in results if r["status"] == "warn"]
    fails = [r for r in results if r["status"] in ("fail", "error")]

    print(f"  {G}✓ PASS: {len(oks)}{X}  {Y}⚠ WARN: {len(warns)}{X}  {R}✗ FAIL: {len(fails)}{X}  of {total} checks\n")

    if fails:
        print(f"  {BOLD}{R}FAILURES:{X}")
        for r in fails:
            print(f"    {R}✗{X} [{r['section']}] {r['name']}: {r['detail']}")

    if warns:
        print(f"\n  {BOLD}{Y}WARNINGS:{X}")
        for r in warns:
            print(f"    {Y}⚠{X} [{r['section']}] {r['name']}: {r['detail']}")

    # Chat latency summary
    chat = [r for r in results if r["section"] == "chat"]
    if chat:
        print(f"\n  {BOLD}{C}CHAT LATENCY:{X}")
        for r in chat:
            icon = G+"✓"+X if r["status"]=="ok" else (Y+"⚠"+X if r["status"]=="warn" else R+"✗"+X)
            print(f"    {icon} {r['name']}: {r['ms']}ms total — {r['detail']}")

    print()

# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
SECTIONS = {
    "cloudflare_purge": section_cloudflare_purge,
    "frontend":         section_frontend,
    "api":              section_api,
    "mongodb":          section_mongodb,
    "chat":             section_chat,
    "vertex":           section_vertex,
    "cloudflare":       section_cloudflare,
    "github":           section_github,
    "sentry":           section_sentry,
}

async def main():
    parser = argparse.ArgumentParser(description="Syrabit live deployment test")
    parser.add_argument("--section", default="all",
                        help="Section to run: all | " + " | ".join(SECTIONS))
    args = parser.parse_args()

    print(f"\n{BOLD}{M}{'═'*60}")
    print(f"  SYRABIT FULL-STACK LIVE DEPLOYMENT TEST")
    print(f"  Target: {PROD_API}  /  {PROD_FRONT}")
    print(f"  Time:   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Cache:  NO-CACHE headers + ?_t=<epoch_ms> busting on all requests")
    print(f"{'═'*60}{X}\n")

    if args.section == "all":
        run_order = list(SECTIONS.keys())
    elif args.section in SECTIONS:
        run_order = [args.section]
    else:
        print(f"Unknown section: {args.section}")
        print(f"Available: all | {' | '.join(SECTIONS)}")
        sys.exit(1)

    for sec in run_order:
        try:
            await SECTIONS[sec]()
        except Exception as e:
            fail(f"Section {sec} crashed: {e}")
            traceback.print_exc()

    print_summary()

if __name__ == "__main__":
    asyncio.run(main())
