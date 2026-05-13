"""
Syllabus Embedder
=================
Seeds chapter-level AND topic-level embeddings into Cloudflare Vectorize
(`syllabus-index`, 768 dimensions, cosine metric) and provides fast
vector-based classification of user queries to syllabus chapters/topics.

Features
--------
- Enriched embed text: title + description + full topic list + keywords (~2000 chars)
- Topic-level embeddings: one embedding per topic for precise matching
- Configurable similarity thresholds via environment variables
- Top-3 match score logging for every classify() call
- Vectorize-native nearest-neighbor search (no in-memory cache)
- Admin triggers: POST /admin/syllabus/seed-embeddings
- Admin diagnostics: GET /admin/syllabus/test-classify?q=...
- Admin stats: GET /admin/syllabus/embedding-stats

Usage
-----
    from syllabus_embedder import SyllabusEmbedder
    embedder = SyllabusEmbedder(db)          # db = motor AsyncIOMotorDB
    await embedder.ensure_seeded()            # call once at startup
    result = await embedder.classify(query)  # returns SyllabusMatch | None
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("syllabus_embedder")

EMBED_TEXT_MAX_CHARS = 2000


def _safe_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid float for env {name}={raw!r}, using default {default}")
        return default


SIMILARITY_THRESHOLD = _safe_float_env("SYLLABUS_CLASSIFY_THRESHOLD", 0.58)
SUBJECT_MATCH_BONUS = _safe_float_env("SYLLABUS_SUBJECT_MATCH_BONUS", 0.05)
SUBJECT_MISMATCH_PENALTY = _safe_float_env("SYLLABUS_SUBJECT_MISMATCH_PENALTY", 0.08)


@dataclass
class SyllabusMatch:
    board: str
    class_name: str
    stream: str
    subject_name: str
    chapter_title: str
    chapter_number: int
    subject_id: str
    chapter_id: str
    similarity: float
    level: str = "chapter"
    topic: str = ""

    def scope_query(self, user_query: str) -> str:
        parts = [self.board, self.class_name, self.stream, self.subject_name, user_query]
        return " ".join(p for p in parts if p)


def _build_rich_embed_text(
    board_name: str,
    class_name: str,
    stream_name: str,
    subject_name: str,
    chapter_title: str,
    description: str = "",
    topics: list = None,
    content: str = "",
) -> str:
    context_parts = [p for p in [board_name, class_name, stream_name, subject_name] if p]
    context = " ".join(context_parts)

    sections = [f"{context} — {chapter_title}"]

    clean_desc = (description or "").strip()
    if clean_desc and not clean_desc.lower().startswith("chapter "):
        sections.append(clean_desc)

    if topics:
        sections.append("Topics: " + ", ".join(topics))

    if content:
        keywords = _extract_content_keywords(content)
        if keywords:
            sections.append("Key terms: " + ", ".join(keywords))

    result = ". ".join(sections)
    return result[:EMBED_TEXT_MAX_CHARS]


def _extract_content_keywords(content: str, max_keywords: int = 20) -> list[str]:
    import re
    if not content:
        return []
    bold_terms = re.findall(r'\*\*([^*]{2,40})\*\*', content)
    heading_terms = re.findall(r'^#{1,4}\s+(.+)$', content, re.MULTILINE)
    terms = []
    seen = set()
    for t in bold_terms + heading_terms:
        t_clean = t.strip().lower()
        if t_clean not in seen and len(t_clean) > 2:
            seen.add(t_clean)
            terms.append(t.strip())
    return terms[:max_keywords]


def _extract_topic_snippet(content: str, topic: str, max_chars: int = 600) -> str:
    import re, difflib
    if not content or not topic:
        return ""
    topic_lower = topic.strip().lower()
    lines = content.split("\n")
    best_idx = -1
    best_ratio = 0.0
    for i, line in enumerate(lines):
        stripped = re.sub(r'^#{1,4}\s*', '', line).strip().rstrip(".").lower()
        stripped = re.sub(r'^\d+[\.\)]\s*', '', stripped).strip()
        if not stripped or len(stripped) < 3:
            continue
        ratio = difflib.SequenceMatcher(None, stripped, topic_lower).ratio()
        if ratio > best_ratio and ratio >= 0.55:
            best_ratio = ratio
            best_idx = i
        if topic_lower in stripped or stripped in topic_lower:
            best_idx = i
            break
    if best_idx < 0:
        return ""
    snippet_lines = lines[best_idx:best_idx + 15]
    snippet = "\n".join(snippet_lines).strip()
    return snippet[:max_chars]


def _build_topic_embed_text(
    board_name: str,
    class_name: str,
    stream_name: str,
    subject_name: str,
    chapter_title: str,
    topic: str,
    content: str = "",
) -> str:
    context_parts = [p for p in [board_name, class_name, stream_name, subject_name] if p]
    context = " ".join(context_parts)
    sections = [f"{context} — {chapter_title} — {topic}"]
    snippet = _extract_topic_snippet(content, topic, max_chars=800)
    if snippet:
        keywords = _extract_content_keywords(snippet, max_keywords=10)
        if keywords:
            sections.append("Key terms: " + ", ".join(keywords))
        clean_snippet = " ".join(snippet.split()[:80])
        sections.append(clean_snippet)
    return ". ".join(sections)[:EMBED_TEXT_MAX_CHARS]


def _make_vector_id(chapter_id: str, level: str = "chapter", topic: str = "") -> str:
    import hashlib
    raw = f"{chapter_id}::{level}::{topic}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class SyllabusEmbedder:
    """Manages chapter + topic embeddings via Cloudflare Vectorize."""

    def __init__(self, db):
        self._db = db
        self._seed_lock = asyncio.Lock()
        self._seeded = False


    async def _get_retriever(self):
        """Return the active retriever. Routed through the factory so
        the admin-set runtime override + RAG_RETRIEVER env var apply."""
        from retrievers import get_retriever as _gr
        return await _gr()

    async def embed_chapter(
        self,
        chapter_id: str,
        subject_id: str,
        title: str,
        description: str = "",
        topics: list = None,
        content: str = "",
    ) -> int:
        # ── Embed function: workers_ai_custom (Task #491 single-source).
        # Gemma-300M + Qwen3-0.6B mean-pool (1024-dim) via Workers AI:
        # - Multilingual: handles Assamese/Bengali queries natively
        # - 1024-dim: matches Atlas vector_index and Cloudflare Vectorize dims
        # - Routes through `vertex_services.embed_text` which now points
        #   exclusively at workers_ai_custom (Task #491). No fallback —
        #   if the primary fails we surface the error and let the SQS
        #   deferred-embed consumer replay later (V4 §15).

        async def _embed_fn_primary(text: str, task_type: str = "RETRIEVAL_DOCUMENT", **_kw) -> Optional[list]:
            try:
                from vertex_services import embed_text as _vt_embed
                return await asyncio.wait_for(
                    _vt_embed(text, task_type=task_type),
                    timeout=20.0,
                )
            except Exception as exc:
                logger.warning("workers_ai_custom embed failed for '%s': %s", text[:40], exc)
                return None

        _embed_fn = _embed_fn_primary
        _current_embed_model = "workers_ai_custom/gemma-300m+qwen3-0.6b"

        retriever = await self._get_retriever()
        if not retriever.is_configured():
            logger.warning(f"Retriever {retriever.name} not configured — skipping chapter embedding")
            return 0

        db = self._db
        subj = await db.subjects.find_one({"id": subject_id}, {"_id": 0}) or {}
        board_name = subj.get("boardName", "")
        class_name = subj.get("className", "")
        stream_name = subj.get("streamName", "")
        subject_name = subj.get("title") or subj.get("name", "")

        embed_text_input = _build_rich_embed_text(
            board_name, class_name, stream_name, subject_name,
            title, description, topics or [], content,
        )

        try:
            vec = await _embed_fn(embed_text_input, task_type="RETRIEVAL_DOCUMENT")
        except Exception as exc:
            logger.warning(f"Embed chapter failed for {title[:40]}: {exc}")
            vec = None

        # Task #491 — no cross-model fallback. Indexed and query
        # vectors must share the same embedding space; mixing model
        # families silently degrades retrieval quality.
        if not vec:
            return 0

        await self.remove_chapter_embeddings(chapter_id)

        vectors_to_upsert = []

        chapter_vec_id = _make_vector_id(chapter_id, "chapter")
        vectors_to_upsert.append({
            "id": chapter_vec_id,
            "values": vec,
            "metadata": {
                "chapter_id": chapter_id,
                "subject_id": subject_id,
                "board": board_name,
                "class_name": class_name,
                "stream": stream_name,
                "subject_name": subject_name,
                "chapter_title": title,
                "chapter_number": 0,
                "level": "chapter",
                "topic": "",
                "embed_text": embed_text_input[:500],
                "embedding_model": _current_embed_model,
                "source": "content_editor",
            },
        })

        topic_vecs = await self._build_topic_vectors(
            chapter_id, subject_id, board_name, class_name, stream_name,
            subject_name, title, 0, topics or [], _embed_fn,
            _current_embed_model, content=content,
        )
        vectors_to_upsert.extend(topic_vecs)

        # embed-model: @cf/google/embeddinggemma-300m
        result = await retriever.upsert(vectors_to_upsert)
        inserted = result.get("upserted", len(vectors_to_upsert))
        logger.info(f"Embedded chapter '{title[:40]}' + {len(topic_vecs)} topics to Vectorize")
        return inserted

    async def remove_chapter_embeddings(self, chapter_id: str) -> int:
        retriever = await self._get_retriever()
        if not retriever.is_configured():
            return 0

        ids_to_delete = [_make_vector_id(chapter_id, "chapter")]

        if self._db is not None:
            chapter = await self._db.chapters.find_one({"id": chapter_id}, {"_id": 0, "topics": 1})
            if chapter:
                for t in (chapter.get("topics") or []):
                    topic_str = str(t).strip()
                    if topic_str:
                        ids_to_delete.append(_make_vector_id(chapter_id, "topic", topic_str))

        deleted = await retriever.delete(ids_to_delete)
        return deleted

    async def ensure_seeded(self) -> int:
        async with self._seed_lock:
            if self._seeded:
                return 0
            inserted = await self._seed_chapters()
            self._seeded = True
            return inserted

    async def reseed(self) -> int:
        async with self._seed_lock:
            self._seeded = False
            inserted = await self._seed_chapters()
            self._seeded = True
        return inserted

    async def classify(self, query: str, subject_id: Optional[str] = None) -> Optional[SyllabusMatch]:
        retriever = await self._get_retriever()
        if not retriever.is_configured():
            return None

        try:
            from cache import _query_embed_cache
        except ImportError:
            _query_embed_cache = None

        _embed_key = query.strip().lower()
        q_vec = _query_embed_cache.get(_embed_key) if _query_embed_cache is not None else None

        if q_vec is None:
            # Task #491 — single-source workers_ai_custom for query embedding.
            # Indexed and query vectors MUST share the same embedding
            # space; the previous Pinecone-first branch produced
            # cross-model contamination at retrieval time.
            try:
                from vertex_services import embed_text
                q_vec = await asyncio.wait_for(
                    embed_text(query, task_type="RETRIEVAL_QUERY"),
                    timeout=3.0,
                )
            except Exception as exc:
                logger.warning(f"Embed query failed (workers_ai_custom): {exc}")
                return None

            if not q_vec:
                return None
            if _query_embed_cache is not None:
                _query_embed_cache[_embed_key] = q_vec
        else:
            logger.debug(f"SyllabusEmbed: reusing cached query embedding for '{query[:40]}'")

        mf = {"subject_id": subject_id} if subject_id else None
        matches = await retriever.query(
            vector=q_vec,
            top_k=10,
            metadata_filter=mf,
            return_metadata=True,
        )

        if not matches and subject_id:
            matches = await retriever.query(
                vector=q_vec,
                top_k=10,
                return_metadata=True,
            )

        if not matches:
            return None

        scored = []
        for m in matches:
            meta = m.get("metadata", {})
            score = m.get("score", 0.0)
            if subject_id:
                entry_sid = meta.get("subject_id", "")
                if entry_sid == subject_id:
                    score += SUBJECT_MATCH_BONUS
                elif entry_sid:
                    score -= SUBJECT_MISMATCH_PENALTY
            scored.append((score, meta))

        scored.sort(key=lambda x: -x[0])

        top3 = scored[:3]
        top3_log = " | ".join(
            f"{e.get('chapter_title', '?')}"
            f"{'/' + e.get('topic', '') if e.get('level') == 'topic' else ''}"
            f" ({s:.3f})"
            for s, e in top3
        )
        logger.info(f"SyllabusEmbed top-3: [{top3_log}] | query: {query[:60]}")

        best_score, best_entry = scored[0]

        if best_score >= SIMILARITY_THRESHOLD:
            logger.info(
                f"SyllabusEmbed match: {best_entry.get('subject_name', '')} / "
                f"{best_entry.get('chapter_title', '')} "
                f"(level={best_entry.get('level', 'chapter')}, sim={best_score:.3f}) | query: {query[:50]}"
            )
            return SyllabusMatch(
                board          = best_entry.get("board", "AHSEC"),
                class_name     = best_entry.get("class_name", ""),
                stream         = best_entry.get("stream", ""),
                subject_name   = best_entry.get("subject_name", ""),
                chapter_title  = best_entry.get("chapter_title", ""),
                chapter_number = int(best_entry.get("chapter_number", 0)),
                subject_id     = best_entry.get("subject_id", ""),
                chapter_id     = best_entry.get("chapter_id", ""),
                similarity     = round(best_score, 4),
                level          = best_entry.get("level", "chapter"),
                topic          = best_entry.get("topic", ""),
            )
        return None

    async def classify_top_n(self, query: str, top_n: int = 5) -> list[dict]:
        try:
            from vertex_services import embed_text
        except ImportError:
            return []

        retriever = await self._get_retriever()
        if not retriever.is_configured():
            return []

        try:
            q_vec = await asyncio.wait_for(
                embed_text(query, task_type="RETRIEVAL_QUERY"),
                timeout=3.0,
            )
            if not q_vec:
                return []
        except Exception:
            return []

        matches = await retriever.query(
            vector=q_vec,
            top_k=top_n,
            return_metadata=True,
        )

        results = []
        for m in matches:
            meta = m.get("metadata", {})
            score = m.get("score", 0.0)
            results.append({
                "board": meta.get("board", ""),
                "class_name": meta.get("class_name", ""),
                "stream": meta.get("stream", ""),
                "subject_name": meta.get("subject_name", ""),
                "chapter_title": meta.get("chapter_title", ""),
                "chapter_number": int(meta.get("chapter_number", 0)),
                "subject_id": meta.get("subject_id", ""),
                "chapter_id": meta.get("chapter_id", ""),
                "level": meta.get("level", "chapter"),
                "topic": meta.get("topic", ""),
                "embed_text_preview": (meta.get("embed_text", ""))[:200],
                "similarity": round(score, 4),
                "passes_threshold": score >= SIMILARITY_THRESHOLD,
            })

        return results

    async def full_reseed(self) -> dict:
        retriever = await self._get_retriever()
        if not retriever.is_configured():
            return {"error": f"Retriever {retriever.name} not configured"}

        async with self._seed_lock:
            self._seeded = False

            deleted_total = 0
            if self._db is not None:
                all_ids = []
                async for chapter in self._db.chapters.find({}, {"id": 1, "topics": 1}):
                    ch_id = chapter.get("id") or str(chapter.get("_id", ""))
                    all_ids.append(_make_vector_id(ch_id, "chapter"))
                    for t in (chapter.get("topics") or []):
                        topic_str = str(t).strip()
                        if topic_str:
                            all_ids.append(_make_vector_id(ch_id, "topic", topic_str))
                if all_ids:
                    deleted_total += await retriever.delete(all_ids)
                    logger.info(f"full_reseed: deleted {len(all_ids)} known vectors")

            stale_rounds = 0
            max_stale_rounds = 200
            while stale_rounds < max_stale_rounds:
                zero_vec = [0.0] * retriever.dimensions
                stale = await retriever.query(
                    vector=zero_vec, top_k=100, return_metadata=False, return_values=False,
                )
                if not stale:
                    break
                stale_ids = [m["id"] for m in stale]
                await retriever.delete(stale_ids)
                deleted_total += len(stale_ids)
                stale_rounds += 1
                logger.info(f"full_reseed: swept {len(stale_ids)} stale vectors (round {stale_rounds})")
            if stale_rounds >= max_stale_rounds:
                logger.warning(
                    f"full_reseed: hit sweep limit ({max_stale_rounds} rounds, ~{max_stale_rounds * 100} vectors). "
                    "Some stale vectors may remain — consider recreating the index."
                )

            if deleted_total:
                logger.info(f"full_reseed: total deleted {deleted_total} vectors before re-embedding")

            inserted = await self._seed_chapters()
            self._seeded = True
        return {"status": "ok", "inserted": inserted, "deleted": deleted_total}

    async def stats(self) -> dict:
        retriever = await self._get_retriever()
        if not retriever.is_configured():
            return {"error": f"Retriever {retriever.name} not configured"}

        index_info = await retriever.index_info()
        index_config = await retriever.index_config()

        return {
            "index_name": index_config.get("name", retriever.name),
            "total_vectors": index_info.get("vector_count", 0),
            "dimensions": index_config.get("dimensions", retriever.dimensions),
            "metric": index_config.get("metric", "cosine"),
            "similarity_threshold": SIMILARITY_THRESHOLD,
            "subject_match_bonus": SUBJECT_MATCH_BONUS,
            "subject_mismatch_penalty": SUBJECT_MISMATCH_PENALTY,
            "backend": retriever.name,
        }

    async def _seed_chapters(self) -> int:
        try:
            from config import SEED_DATA
            from vertex_services import embed_text
        except ImportError as exc:
            logger.warning(f"Cannot seed — import error: {exc}")
            return 0

        retriever = await self._get_retriever()
        if not retriever.is_configured():
            logger.warning(f"Retriever {retriever.name} not configured — skipping seed")
            return 0

        boards   = {b["id"]: b for b in SEED_DATA.get("boards", [])}
        classes_ = {c["id"]: c for c in SEED_DATA.get("classes", [])}
        streams  = {s["id"]: s for s in SEED_DATA.get("streams", [])}
        subjects = {s["id"]: s for s in SEED_DATA.get("subjects", [])}

        try:
            from vertex_services import _EMBED_MODEL as _current_embed_model
        except ImportError:
            _current_embed_model = "unknown"

        all_candidate_ids: list[str] = []
        chapter_entries: list[dict] = []

        for chapter in SEED_DATA.get("chapters", []):
            ch_id = chapter["id"]
            subj_id = chapter["subject_id"]
            subj = subjects.get(subj_id, {})
            stream = streams.get(subj.get("stream_id", ""), {})
            cls = classes_.get(stream.get("class_id", ""), {})
            board = boards.get(cls.get("board_id", ""), {})

            entry = {
                "ch_id": ch_id, "subj_id": subj_id,
                "board_name": board.get("name", "AHSEC"),
                "class_name": cls.get("name", ""),
                "stream_name": stream.get("name", ""),
                "subject_name": subj.get("name", ""),
                "chapter_title": chapter.get("title", ""),
                "description": (chapter.get("description") or "").strip(),
                "topic_list": chapter.get("topics") or [],
                "chapter_number": chapter.get("chapter_number", 0),
                "content": (chapter.get("content") or ""),
                "source": "seed",
            }
            chapter_entries.append(entry)
            all_candidate_ids.append(_make_vector_id(ch_id, "chapter"))
            for t in entry["topic_list"]:
                ts = str(t).strip()
                if ts:
                    all_candidate_ids.append(_make_vector_id(ch_id, "topic", ts))

        try:
            db = self._db
            if db is not None:
                mongo_subjects: dict = {}
                async for s in db.subjects.find({}, {
                    "id": 1, "title": 1, "name": 1,
                    "boardName": 1, "className": 1, "streamName": 1,
                }):
                    sid = s.get("id") or str(s.get("_id", ""))
                    mongo_subjects[sid] = s

                async for chapter in db.chapters.find({}):
                    ch_id = chapter.get("id") or str(chapter.get("_id", ""))
                    subj_id = chapter.get("subject_id", "")
                    subj = mongo_subjects.get(subj_id, {})
                    topic_list = chapter.get("topics") or []

                    entry = {
                        "ch_id": ch_id, "subj_id": subj_id,
                        "board_name": subj.get("boardName", ""),
                        "class_name": subj.get("className", ""),
                        "stream_name": subj.get("streamName", ""),
                        "subject_name": subj.get("title") or subj.get("name", ""),
                        "chapter_title": chapter.get("title", ""),
                        "description": (chapter.get("description") or "").strip(),
                        "topic_list": topic_list,
                        "chapter_number": chapter.get("chapter_number", 0),
                        "content": (chapter.get("content") or "").strip(),
                        "source": "mongo",
                    }
                    chapter_entries.append(entry)
                    all_candidate_ids.append(_make_vector_id(ch_id, "chapter"))
                    for t in topic_list:
                        ts = str(t).strip()
                        if ts:
                            all_candidate_ids.append(_make_vector_id(ch_id, "topic", ts))
        except Exception as mongo_err:
            logger.warning(f"SyllabusEmbedder: MongoDB chapter loading failed: {mongo_err}")

        existing_ids: set[str] = set()
        for i in range(0, len(all_candidate_ids), 100):
            batch_ids = all_candidate_ids[i : i + 100]
            try:
                found = await retriever.get_by_ids(batch_ids)
                for v in found:
                    vid = v.get("id") if isinstance(v, dict) else getattr(v, "id", None)
                    if vid:
                        existing_ids.add(vid)
            except Exception:
                pass

        skipped = 0
        inserted = 0
        vectors_batch: list[dict] = []

        for entry in chapter_entries:
            ch_id = entry["ch_id"]
            ch_vid = _make_vector_id(ch_id, "chapter")

            if ch_vid not in existing_ids:
                embed_text_input = _build_rich_embed_text(
                    entry["board_name"], entry["class_name"], entry["stream_name"],
                    entry["subject_name"], entry["chapter_title"],
                    entry["description"], entry["topic_list"],
                    entry.get("content", ""),
                )
                try:
                    vec = await asyncio.wait_for(
                        embed_text(embed_text_input, task_type="RETRIEVAL_DOCUMENT"),
                        timeout=20.0,  # Task #545: was 5.0; bumped so embed retry-with-backoff can land
                    )
                except Exception as exc:
                    logger.warning(f"Embed failed for {entry['chapter_title'][:40]}: {exc}")
                    vec = None

                if vec:
                    vectors_batch.append({
                        "id": ch_vid,
                        "values": vec,
                        "metadata": {
                            "chapter_id": ch_id,
                            "subject_id": entry["subj_id"],
                            "board": entry["board_name"],
                            "class_name": entry["class_name"],
                            "stream": entry["stream_name"],
                            "subject_name": entry["subject_name"],
                            "chapter_title": entry["chapter_title"],
                            "chapter_number": entry["chapter_number"],
                            "level": "chapter",
                            "topic": "",
                            "embed_text": embed_text_input[:500],
                            "embedding_model": _current_embed_model,
                            "source": entry["source"],
                        },
                    })
            else:
                skipped += 1

            missing_topics = []
            for t in entry["topic_list"]:
                ts = str(t).strip()
                if ts:
                    t_vid = _make_vector_id(ch_id, "topic", ts)
                    if t_vid not in existing_ids:
                        missing_topics.append(ts)
                    else:
                        skipped += 1

            if missing_topics:
                topic_vecs = await self._build_topic_vectors(
                    ch_id, entry["subj_id"], entry["board_name"],
                    entry["class_name"], entry["stream_name"],
                    entry["subject_name"], entry["chapter_title"],
                    entry["chapter_number"], missing_topics,
                    embed_text, _current_embed_model,
                    content=entry.get("content", ""),
                )
                vectors_batch.extend(topic_vecs)

            # embed-model: @cf/google/embeddinggemma-300m
            if len(vectors_batch) >= 20:
                result = await retriever.upsert(vectors_batch)
                inserted += result.get("upserted", len(vectors_batch))
                vectors_batch = []
                logger.info(f"SyllabusEmbedder: {inserted} new embeddings, {skipped} skipped…")

        if vectors_batch:
            result = await retriever.upsert(vectors_batch)
            inserted += result.get("upserted", len(vectors_batch))

        logger.info(
            f"SyllabusEmbedder: seeding complete — {inserted} new embeddings, "
            f"{skipped} already existed (skipped)"
        )
        return inserted

    async def reseed_all(self, force: bool = False) -> dict:
        if force:
            return await self.full_reseed()

        try:
            from vertex_services import embed_text as _embed_fn
            from vertex_services import _EMBED_MODEL as _current_embed_model
        except ImportError as exc:
            return {"status": "error", "reason": f"import error: {exc}"}

        retriever = await self._get_retriever()
        if not retriever.is_configured():
            return {"status": "error", "reason": f"Retriever {retriever.name} not configured"}

        async with self._seed_lock:
            db = self._db
            mongo_subjects: dict = {}
            if db is not None:
                async for s in db.subjects.find({}, {
                    "id": 1, "title": 1, "name": 1,
                    "boardName": 1, "className": 1, "streamName": 1,
                }):
                    sid = s.get("id") or str(s.get("_id", ""))
                    mongo_subjects[sid] = s

            stats = {"chapters_processed": 0, "topics_processed": 0, "embed_failures": 0, "skipped": 0}
            vectors_batch: list[dict] = []

            if db is not None:
                all_candidate_ids: list[str] = []
                chapter_list: list[dict] = []
                async for chapter in db.chapters.find({}):
                    ch_id = chapter.get("id") or str(chapter.get("_id", ""))
                    topic_list = chapter.get("topics") or []
                    chapter_list.append(chapter)
                    all_candidate_ids.append(_make_vector_id(ch_id, "chapter"))
                    for t in topic_list:
                        ts = str(t).strip()
                        if ts:
                            all_candidate_ids.append(_make_vector_id(ch_id, "topic", ts))

                existing_ids: set[str] = set()
                for i in range(0, len(all_candidate_ids), 100):
                    batch_ids = all_candidate_ids[i : i + 100]
                    try:
                        found = await retriever.get_by_ids(batch_ids)
                        for v in found:
                            vid = v.get("id") if isinstance(v, dict) else getattr(v, "id", None)
                            if vid:
                                existing_ids.add(vid)
                    except Exception:
                        pass

                for chapter in chapter_list:
                    ch_id = chapter.get("id") or str(chapter.get("_id", ""))
                    subj_id = chapter.get("subject_id", "")
                    subj = mongo_subjects.get(subj_id, {})

                    board_name = subj.get("boardName", "")
                    class_name = subj.get("className", "")
                    stream_name = subj.get("streamName", "")
                    subject_name = subj.get("title") or subj.get("name", "")
                    chapter_title = chapter.get("title", "")
                    description = (chapter.get("description") or "").strip()
                    topic_list: list = chapter.get("topics") or []
                    content = (chapter.get("content") or "").strip()

                    ch_vid = _make_vector_id(ch_id, "chapter")
                    if ch_vid not in existing_ids:
                        embed_text_input = _build_rich_embed_text(
                            board_name, class_name, stream_name, subject_name,
                            chapter_title, description, topic_list, content,
                        )
                        try:
                            vec = await asyncio.wait_for(
                                _embed_fn(embed_text_input, task_type="RETRIEVAL_DOCUMENT"),
                                timeout=8.0,
                            )
                        except Exception as exc:
                            logger.warning(f"reseed embed failed for {chapter_title[:40]}: {exc}")
                            vec = None
                            stats["embed_failures"] += 1

                        if vec:
                            vectors_batch.append({
                                "id": ch_vid,
                                "values": vec,
                                "metadata": {
                                    "chapter_id": ch_id,
                                    "subject_id": subj_id,
                                    "board": board_name,
                                    "class_name": class_name,
                                    "stream": stream_name,
                                    "subject_name": subject_name,
                                    "chapter_title": chapter_title,
                                    "chapter_number": chapter.get("chapter_number", 0),
                                    "level": "chapter",
                                    "topic": "",
                                    "embed_text": embed_text_input[:500],
                                    "embedding_model": _current_embed_model,
                                    "source": "reseed",
                                },
                            })
                            stats["chapters_processed"] += 1
                    else:
                        stats["skipped"] += 1

                    missing_topics = []
                    for t in topic_list:
                        ts = str(t).strip()
                        if ts:
                            t_vid = _make_vector_id(ch_id, "topic", ts)
                            if t_vid not in existing_ids:
                                missing_topics.append(ts)
                            else:
                                stats["skipped"] += 1

                    if missing_topics:
                        topic_vecs = await self._build_topic_vectors(
                            ch_id, subj_id, board_name, class_name, stream_name,
                            subject_name, chapter_title, chapter.get("chapter_number", 0),
                            missing_topics, _embed_fn, _current_embed_model,
                            content=content,
                        )
                        vectors_batch.extend(topic_vecs)
                        stats["topics_processed"] += len(topic_vecs)

                    # embed-model: @cf/google/embeddinggemma-300m
                    if len(vectors_batch) >= 20:
                        await retriever.upsert(vectors_batch)
                        vectors_batch = []

                    await asyncio.sleep(0.1)

            if vectors_batch:
                await retriever.upsert(vectors_batch)

            stats["status"] = "ok"
            logger.info(f"reseed_all complete: {stats}")
            return stats

    async def _build_topic_vectors(
        self,
        chapter_id: str,
        subject_id: str,
        board_name: str,
        class_name: str,
        stream_name: str,
        subject_name: str,
        chapter_title: str,
        chapter_number: int,
        topics: list,
        embed_text_fn,
        embed_model: str,
        content: str = "",
    ) -> list[dict]:
        if not topics:
            return []

        vectors = []
        for topic in topics:
            topic_str = str(topic).strip()
            if not topic_str:
                continue

            topic_embed_text = _build_topic_embed_text(
                board_name, class_name, stream_name,
                subject_name, chapter_title, topic_str, content,
            )

            try:
                vec = await asyncio.wait_for(
                    embed_text_fn(topic_embed_text, task_type="RETRIEVAL_DOCUMENT"),
                    timeout=20.0,  # Task #545: was 5.0; bumped so embed retry-with-backoff can land
                )
            except Exception as exc:
                logger.warning(f"Topic embed failed for {topic_str[:30]}: {exc}")
                vec = None

            if vec:
                vectors.append({
                    "id": _make_vector_id(chapter_id, "topic", topic_str),
                    "values": vec,
                    "metadata": {
                        "chapter_id": chapter_id,
                        "subject_id": subject_id,
                        "board": board_name,
                        "class_name": class_name,
                        "stream": stream_name,
                        "subject_name": subject_name,
                        "chapter_title": chapter_title,
                        "chapter_number": chapter_number,
                        "level": "topic",
                        "topic": topic_str,
                        "embed_text": topic_embed_text[:500],
                        "embedding_model": embed_model,
                    },
                })

        return vectors
