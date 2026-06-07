#!/usr/bin/env python3
# Cloud Shell: use  python3  and  pip3  (not python/pip)
"""
seed_class11_physics_topics.py
===============================
Generates topic-wise KnowledgeObject nodes for every Class 11 Physics chapter
already seeded in MongoDB, then fans out to all three storage tiers:

  1. MongoDB        — upserts KnowledgeObject (slug-keyed, idempotent)
  2. Cloudflare KV  — bulk-puts 5 rendered HTML page-types per topic
  3. GCS            — writes canonical JSON (knowledge/<slug>.json)
  4. Vertex AI Search — upserts chunked content documents

All credentials are pulled live from GCP Secret Manager so no .env file
is required. Run from Cloud Shell (ADC already present):

  cd ~/syrabit
  python infra/scripts/seed_class11_physics_topics.py

  # Dry-run (shows what would be processed, no writes)
  python infra/scripts/seed_class11_physics_topics.py --dry-run

  # Only one board
  python infra/scripts/seed_class11_physics_topics.py --board AHSEC

  # Skip individual storage tiers
  python infra/scripts/seed_class11_physics_topics.py --skip-kv --skip-gcs

Dependencies (all available in Cloud Shell or pip install):
  pip install pymongo google-cloud-secret-manager google-generativeai \
              google-cloud-storage google-cloud-discoveryengine \
              httpx jinja2 markupsafe
"""

import argparse
import asyncio
import hashlib
import html as html_lib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from uuid import uuid4

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("seed_physics")

GCP_PROJECT = "blissful-acumen-495019-t6"
DB_NAME = "syrabit_prod"
BASE_URL = "https://syrabit.ai"
PAGE_TYPES = ["notes", "mcqs", "summary", "definitions", "important-questions"]
GEMINI_MODEL = "gemini-1.5-flash"

# ── GCP secret-name → env-var-name ──────────────────────────────────────────
SECRET_MAP = {
    "mongodb-uri": "MONGODB_URI",
    "GEMINI_API_KEY": "GEMINI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    "VERTEX_PROJECT_ID": "VERTEX_PROJECT_ID",
    "VERTEX_SEARCH_DATASTORE_ID": "VERTEX_SEARCH_DATASTORE_ID",
    "CF_KV_API_TOKEN": "CLOUDFLARE_KV_API_TOKEN",
    "CF_ACCOUNT_ID": "CLOUDFLARE_ACCOUNT_ID",
    "CF_KV_NAMESPACE_ID": "CLOUDFLARE_KV_NAMESPACE_ID",
    "GCS_CONTENT_BUCKET": "GCS_CONTENT_BUCKET",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SECRET LOADING  (uses gcloud CLI — same auth as update-and-test.sh)
# ═══════════════════════════════════════════════════════════════════════════════

def _gcloud_secret(gcp_name: str) -> str | None:
    """Fetch one secret via `gcloud secrets versions access` (uses gcloud token, not ADC)."""
    import subprocess
    try:
        result = subprocess.run(
            [
                "gcloud", "secrets", "versions", "access", "latest",
                f"--secret={gcp_name}",
                f"--project={GCP_PROJECT}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        log.debug(f"  gcloud stderr for {gcp_name}: {result.stderr.strip()[:120]}")
        return None
    except FileNotFoundError:
        return None   # gcloud not installed
    except Exception as e:
        log.debug(f"  gcloud error for {gcp_name}: {e}")
        return None


def load_secrets() -> dict:
    """
    Pull every secret from GCP Secret Manager using the gcloud CLI.
    This avoids the ADC scope issue (ACCESS_TOKEN_SCOPE_INSUFFICIENT) that
    the Python SDK hits in Cloud Shell — gcloud uses its own auth token.
    Falls back to environment variables for any secret not found.
    """
    import shutil
    env = {}
    loaded = skipped = 0

    if not shutil.which("gcloud"):
        log.warning("gcloud CLI not found — falling back to environment variables only")
    else:
        account = ""
        try:
            import subprocess
            r = subprocess.run(
                ["gcloud", "config", "get-value", "account"],
                capture_output=True, text=True, timeout=5,
            )
            account = r.stdout.strip()
        except Exception:
            pass
        log.info(f"  gcloud account: {account or '(unknown)'}")

        for gcp_name, env_name in SECRET_MAP.items():
            value = _gcloud_secret(gcp_name)
            if value:
                env[env_name] = value
                os.environ[env_name] = value
                loaded += 1
                log.info(f"  ✓ {gcp_name} → {env_name}")
            else:
                # Fall back to existing env var
                existing = os.environ.get(env_name, "")
                if existing:
                    env[env_name] = existing
                    skipped += 1
                    log.info(f"  ~ {gcp_name} not in SM — using env var {env_name}")
                else:
                    skipped += 1
                    log.warning(f"  ✗ {gcp_name} not found (SM or env)")

    # Env-var-only fallback (when gcloud absent)
    for env_name in SECRET_MAP.values():
        if env_name not in env and os.environ.get(env_name):
            env[env_name] = os.environ[env_name]

    log.info(f"Secrets loaded: {loaded} from SM, {skipped} fallback/missing")
    return env


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MONGODB — find Class 11 Physics chapters
# ═══════════════════════════════════════════════════════════════════════════════

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def connect_mongo(uri: str):
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
    client.admin.command("ping")
    log.info("MongoDB connected ✓")
    return client[DB_NAME]


def find_class11_physics_chapters(db, board_filter: str | None = None) -> list[dict]:
    """
    Traverse Board → Class → Stream → Subject → Chapter and return all
    Class 11 Physics chapters (published or draft, with at least one topic).
    """
    # ── Boards ───────────────────────────────────────────────────────────────
    board_query = {}
    if board_filter:
        board_query = {"name": {"$regex": board_filter, "$options": "i"}}
    boards = {str(b["_id"]): b for b in db.boards.find(board_query)}
    log.info(f"Boards found: {[b['name'] for b in boards.values()]}")

    # ── Classes — match "11", "XI", "Class XI", "Class 11" ──────────────────
    class_re = re.compile(r"\b(11|XI)\b", re.IGNORECASE)
    classes = {}
    for cls in db.classes.find({"board_id": {"$in": list(boards.keys())}}):
        if class_re.search(cls.get("name", "")):
            classes[str(cls["_id"])] = cls

    log.info(f"Class 11 entries: {len(classes)}")
    if not classes:
        log.warning("No Class 11 entries found. Trying string board_id match …")
        # Some records store board_id as a string slug like "b1"
        board_slugs = [b.get("slug", "") for b in boards.values()]
        board_names = [b.get("name", "") for b in boards.values()]
        all_cls = list(db.classes.find({}))
        for cls in all_cls:
            bid = str(cls.get("board_id", ""))
            if (bid in boards or bid in board_slugs or bid in board_names) and class_re.search(cls.get("name", "")):
                classes[str(cls["_id"])] = cls
        log.info(f"Class 11 entries (fallback): {len(classes)}")

    # ── Streams ───────────────────────────────────────────────────────────────
    streams = {}
    for st in db.streams.find({"class_id": {"$in": list(classes.keys())}}):
        streams[str(st["_id"])] = st

    # Also check string class_id
    class_ids_str = list(classes.keys())
    for st in db.streams.find({}):
        cid = str(st.get("class_id", ""))
        if cid in class_ids_str and str(st["_id"]) not in streams:
            streams[str(st["_id"])] = st

    log.info(f"Streams (Class 11): {len(streams)}")

    # ── Subjects — Physics ───────────────────────────────────────────────────
    physics_re = re.compile(r"\bphysics\b", re.IGNORECASE)
    subjects = {}
    for sub in db.subjects.find({"stream_id": {"$in": list(streams.keys())}}):
        if physics_re.search(sub.get("name", "")):
            subjects[str(sub["_id"])] = sub

    # Fallback with string stream_id
    stream_ids_str = list(streams.keys())
    for sub in db.subjects.find({}):
        sid = str(sub.get("stream_id", ""))
        if sid in stream_ids_str and physics_re.search(sub.get("name", "")):
            if str(sub["_id"]) not in subjects:
                subjects[str(sub["_id"])] = sub

    log.info(f"Physics subjects (Class 11): {len(subjects)}")

    # ── Chapters ─────────────────────────────────────────────────────────────
    chapters = []
    for ch in db.chapters.find({"subject_id": {"$in": list(subjects.keys())}}):
        ch["_subject"] = subjects.get(str(ch["subject_id"]), {})
        ch["_stream"] = streams.get(str(ch["_subject"].get("stream_id", "")), {})
        ch["_class"] = classes.get(str(ch["_stream"].get("class_id", "")), {})
        ch["_board"] = boards.get(str(ch["_class"].get("board_id", "")), {})
        chapters.append(ch)

    # Fallback string subject_id
    sub_ids_str = list(subjects.keys())
    found_ids = {str(c["_id"]) for c in chapters}
    for ch in db.chapters.find({}):
        sid = str(ch.get("subject_id", ""))
        if sid in sub_ids_str and str(ch["_id"]) not in found_ids:
            ch["_subject"] = subjects.get(sid, {})
            ch["_stream"] = streams.get(str(ch["_subject"].get("stream_id", "")), {})
            ch["_class"] = classes.get(str(ch["_stream"].get("class_id", "")), {})
            ch["_board"] = boards.get(str(ch["_class"].get("board_id", "")), {})
            chapters.append(ch)

    log.info(f"Class 11 Physics chapters found: {len(chapters)}")
    for ch in chapters:
        board_name = ch["_board"].get("name", "?")
        log.info(f"  [{board_name}] Ch{ch.get('chapter_number','?')}: {ch['title']} "
                 f"({len(ch.get('published_topics', []))} topics)")
    return chapters


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GEMINI CONTENT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def build_gemini_client(api_key: str):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(GEMINI_MODEL)


SYSTEM_PROMPT = """You are an expert physics teacher producing high-quality NCERT-aligned
educational content for Indian Class 11 students (AHSEC / SEBA boards).
Always respond in strict, valid JSON only — no markdown fences, no prose outside the JSON."""


def _call_gemini(model, prompt: str, retries: int = 3) -> str:
    import time
    for attempt in range(retries):
        try:
            resp = model.generate_content(
                f"{SYSTEM_PROMPT}\n\n{prompt}",
                generation_config={"temperature": 0.4, "max_output_tokens": 8192},
            )
            return resp.text.strip()
        except Exception as e:
            log.warning(f"Gemini attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError("Gemini failed after retries")


def generate_topic_content(model, chapter_title: str, topic_title: str,
                            board: str, class_level: str) -> dict:
    """
    Ask Gemini to produce all content for one topic node in a single call.
    Returns a dict matching the KnowledgeObject shape.
    """
    prompt = f"""Generate comprehensive Class 11 Physics content for:
Board: {board}
Chapter: {chapter_title}
Topic: {topic_title}

Return ONLY a single JSON object with exactly these keys:

{{
  "title": "<topic_title> - <chapter_title> | Class 11 Physics",
  "description": "<150-char SEO meta description>",
  "keywords": ["keyword1", "keyword2", ...],
  "body_markdown": "<detailed notes in markdown, min 800 words, use ## subheadings, ## Examples, ## Key Points>",
  "mcqs": [
    {{"question": "...", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "answer": "A. ..."}}
  ],
  "summary": "<3-4 paragraph concise summary of the topic>",
  "definitions": [
    {{"term": "...", "definition": "..."}}
  ],
  "important_questions": [
    {{"question": "...", "answer": "..."}}
  ]
}}

Requirements:
- body_markdown: min 800 words, NCERT-aligned, include formulas in text form e.g. F = ma
- mcqs: exactly 10 questions, 4 options each, one correct answer
- summary: plain prose, 3-4 paragraphs
- definitions: 8-12 key terms from the topic
- important_questions: 8 questions with model answers (2-3 sentences each)
"""
    raw = _call_gemini(model, prompt)

    # Strip markdown code fences if Gemini wraps them anyway
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract first JSON object
        m = re.search(r"\{[\s\S]+\}", raw)
        if m:
            data = json.loads(m.group(0))
        else:
            raise ValueError(f"Gemini returned non-JSON for topic '{topic_title}':\n{raw[:400]}")

    return data


# ═══════════════════════════════════════════════════════════════════════════════
# 4. KNOWLEDGE OBJECT — upsert to MongoDB
# ═══════════════════════════════════════════════════════════════════════════════

def build_ko_doc(chapter: dict, topic: dict, content: dict, board_slug: str,
                  class_slug: str, subject_slug: str) -> dict:
    """Assemble a KnowledgeObject document from generated content."""
    chapter_slug = chapter.get("slug", slugify(chapter["title"]))
    topic_slug = topic.get("topic_slug", slugify(topic["title"]))
    slug = f"{board_slug}-class-{class_slug}-physics-{chapter_slug}-{topic_slug}"
    slug = re.sub(r"-+", "-", slug)[:200]

    now = datetime.now(timezone.utc)

    return {
        "slug": slug,
        "title": content.get("title", f"{topic['title']} | Class 11 Physics"),
        "description": content.get("description", ""),
        "body_markdown": content.get("body_markdown", ""),
        "content_blocks": [],
        "metadata": {
            "board": board_slug.upper(),
            "class_level": class_slug,
            "subject": subject_slug,
            "chapter": chapter_slug,
            "chapter_number": chapter.get("chapter_number"),
            "topic": topic_slug,
            "difficulty": "medium",
            "language": "en",
            "estimated_read_time_minutes": max(5, len(content.get("body_markdown", "")) // 1000),
            "keywords": content.get("keywords", []),
        },
        "generated": {
            "mcqs": content.get("mcqs", []),
            "summary": content.get("summary", ""),
            "definitions": content.get("definitions", []),
            "important_questions": content.get("important_questions", []),
        },
        "derivative_hashes": {},
        "rendered_html": {},
        "status": "published",
        "published_at": now,
        "last_pipeline_run": now,
        "page_views": 0,
        "search_impressions": 0,
        "created_at": now,
        "updated_at": now,
        "_chapter_id": str(chapter["_id"]),
        "_topic_id": topic.get("id", ""),
        "_board_name": chapter["_board"].get("name", ""),
    }


def upsert_ko(db, ko: dict, dry_run: bool = False) -> str:
    """Upsert KnowledgeObject by slug. Returns the slug."""
    if dry_run:
        log.info(f"  [dry-run] would upsert KO: {ko['slug']}")
        return ko["slug"]

    ko["updated_at"] = datetime.now(timezone.utc)
    db.knowledge_objects.update_one(
        {"slug": ko["slug"]},
        {"$set": ko, "$setOnInsert": {"created_at": ko["created_at"]}},
        upsert=True,
    )
    return ko["slug"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. HTML RENDERER (inline — no backend import needed)
# ═══════════════════════════════════════════════════════════════════════════════

_HTML_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} | Syrabit</title>
<meta name="description" content="{description}">
<meta name="keywords" content="{keywords}">
<link rel="canonical" href="{canonical}">
<script type="application/ld+json">{jsonld}</script>
</head>
<body>
<nav>{nav_links}</nav>
<main>{body}</main>
</body>
</html>"""


def _esc(s: str) -> str:
    return html_lib.escape(str(s))


def _md_to_html(md: str) -> str:
    parts = []
    for block in md.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("### "):
            parts.append(f"<h3>{_esc(block[4:])}</h3>")
        elif block.startswith("## "):
            parts.append(f"<h2>{_esc(block[3:])}</h2>")
        elif block.startswith("# "):
            parts.append(f"<h1>{_esc(block[2:])}</h1>")
        elif block.startswith("- ") or block.startswith("* "):
            items = [re.sub(r"^[-*]\s+", "", ln) for ln in block.splitlines() if ln.strip()]
            lis = "".join(f"<li>{_esc(i)}</li>" for i in items)
            parts.append(f"<ul>{lis}</ul>")
        else:
            parts.append(f"<p>{_esc(block)}</p>")
    return "\n".join(parts)


def _render_body(ko: dict, page_type: str) -> str:
    gen = ko.get("generated", {})
    if page_type == "notes":
        return _md_to_html(ko.get("body_markdown", ""))
    elif page_type == "mcqs":
        mcqs = gen.get("mcqs", [])
        if not mcqs:
            return "<p>No MCQs available yet.</p>"
        parts = ['<section class="mcqs">']
        for i, q in enumerate(mcqs, 1):
            parts.append(f'<article class="mcq"><h3>Q{i}. {_esc(q.get("question",""))}</h3>')
            parts.append('<ol type="A">' + "".join(f"<li>{_esc(o)}</li>" for o in q.get("options", [])) + "</ol>")
            parts.append(f'<details><summary>Answer</summary><p>{_esc(q.get("answer",""))}</p></details></article>')
        parts.append("</section>")
        return "\n".join(parts)
    elif page_type == "summary":
        summary = gen.get("summary", "")
        return "\n".join(f"<p>{_esc(p.strip())}</p>" for p in summary.split("\n\n") if p.strip())
    elif page_type == "definitions":
        defs = gen.get("definitions", [])
        if not defs:
            return "<p>No definitions available yet.</p>"
        parts = ['<dl class="definitions">']
        for d in defs:
            parts.append(f'<dt>{_esc(d.get("term",""))}</dt><dd>{_esc(d.get("definition",""))}</dd>')
        parts.append("</dl>")
        return "\n".join(parts)
    elif page_type == "important-questions":
        qs = gen.get("important_questions", [])
        if not qs:
            return "<p>No important questions available yet.</p>"
        parts = ['<section class="important-questions">']
        for i, q in enumerate(qs, 1):
            parts.append(f'<article><h3>Q{i}. {_esc(q.get("question",""))}</h3>'
                         f'<p><strong>Answer:</strong> {_esc(q.get("answer",""))}</p></article>')
        parts.append("</section>")
        return "\n".join(parts)
    return ""


def render_all_html(ko: dict) -> dict[str, str]:
    """Render all 5 page types to HTML. Returns {page_type: html_string}."""
    meta = ko.get("metadata", {})
    board = meta.get("board", "").lower()
    cls = meta.get("class_level", "")
    subj = meta.get("subject", "")
    chapter = meta.get("chapter", "")
    base_path = f"/render/{board}/{cls}/{subj}/{chapter}"

    nav = " | ".join(
        f'<a href="{base_path}/{pt}">{pt.replace("-"," ").title()}</a>'
        for pt in PAGE_TYPES
    )

    keywords_str = ", ".join(meta.get("keywords", []))

    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "name": ko.get("title", ""),
        "description": ko.get("description", ""),
        "url": f"{BASE_URL}{base_path}/notes",
        "publisher": {"@type": "Organization", "name": "Syrabit Education"},
    })

    rendered = {}
    for pt in PAGE_TYPES:
        body = _render_body(ko, pt)
        html = _HTML_TPL.format(
            title=_esc(f"{ko.get('title','')} — {pt.replace('-',' ').title()}"),
            description=_esc(ko.get("description", "")),
            keywords=_esc(keywords_str),
            canonical=f"{BASE_URL}{base_path}/{pt}",
            jsonld=jsonld,
            nav_links=nav,
            body=body,
        )
        rendered[pt] = html
    return rendered


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CLOUDFLARE KV PUSH
# ═══════════════════════════════════════════════════════════════════════════════

async def push_cloudflare_kv(ko: dict, rendered_html: dict[str, str],
                              env: dict, dry_run: bool = False) -> bool:
    token = env.get("CLOUDFLARE_KV_API_TOKEN", "")
    account_id = env.get("CLOUDFLARE_ACCOUNT_ID", "")
    namespace_id = env.get("CLOUDFLARE_KV_NAMESPACE_ID", "")

    if not all([token, account_id, namespace_id]):
        log.warning("  Cloudflare KV not configured — skipping")
        return False

    meta = ko.get("metadata", {})
    base_key = f"{meta.get('board','').lower()}/{meta.get('class_level','')}/{meta.get('subject','')}/{meta.get('chapter','')}_{meta.get('topic','')}"

    kv_pairs = [
        {"key": f"{base_key}/{pt}", "value": html}
        for pt, html in rendered_html.items()
    ]

    if dry_run:
        log.info(f"  [dry-run] would push {len(kv_pairs)} KV keys for {ko['slug']}")
        return True

    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/storage/kv/namespaces/{namespace_id}/bulk")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.put(
            url,
            json=kv_pairs,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
    if resp.status_code == 200:
        log.info(f"  ✓ Cloudflare KV: pushed {len(kv_pairs)} entries")
        return True
    log.warning(f"  ✗ Cloudflare KV: HTTP {resp.status_code} — {resp.text[:200]}")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 7. GCS WRITE
# ═══════════════════════════════════════════════════════════════════════════════

def write_gcs(ko: dict, env: dict, dry_run: bool = False) -> bool:
    project_id = env.get("VERTEX_PROJECT_ID", "")
    bucket_name = env.get("GCS_CONTENT_BUCKET", "") or (f"{project_id}-syrabit-content" if project_id else "")

    if not bucket_name:
        log.warning("  GCS bucket not configured — skipping")
        return False

    creds_json = env.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
    if dry_run:
        log.info(f"  [dry-run] would write gs://{bucket_name}/knowledge/{ko['slug']}.json")
        return True

    try:
        from google.cloud import storage
        from google.oauth2 import service_account

        if creds_json:
            creds_info = json.loads(creds_json)
            credentials = service_account.Credentials.from_service_account_info(creds_info)
            gcs = storage.Client(project=project_id, credentials=credentials)
        else:
            gcs = storage.Client(project=project_id)

        bucket = gcs.bucket(bucket_name)
        blob = bucket.blob(f"knowledge/{ko['slug']}.json")

        payload = {k: v for k, v in ko.items() if not k.startswith("_")}
        payload["rendered_html"] = {}  # exclude large HTML from GCS JSON

        blob.upload_from_string(
            json.dumps(payload, ensure_ascii=False, default=str),
            content_type="application/json",
        )
        log.info(f"  ✓ GCS: gs://{bucket_name}/knowledge/{ko['slug']}.json")
        return True
    except Exception as e:
        log.error(f"  ✗ GCS write failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 8. VERTEX AI SEARCH INDEXING
# ═══════════════════════════════════════════════════════════════════════════════

def _chunk_text(text: str, chunk_tokens: int = 500) -> list[str]:
    max_chars = chunk_tokens * 4
    paragraphs = text.split("\n\n")
    chunks, current = [], ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            current = para[:max_chars] if len(para) > max_chars else para
    if current:
        chunks.append(current)
    return chunks


def index_vertex_search(ko: dict, env: dict, dry_run: bool = False) -> bool:
    project_id = env.get("VERTEX_PROJECT_ID", "")
    datastore_id = env.get("VERTEX_SEARCH_DATASTORE_ID", "")
    creds_json = env.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")

    if not all([project_id, datastore_id, creds_json]):
        log.warning("  Vertex AI Search not configured — skipping")
        return False

    chunks = _chunk_text(ko.get("body_markdown", ""))
    if not chunks:
        log.warning("  No content to index")
        return False

    if dry_run:
        log.info(f"  [dry-run] would index {len(chunks)} chunks for {ko['slug']}")
        return True

    try:
        from google.cloud import discoveryengine_v1
        from google.oauth2 import service_account
        from google.protobuf import struct_pb2

        creds_info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        client = discoveryengine_v1.DocumentServiceClient(credentials=credentials)

        parent = client.branch_path(
            project=project_id,
            location="global",
            data_store=datastore_id,
            branch="default_branch",
        )

        meta = ko.get("metadata", {})
        succeeded = 0

        for i, chunk in enumerate(chunks):
            doc_id = f"{ko['slug']}_chunk_{i}"
            struct_data = struct_pb2.Struct()
            struct_data.update({
                "title": ko.get("title", ""),
                "content": chunk,
                "slug": ko["slug"],
                "board": meta.get("board", ""),
                "class_level": meta.get("class_level", ""),
                "subject": meta.get("subject", ""),
                "chapter": meta.get("chapter", ""),
                "topic": meta.get("topic", ""),
                "difficulty": meta.get("difficulty", "medium"),
                "language": meta.get("language", "en"),
                "chunk_index": i,
                "tier_access": "free",
                "source_url": f"/render/{meta.get('board','').lower()}/{meta.get('class_level','')}/{meta.get('subject','')}/{meta.get('chapter','')}/notes",
            })
            doc = discoveryengine_v1.Document(id=doc_id, struct_data=struct_data)
            try:
                req = discoveryengine_v1.CreateDocumentRequest(
                    parent=parent, document=doc, document_id=doc_id
                )
                client.create_document(request=req)
                succeeded += 1
            except Exception:
                try:
                    doc.name = f"{parent}/documents/{doc_id}"
                    req = discoveryengine_v1.UpdateDocumentRequest(document=doc, allow_missing=True)
                    client.update_document(request=req)
                    succeeded += 1
                except Exception as ue:
                    log.error(f"  ✗ Vertex chunk {doc_id}: {ue}")

        log.info(f"  ✓ Vertex AI Search: {succeeded}/{len(chunks)} chunks indexed")
        return succeeded > 0

    except ImportError:
        log.error("  google-cloud-discoveryengine not installed. Run: pip install google-cloud-discoveryengine")
        return False
    except Exception as e:
        log.error(f"  ✗ Vertex AI Search failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 9. MAIN ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════════

async def process_topic(
    db, gemini_model, chapter: dict, topic: dict,
    board_slug: str, class_slug: str, subject_slug: str,
    env: dict, args, semaphore: asyncio.Semaphore
) -> dict:
    """Generate content for one topic and seed to all tiers."""
    async with semaphore:
        topic_title = topic.get("title", "")
        chapter_title = chapter.get("title", "")
        board_name = chapter["_board"].get("name", board_slug.upper())

        log.info(f"→ [{board_name}] {chapter_title} / {topic_title}")

        result = {
            "slug": None, "mongo": False, "kv": False,
            "gcs": False, "vertex": False, "error": None
        }

        try:
            # Generate content with Gemini
            content = await asyncio.to_thread(
                generate_topic_content, gemini_model,
                chapter_title, topic_title, board_name, class_slug
            )
            log.info(f"  ✓ Gemini: {len(content.get('body_markdown',''))} chars body")

            # Build KnowledgeObject document
            ko = build_ko_doc(chapter, topic, content, board_slug, class_slug, subject_slug)
            result["slug"] = ko["slug"]

            # Render HTML
            rendered = render_all_html(ko)
            ko["rendered_html"] = rendered
            ko["derivative_hashes"] = {
                pt: hashlib.sha256(html.encode()).hexdigest()
                for pt, html in rendered.items()
            }

            # Upsert MongoDB
            upsert_ko(db, ko, dry_run=args.dry_run)
            result["mongo"] = True
            log.info(f"  ✓ MongoDB: upserted {ko['slug']}")

            # Skip storage tiers if requested
            if not args.skip_kv:
                result["kv"] = await push_cloudflare_kv(ko, rendered, env, args.dry_run)
            if not args.skip_gcs:
                result["gcs"] = await asyncio.to_thread(write_gcs, ko, env, args.dry_run)
            if not args.skip_vertex:
                result["vertex"] = await asyncio.to_thread(index_vertex_search, ko, env, args.dry_run)

        except Exception as e:
            log.error(f"  ✗ Failed [{board_name}] {chapter_title} / {topic_title}: {e}")
            result["error"] = str(e)

        return result


async def main(args):
    log.info("═" * 60)
    log.info("  Syrabit — Class 11 Physics Topic Seeder")
    log.info("═" * 60)

    # Step 1: Load secrets
    log.info("\n[1/5] Loading secrets from GCP Secret Manager …")
    env = load_secrets()

    mongo_uri = env.get("MONGODB_URI", "")
    gemini_key = env.get("GEMINI_API_KEY", "")

    if not mongo_uri:
        log.error("MONGODB_URI not available. Aborting.")
        sys.exit(1)
    if not gemini_key:
        log.error("GEMINI_API_KEY not available. Aborting.")
        sys.exit(1)

    # Step 2: Connect MongoDB
    log.info("\n[2/5] Connecting to MongoDB …")
    db = connect_mongo(mongo_uri)

    # Step 3: Find chapters
    log.info("\n[3/5] Finding Class 11 Physics chapters …")
    chapters = find_class11_physics_chapters(db, board_filter=args.board)

    if not chapters:
        log.error("No Class 11 Physics chapters found. Check that chapters are seeded first.")
        log.error("Tip: run  python infra/scripts/seed-content.py  first.")
        sys.exit(1)

    # Collect all (chapter, topic) pairs
    work_items = []
    for ch in chapters:
        board = ch["_board"]
        board_slug = board.get("slug", slugify(board.get("name", "unknown")))
        cls = ch["_class"]
        class_slug = re.sub(r"[^\d]", "", cls.get("name", "11")) or "11"
        subject = ch["_subject"]
        subject_slug = subject.get("slug", slugify(subject.get("name", "physics")))

        topics = ch.get("published_topics", [])
        if not topics:
            log.warning(f"  Chapter '{ch['title']}' has no topics — skipping")
            continue

        for topic in topics:
            work_items.append((ch, topic, board_slug, class_slug, subject_slug))

    total = len(work_items)
    log.info(f"\nTotal topic nodes to process: {total}")

    if args.dry_run:
        log.info("[dry-run mode] No writes will be performed.\n")
        for ch, topic, *_ in work_items:
            log.info(f"  {ch['title']} / {topic.get('title','')}")
        return

    # Step 4: Build Gemini model
    log.info("\n[4/5] Initialising Gemini …")
    gemini_model = build_gemini_client(gemini_key)
    log.info(f"  Model: {GEMINI_MODEL} ✓")

    # Step 5: Process all topics (bounded concurrency)
    log.info(f"\n[5/5] Processing {total} topics (concurrency={args.concurrency}) …\n")
    semaphore = asyncio.Semaphore(args.concurrency)

    tasks = [
        process_topic(
            db, gemini_model, ch, topic,
            board_slug, class_slug, subject_slug,
            env, args, semaphore
        )
        for ch, topic, board_slug, class_slug, subject_slug in work_items
    ]

    results = await asyncio.gather(*tasks)

    # Summary
    ok = [r for r in results if r["mongo"] and not r["error"]]
    errs = [r for r in results if r["error"]]

    log.info("\n" + "═" * 60)
    log.info("  SEEDING COMPLETE")
    log.info("═" * 60)
    log.info(f"  Topics processed : {total}")
    log.info(f"  ✓ Success        : {len(ok)}")
    log.info(f"  ✗ Errors         : {len(errs)}")
    log.info(f"  Cloudflare KV    : {sum(1 for r in ok if r['kv'])}/{len(ok)}")
    log.info(f"  GCS              : {sum(1 for r in ok if r['gcs'])}/{len(ok)}")
    log.info(f"  Vertex AI Search : {sum(1 for r in ok if r['vertex'])}/{len(ok)}")

    if errs:
        log.warning("\nFailed topics:")
        for r in errs:
            log.warning(f"  {r['slug'] or '?'}: {r['error']}")

    log.info("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed Class 11 Physics topic-wise KnowledgeObjects to MongoDB + CF KV + GCS + Vertex AI"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed without writing anything")
    parser.add_argument("--board", default=None,
                        help="Filter by board name, e.g. AHSEC or SEBA (default: all boards)")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Max simultaneous Gemini calls (default: 3; keep ≤5 for rate limits)")
    parser.add_argument("--skip-kv", action="store_true", help="Skip Cloudflare KV push")
    parser.add_argument("--skip-gcs", action="store_true", help="Skip GCS write")
    parser.add_argument("--skip-vertex", action="store_true", help="Skip Vertex AI Search indexing")
    args = parser.parse_args()

    asyncio.run(main(args))
