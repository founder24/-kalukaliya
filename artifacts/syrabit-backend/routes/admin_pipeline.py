"""Syrabit.ai — Admin pipeline: generate notes, MCQs, flashcards"""
import re, json, asyncio, time, uuid, logging, os
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import (
    APIRouter, HTTPException, Depends, Query, Body, File, UploadFile,
)

from deps import (
    db,
    is_mongo_available,
    mark_mongo_down,
    sarvam_client,
    sarvam_translate_client,
    supa,
)
from cache import _invalidate_content_cache
from auth_deps import (
    get_admin_user,
)
from llm import (
    call_llm_api_content,
    call_llm_api_content_with_retry,
    call_translate_with_dispatch,
    call_embed_with_dispatch,
    polish_notes_with_vertex,
)
from rag import (
    auto_chunk_content,
    backfill_chunk_embeddings,
    rechunk_chapter,
)
from seed import ensure_seeded
from seo_engine import _normalize_headings, _format_content_html

logger = logging.getLogger(__name__)

router = APIRouter()


async def _schedule_indexnow_for_chapter_ids(chapter_ids: List[str], source: str = "admin_bulk"):
    """Resolve chapter IDs to public URLs and queue them with the IndexNow
    batcher. Used by bulk import / regeneration endpoints so that newly
    generated or refreshed pages get notified to search engines on
    completion of the batch (instead of relying on per-row hooks that
    don't fire for bulk paths)."""
    if not chapter_ids:
        return 0
    try:
        from routes.bot_discovery import indexnow_batcher
        chapters = await db.chapters.find(
            {"id": {"$in": chapter_ids}},
            {"_id": 0, "id": 1, "slug": 1, "subject_id": 1},
        ).to_list(len(chapter_ids))
        subject_ids = list({ch.get("subject_id", "") for ch in chapters if ch.get("subject_id")})
        subjects = {}
        if subject_ids:
            subj_docs = await db.subjects.find(
                {"id": {"$in": subject_ids}},
                {"_id": 0, "id": 1, "board_slug": 1, "class_slug": 1, "slug": 1},
            ).to_list(len(subject_ids))
            subjects = {s["id"]: s for s in subj_docs}
        paths = []
        for ch in chapters:
            sid = ch.get("subject_id", "")
            ch_slug = ch.get("slug", "")
            subj = subjects.get(sid, {})
            bs = subj.get("board_slug", "")
            cs = subj.get("class_slug", "")
            ss = subj.get("slug", "")
            if bs and cs and ss and ch_slug:
                paths.append(f"/{bs}/{cs}/{ss}/{ch_slug}")
        if not paths:
            return 0
        await indexnow_batcher.queue_raw_paths(paths)
        await indexnow_batcher.flush_force(source=source)
        return len(paths)
    except Exception as e:
        logger.debug(f"Bulk IndexNow notify failed ({source}): {e}")
        return 0


def _extract_content_topics(content: str) -> list[str]:
    topics = []
    skip = {"summary", "introduction", "conclusion", "key points", "key facts"}
    for line in content.split("\n"):
        m = re.match(r'^#{2,3}\s+(.+)', line.strip())
        if m:
            heading = m.group(1).strip().rstrip(".")
            if len(heading) > 3 and heading.lower() not in skip:
                topics.append(heading)
    return list(dict.fromkeys(topics))


def _trim_long_intro(content: str, max_intro_lines: int = 4) -> str:
    lines = content.split("\n")
    first_heading_idx = None
    for i, line in enumerate(lines):
        if re.match(r'^#{2,3}\s+', line.strip()):
            first_heading_idx = i
            break
    if first_heading_idx is None or first_heading_idx <= max_intro_lines:
        return content
    return "\n".join(lines[first_heading_idx:]).strip()


async def _polish_notes_with_sarvam(raw_notes: str, title: str, subject_name: str) -> str:
    """Stage-2 NotebookLM-style polish — pinned to Vertex / Gemini.

    Despite the legacy ``_with_sarvam`` name (kept so existing callers don't
    break), this helper now routes through ``llm.polish_notes_with_vertex``
    per the 2026-05-05 user instruction:

      * Stage 1 (GENERATE): Workers AI variants produce the raw notes.
      * Stage 2 (POLISH):   Vertex / Gemini 2.5 Flash re-formats the raw
                            notes into NotebookLM-style study notes.

    The previous Sarvam pin was broken — ``call_llm_api_content`` ignores
    the ``model=`` parameter and routes via the ``content`` POOL_WEIGHTS
    pool, which no longer contains Sarvam at all.
    """
    try:
        async with _pipeline_sem:
            return await polish_notes_with_vertex(
                raw_notes,
                title=title,
                subject_name=subject_name,
                lang="en",
                max_tokens=4000,
            )
    except Exception as e:
        logger.warning(f"[POLISH] Vertex polish failed for '{title}': {e} — keeping raw notes")
        return raw_notes


def _validate_mcq_output(result: str, expected_count: int = 5) -> bool:
    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        items = data if isinstance(data, list) else data.get("mcqs", data.get("questions", []))
        return isinstance(items, list) and len(items) >= max(1, expected_count // 2)
    except Exception:
        return False


async def _pipeline_generate_mcqs(
    content: str, subject_name: str, chapter_title: str, class_name: str, count: int = 20,
) -> list:
    if not content or len(content.strip()) < 100:
        return []
    prompt = (
        f"You are an expert examiner for AHSEC/SEBA/Degree students.\n"
        f"Generate exactly {count} MCQ questions for:\n"
        f"Subject: {subject_name} ({class_name})\nChapter: {chapter_title}\n\n"
        f"Each MCQ must have exactly 4 options (A, B, C, D), one correct answer, and a brief explanation.\n"
        f"Mix difficulties: 30% easy, 40% medium, 30% hard.\n"
        f"Questions must use exam-style language matching AHSEC/SEBA/Degree paper patterns.\n"
        f"Return ONLY valid JSON (no markdown fences):\n"
        f'[{{"id": 1, "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, '
        f'"correct_answer": "A", "explanation": "...", "difficulty": "medium", "topic": "...", "marks": 1}}]\n\n'
        f"Chapter content:\n{content[:4500]}"
    )
    try:
        async with _pipeline_sem:
            result = await call_llm_api_content_with_retry(
                [{"role": "user", "content": prompt}],
                max_tokens=3000,
                validate_fn=lambda r: _validate_mcq_output(r, count),
            )
        cleaned = result.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        return data.get("mcqs", data.get("questions", []))
    except Exception:
        return []


def _validate_flashcard_output(result: str) -> bool:
    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        cards = data.get("flashcards", []) if isinstance(data, dict) else data
        return isinstance(cards, list) and len(cards) >= 1
    except Exception:
        return False


async def _pipeline_generate_flashcards(
    content: str, subject_name: str, chapter_title: str, class_name: str, count: int = 15,
    topics: list = None,
) -> list:
    if not content or len(content.strip()) < 100:
        return []
    topic_instruction = ""
    if topics:
        topic_list = ", ".join(str(t) for t in topics[:15])
        topic_instruction = f"\nFlashcards MUST collectively cover ALL of these syllabus topics: {topic_list}\nEnsure at least one flashcard per topic.\n"
    prompt = (
        f"You are an expert memory coach for AHSEC/SEBA/Degree students.\n"
        f"Generate exactly {count} HIGH-IMPACT memory-trick flashcards for:\n"
        f"Subject: {subject_name} ({class_name})\nChapter: {chapter_title}\n"
        f"{topic_instruction}\n"
        f"Card types (distribute evenly): mnemonic, mindmap, shortcut, memory_hack, key_fact\n"
        f"Each card should use exam-relevant terms matching AHSEC/SEBA/Degree paper patterns.\n\n"
        f"Return ONLY valid JSON (no markdown fences):\n"
        f'{{"flashcards": [{{"id": 1, "front": "...", "back": "...", "type": "mnemonic", '
        f'"difficulty": "easy", "tags": ["..."]}}]}}\n\n'
        f"Chapter content:\n{content[:4500]}"
    )
    try:
        async with _pipeline_sem:
            result = await call_llm_api_content_with_retry(
                [{"role": "user", "content": prompt}],
                max_tokens=3000,
                validate_fn=_validate_flashcard_output,
            )
        cleaned = result.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = parts[1] if len(parts) > 1 else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned)
        return data.get("flashcards", [])
    except Exception:
        return []


async def _generate_chapter_all(chapter_id: str, generate: list[str]) -> dict:
    chapter = await db.chapters.find_one({"id": chapter_id}, {"_id": 0})
    if not chapter:
        return {"chapter_id": chapter_id, "status": "error", "reason": "not found"}
    title = (chapter.get("title") or "").strip()
    if not title:
        return {"chapter_id": chapter_id, "status": "skipped", "reason": "no title"}

    subject = await db.subjects.find_one({"id": chapter.get("subject_id", "")}, {"_id": 0}) or {}
    subject_name = subject.get("name", "")
    board_ctx, class_ctx, subject_desc = await _resolve_board_context(subject)
    topics = chapter.get("topics") or []
    content = (chapter.get("content") or "").strip()
    content_sufficient = content and len(content) >= 100
    result: dict = {"chapter_id": chapter_id, "title": title}
    needs_mcqs = "mcqs" in generate
    needs_flashcards = "flashcards" in generate
    needs_notes = "notes" in generate
    needs_sequential = needs_notes and not content_sufficient and (needs_mcqs or needs_flashcards)

    if needs_notes:
        topic_block = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(topics)) if topics else f"  {title}"
        desc_block = ""
        ch_desc = (chapter.get("description") or "").strip()
        if ch_desc:
            desc_block += f"**Chapter Description:** {ch_desc}\n"
        if subject_desc:
            desc_block += f"**Subject Description:** {subject_desc}\n"

        seo_topic_docs = await db.seo_topics.find(
            {"linked_chapter_id": chapter_id},
            {"_id": 0, "topic": 1, "primary_keyword": 1}
        ).to_list(30)
        seo_keywords = list(dict.fromkeys(
            (d.get("primary_keyword") or d.get("topic") or "").strip()
            for d in seo_topic_docs
            if (d.get("primary_keyword") or d.get("topic") or "").strip()
        ))
        seo_seed_block = ""
        if seo_keywords:
            seo_seed_block = (
                "\n\n**SEO Keyword Seeds (naturally weave these phrases into headings and body):**\n"
                + "\n".join(f"  - {kw}" for kw in seo_keywords[:15])
            )

        notes_prompt = f"""You are an expert academic content writer for {board_ctx} {class_ctx} students.

Generate **topic-wise study notes** for the chapter below.

**Chapter:** {title}
**Subject:** {subject_name or "Degree Course"}
{desc_block}

**Syllabus Topics to cover (MANDATORY — every topic MUST get its own section):**
{topic_block}{seo_seed_block}

---

**INSTRUCTIONS:**
1. For EACH topic listed above, write:
   - A ## Heading matching the topic name exactly
   - A **bold definition** (1-2 sentences) as the opening line
   - A focused explanation in 5-8 sentences using simple, precise academic language
   - 4-6 bullet points of key facts: definitions in **bold**, significance, relationships
2. If SEO keyword seeds are provided, naturally incorporate them in headings and body text.
3. Use markdown (##, ###, **, -, etc.). NO disclaimers, NO preamble.
4. NO introduction section. NO summary section. NO exam tips. NO extra examples. NO cross-references between topics. Start directly with the first ## topic heading.
5. Maximum 3-4 lines before the first ## heading. Do NOT write a long introduction paragraph. The very first line of output should ideally be a ## heading.
6. Each topic section must be self-contained — one concept, one definition, one explanation, key facts. Nothing else."""

        def _validate_notes(text: str) -> bool:
            return bool(text) and len(text.split()) >= 300

        try:
            async with _pipeline_sem:
                notes_raw = await call_llm_api_content_with_retry(
                    [{"role": "user", "content": notes_prompt}],
                    max_tokens=6000,
                    validate_fn=_validate_notes,
                )
        except Exception as e:
            notes_raw = None
            result["notes"] = {"status": "error", "reason": str(e)}

        if notes_raw and len(notes_raw.split()) >= 200:
            notes_text = _normalize_headings(notes_raw).strip()
            notes_text = _trim_long_intro(notes_text)
            notes_text = await _polish_notes_with_sarvam(notes_text, title, subject_name or "")
            notes_text = _normalize_headings(notes_text).strip()
            notes_text = _trim_long_intro(notes_text)
            wc = len(notes_text.split())

            new_topics = _extract_content_topics(notes_text)
            if new_topics:
                existing = [str(t).strip() if isinstance(t, str) else (t.get("title", "") if isinstance(t, dict) else str(t)) for t in (topics or [])]
                merged = list(dict.fromkeys(existing + new_topics))
                topics = merged

            await db.chapters.update_one(
                {"id": chapter_id},
                {"$set": {
                    "content": notes_text,
                    "content_type": "notes",
                    "notes_generated": True,
                    "notes_generated_at": datetime.now(timezone.utc).isoformat(),
                    "topics": topics,
                }}
            )
            _invalidate_content_cache("chapters")

            try:
                for kw in new_topics[:25]:
                    # Task #349: route through the shared helper so
                    # created_at is stamped exactly once via $setOnInsert.
                    from seo_writes import upsert_seo_topic
                    await upsert_seo_topic(
                        db,
                        {"linked_chapter_id": chapter_id, "topic": kw},
                        {
                            "linked_chapter_id": chapter_id,
                            "topic": kw,
                            "primary_keyword": kw,
                            "source": "ai_notes",
                        },
                    )
            except Exception:
                pass

            try:
                await auto_chunk_content(chapter_id=chapter_id, content=notes_text, subject_id=chapter.get("subject_id"), category=chapter.get("category", "notes"), topics=topics)
            except Exception:
                pass

            # Stage-3 (POOL): Vertex / Gemini also writes the quiz pool
            # for the freshly-polished chapter so the "Quiz Me" cache is
            # hot the moment the chapter publishes (no first-click LLM
            # round-trip for the student). Fire-and-forget — never
            # blocks the bulk-generate response, and `pregenerate_chapter_quiz`
            # is best-effort by contract (logs + returns False on any
            # per-language failure, never raises). `prefer_vertex=True`
            # makes Gemini PRIMARY for this slot to match the formatting
            # stage owner; Azure GPT-4.1-mini stays as the fallback.
            #
            # Concurrency: routed through `_polish_stage_quiz_pregen`
            # which acquires `_quiz_pregen_sem` (default cap = 2) so the
            # bulk wave's per-chapter scheduling cannot pile up an
            # unbounded number of parallel Vertex quiz jobs.
            try:
                fresh_chapter = await db.chapters.find_one(
                    {"id": chapter_id}, {"_id": 0}
                )
                if fresh_chapter:
                    asyncio.create_task(
                        _polish_stage_quiz_pregen(fresh_chapter)
                    )
                    result["quiz_pregenerate"] = {"status": "scheduled", "provider": "vertex"}
            except Exception as e:
                logger.warning(
                    f"[POLISH] quiz pre-gen scheduling failed for {chapter_id}: {e}"
                )
                result["quiz_pregenerate"] = {"status": "error", "reason": str(e)}

            result["notes"] = {"status": "ok", "word_count": wc, "topics_extracted": len(new_topics)}
            if needs_sequential:
                content = notes_text
                content_sufficient = True
        elif notes_raw is not None:
            result["notes"] = {"status": "error", "reason": "too short"}

    parallel_tasks: dict = {}
    src = content if content_sufficient else title

    if needs_mcqs:
        parallel_tasks["mcqs"] = _pipeline_generate_mcqs(src, subject_name, title, class_ctx, count=20)
    if needs_flashcards:
        parallel_tasks["flashcards"] = _pipeline_generate_flashcards(src, subject_name, title, class_ctx, count=15, topics=topics)

    if parallel_tasks:
        p_keys = list(parallel_tasks.keys())
        p_outcomes = await asyncio.gather(*parallel_tasks.values(), return_exceptions=True)

        for key, outcome in zip(p_keys, p_outcomes):
            if isinstance(outcome, Exception):
                result[key] = {"status": "error", "reason": str(outcome)}
                continue

            if key == "mcqs":
                mcqs = outcome if isinstance(outcome, list) else []
                if mcqs:
                    await db.ai_pyq_collections.update_one(
                        {"chapter_id": chapter_id},
                        {"$set": {
                            "chapter_id": chapter_id,
                            "questions": mcqs,
                            "total": len(mcqs),
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                        }},
                        upsert=True,
                    )
                    result["mcqs"] = {"status": "ok", "count": len(mcqs)}
                else:
                    result["mcqs"] = {"status": "error", "reason": "no MCQs generated"}

            elif key == "flashcards":
                cards = outcome if isinstance(outcome, list) else []
                if cards:
                    await db.flashcard_collections.update_one(
                        {"chapter_id": chapter_id},
                        {"$set": {
                            "chapter_id": chapter_id,
                            "flashcards": cards,
                            "total": len(cards),
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                        }},
                        upsert=True,
                    )
                    result["flashcards"] = {"status": "ok", "count": len(cards)}
                else:
                    result["flashcards"] = {"status": "error", "reason": "no flashcards generated"}

    all_keys = [k for k in ("notes", "mcqs", "flashcards") if k in generate]
    if not all_keys:
        result["status"] = "skipped"
        return result
    has_ok = any(
        isinstance(result.get(k), dict) and result[k].get("status") == "ok"
        for k in all_keys
    )
    result["status"] = "ok" if has_ok else "error"
    return result


@router.post("/admin/content/chapters/{chapter_id}/generate-all")
async def admin_generate_chapter_all(
    chapter_id: str,
    generate: str = Query(default="notes,mcqs,flashcards", description="Comma-separated: notes,mcqs,flashcards"),
    admin: dict = Depends(get_admin_user),
):
    gen_list = [g.strip() for g in generate.split(",") if g.strip() in ("notes", "mcqs", "flashcards")]
    if not gen_list:
        raise HTTPException(status_code=400, detail="Specify at least one of: notes, mcqs, flashcards")
    t0 = time.time()
    result = await _generate_chapter_all(chapter_id, gen_list)
    result["elapsed_seconds"] = round(time.time() - t0, 1)
    return result


@router.post("/admin/content/subject/{subject_id}/generate-all")
async def admin_generate_subject_all(
    subject_id: str,
    generate: str = Query(default="notes,mcqs,flashcards", description="Comma-separated: notes,mcqs,flashcards"),
    skip_existing_notes: bool = Query(default=True, description="Skip chapters that already have notes"),
    admin: dict = Depends(get_admin_user),
):
    gen_list = [g.strip() for g in generate.split(",") if g.strip() in ("notes", "mcqs", "flashcards")]
    if not gen_list:
        raise HTTPException(status_code=400, detail="Specify at least one of: notes, mcqs, flashcards")

    chapters = await db.chapters.find(
        {"subject_id": subject_id},
        {"_id": 0, "id": 1, "title": 1, "content": 1, "notes_generated": 1}
    ).to_list(200)
    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters found for this subject")

    chapter_ids = []
    for ch in chapters:
        if skip_existing_notes and "notes" in gen_list:
            has_notes = ch.get("notes_generated") or len((ch.get("content") or "").split()) >= 300
            if has_notes:
                per_ch_gen = [g for g in gen_list if g != "notes"]
                if not per_ch_gen:
                    continue
            else:
                per_ch_gen = gen_list
        else:
            per_ch_gen = gen_list
        chapter_ids.append((ch["id"], per_ch_gen))

    t0 = time.time()
    results = []
    total_waves = (len(chapter_ids) + _WAVE_SIZE - 1) // _WAVE_SIZE

    for wave_idx in range(total_waves):
        wave_start = wave_idx * _WAVE_SIZE
        wave_end = min(wave_start + _WAVE_SIZE, len(chapter_ids))
        wave_items = chapter_ids[wave_start:wave_end]
        wave_t0 = time.time()

        logger.info(
            f"[WAVE {wave_idx + 1}/{total_waves}] Processing chapters "
            f"{wave_start + 1}–{wave_end} of {len(chapter_ids)} "
            f"({len(wave_items)} in this wave)"
        )

        wave_tasks = [_generate_chapter_all(cid, gl) for cid, gl in wave_items]
        wave_results = await asyncio.gather(*wave_tasks, return_exceptions=True)

        wave_ok = 0
        for i, r in enumerate(wave_results):
            if isinstance(r, Exception):
                cid = wave_items[i][0] if i < len(wave_items) else "unknown"
                results.append({"chapter_id": cid, "status": "error", "reason": str(r)})
            else:
                results.append(r)
                if isinstance(r, dict) and r.get("status") == "ok":
                    wave_ok += 1

        wave_elapsed = round(time.time() - wave_t0, 1)
        logger.info(
            f"[WAVE {wave_idx + 1}/{total_waves}] Completed: "
            f"{wave_ok}/{len(wave_items)} succeeded in {wave_elapsed}s"
        )

    elapsed = round(time.time() - t0, 1)

    ok_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "ok")
    return {
        "subject_id": subject_id,
        "total_chapters": len(chapters),
        "processed": len(results),
        "succeeded": ok_count,
        "elapsed_seconds": elapsed,
        "total_waves": total_waves,
        "wave_size": _WAVE_SIZE,
        "concurrency": _PIPELINE_CONCURRENCY,
        "results": results,
    }

@router.post("/admin/content/chapters/{chapter_id}/generate-notes")
async def admin_generate_chapter_notes(chapter_id: str, admin: dict = Depends(get_admin_user)):
    """
    Use AI to generate topic-wise summary notes for a chapter.
    Reads: title, description, topics from the chapter + subject context.
    Writes rich markdown notes back to chapter.content and re-chunks.
    """
    chapter = await db.chapters.find_one({"id": chapter_id}, {"_id": 0})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    subject = await db.subjects.find_one({"id": chapter.get("subject_id", "")}, {"_id": 0}) or {}
    subject_name = subject.get("name", "")
    paper_type   = subject.get("paper_type", "")
    class_name   = subject.get("className", "")
    subject_desc = (subject.get("description") or "").strip()

    # Resolve board context from stream → class → board hierarchy
    board_label = ""
    stream = None
    if subject.get("stream_id"):
        stream = await db.streams.find_one({"id": subject["stream_id"]}, {"_id": 0})
    if stream and stream.get("class_id"):
        cls = await db.classes.find_one({"id": stream["class_id"]}, {"_id": 0})
        if cls:
            if not class_name:
                class_name = cls.get("name", "")
            if cls.get("board_id"):
                board_doc = await db.boards.find_one({"id": cls["board_id"]}, {"_id": 0})
                if board_doc:
                    board_label = board_doc.get("name", "")

    title       = chapter.get("title", "").strip()
    description = (chapter.get("description") or "").strip()
    topics      = chapter.get("topics") or []

    if not title:
        raise HTTPException(status_code=400, detail="Chapter has no title")
    if not description and not topics:
        raise HTTPException(
            status_code=422,
            detail="Add a description (or syllabus topics) to this chapter before generating notes."
        )

    # ── Fetch SEO topics for this chapter as keyword seeds ───────────────────
    seo_topic_docs = await db.seo_topics.find(
        {"linked_chapter_id": chapter_id},
        {"_id": 0, "topic": 1, "primary_keyword": 1}
    ).to_list(30)
    seo_keywords = list(dict.fromkeys(
        (d.get("primary_keyword") or d.get("topic") or "").strip()
        for d in seo_topic_docs
        if (d.get("primary_keyword") or d.get("topic") or "").strip()
    ))

    topic_block = ""
    if topics:
        topic_names = [t.get("name", str(t)) if isinstance(t, dict) else str(t) for t in topics]
        topic_block = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(topic_names))
    else:
        topic_block = f"  {description}"

    seo_seed_block = ""
    if seo_keywords:
        seo_seed_block = (
            "\n\n**SEO Keyword Seeds (weave naturally into headings and body):**\n"
            + "\n".join(f"  - {kw}" for kw in seo_keywords[:15])
        )

    board_ctx = board_label or "Degree"
    class_ctx = class_name or "FYUGP"
    subject_ctx = subject_name or "Degree Course"
    paper_ctx = (paper_type or "").upper()
    num_topics = len(topics) if topics else 1
    word_target_min = max(2500, num_topics * 250)
    word_target_max = max(4000, num_topics * 400)

    desc_block = ""
    if description:
        desc_block += f"**Chapter Description:** {description}\n"
    if subject_desc:
        desc_block += f"**Subject Description:** {subject_desc}\n"
    if not desc_block:
        desc_block = "**Description:** (No additional description provided.)\n"

    prompt = f"""You are an exam-focused revision coach writing **last-minute revision notes** for {board_ctx} {class_ctx} students in Assam, India preparing for the {paper_ctx} paper.

Goal: cover the **maximum number of high-yield exam concepts** in the **minimum readable format** so a student revising the night before can score full marks.

**Chapter:** {title}
**Subject:** {subject_ctx} ({paper_ctx} — {class_ctx})
{desc_block}

**Syllabus Topics (MANDATORY — every single topic MUST appear as its own ## section, in the listed order, using the EXACT topic name):**
{topic_block}{seo_seed_block}

---

## OUTPUT FORMAT — follow EXACTLY for every topic:

```
## <Exact Topic Name>

**Definition:** <one crisp 1-2 line definition with the term in bold. No filler.>

### Core Concept
<3-5 short sentences max. Cover what it is, why it matters, and how it works. Mention any specific name, year, formula, or classification a student MUST write to score the mark.>

### Must-Know Points
- **<Keyword/Term>** — <one-line fact, definition, distinction, formula, date, or example>
- **<Keyword/Term>** — <one-line fact>
- **<Keyword/Term>** — <one-line fact>
- **<Keyword/Term>** — <one-line fact>
- **<Keyword/Term>** — <one-line fact>
- **<Keyword/Term>** — <one-line fact>
(6-8 bullets per topic. Every bullet must be EXAM-WORTHY — something a student would lose marks for not knowing.)

### Exam Angle
- **1-mark:** <typical short-answer / fill-in-the-blank prompt this topic generates>
- **3-mark:** <typical short-note / "explain briefly" prompt>
- **5-mark / long:** <typical long-answer prompt or comparison/diagram this topic generates>
```

---

## CONTENT RULES — non-negotiable:

1. **Exam-first, not textbook.** Every sentence must earn its place by helping the student score marks. Cut filler, hedging, repetition.
2. **Be specific.** Name the people, years, places, frameworks, types, formulas, classifications. "Various scholars believed…" is BANNED. Write "**Henri Fayol (1916)** proposed 14 principles…" instead.
3. **Use compact lists, not paragraphs**, wherever a list works. Students scan, they don't read.
4. **Bold every keyword**, technical term, person name, year, framework, and exam term — they double as flashcard pivots.
5. **Cover ALL the listed topics**, in order, with no missing topic. Each topic must have all four sub-sections (Definition, Core Concept, Must-Know Points, Exam Angle).
6. **Use AHSEC/SEBA/Degree exam vocabulary**: "explain", "differentiate", "discuss", "state with example", "with diagram", "merits and demerits", etc.
7. If a topic has standard classifications (types, stages, principles, levels), list them as a numbered or bulleted enumeration — students get marks for naming each one correctly.

## STRUCTURAL RULES — non-negotiable:

- The very first line of output must be `## <First Topic Name>`. Zero introduction, zero preamble, zero "Here are the notes".
- Headings: `##` only for topic names, `###` only for the four fixed sub-headings (Definition is inline bold, not a heading).
- Sub-headings appear in this fixed order: **Core Concept → Must-Know Points → Exam Angle**.
- Use `**bold**` for keywords, `-` for bullets, `1.` `2.` for ordered lists.
- NO summary, NO conclusion, NO "exam tips" section, NO cross-topic references, NO emoji.
- Output must be clean markdown — no HTML, no code fences around the whole output.
"""

    try:
        generated = await call_llm_api_content(
            [{"role": "user", "content": prompt}],
            max_tokens=8000
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {e}")

    if not generated or len(generated.strip()) < 100:
        raise HTTPException(status_code=502, detail="AI returned empty or too-short content")

    notes_text = _normalize_headings(generated).strip()
    notes_text = _trim_long_intro(notes_text)

    await db.chapters.update_one(
        {"id": chapter_id},
        {"$set": {
            "content":      notes_text,
            "content_type": "notes",
            "notes_generated": True,
            "notes_generated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    _invalidate_content_cache("chapters")

    try:
        await auto_chunk_content(chapter_id=chapter_id, content=notes_text, subject_id=chapter.get("subject_id"), category=chapter.get("category", "notes"), topics=topics)
    except Exception:
        pass

    content_as_words = 0
    if sarvam_translate_client or sarvam_client:
        try:
            translated = await _translate_text_sarvam(notes_text, "en-IN", "as-IN")
            if translated and len(translated.strip()) >= 50:
                await db.chapters.update_one(
                    {"id": chapter_id},
                    {"$set": {
                        "content_as": translated,
                        "content_as_generated_at": datetime.now(timezone.utc).isoformat(),
                    }}
                )
                content_as_words = len(translated.split())
                logger.info(f"Auto-translated chapter {chapter_id} to Assamese ({content_as_words} words)")
        except Exception as e:
            logger.warning(f"Auto-translate to Assamese failed for {chapter_id}: {e}")

    return {
        "chapter_id": chapter_id,
        "title": title,
        "content": notes_text,
        "word_count": len(notes_text.split()),
        "content_as_words": content_as_words,
        "message": "Notes generated successfully",
    }


async def _translate_text_sarvam(text: str, source_lang: str = "en-IN", target_lang: str = "as-IN") -> str:
    """Translate text via PROVIDER_PRIORITY["translate"] weighted dispatch.

    Routes through call_translate_with_dispatch (sarvam → vertex → workers_ai)
    with chunking to stay under Sarvam's 1 800-char per-request limit.
    Falls back to legacy sarvam_translate_client if dispatch exhausts all providers.
    """
    if not text or not text.strip():
        return ""
    MAX_CHUNK = 1800
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(line) > MAX_CHUNK:
            if current:
                chunks.append(current)
                current = ""
            for j in range(0, len(line), MAX_CHUNK):
                chunks.append(line[j:j + MAX_CHUNK])
        elif len(current) + len(line) + 1 > MAX_CHUNK and current:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)

    translated_parts = []
    _target_lang_bcp = target_lang.split("-")[0] if "-" in target_lang else "as"
    for chunk in chunks:
        try:
            translated = await call_translate_with_dispatch(
                chunk[:1950], source_lang, target_lang, lang=_target_lang_bcp
            )
            translated_parts.append(translated)
        except Exception as e:
            logger.warning(f"Translate dispatch failed for chunk ({source_lang}→{target_lang}): {e}")
            _client = sarvam_translate_client or sarvam_client
            if not _client:
                raise HTTPException(status_code=503, detail="Translation failed — no provider available")
            try:
                payload = {
                    "input": chunk[:1950],
                    "source_language_code": source_lang,
                    "target_language_code": target_lang,
                    "speaker_gender": "Female",
                    "mode": "formal",
                    "model": "sarvam-translate:v1",
                    "enable_preprocessing": False,
                }
                resp = await _client.post("/translate", json=payload)
                resp.raise_for_status()
                translated_parts.append(resp.json().get("translated_text", ""))
            except Exception as e2:
                logger.warning(f"Sarvam translate fallback chunk failed: {e2}")
                raise
    return "\n".join(translated_parts)


@router.post("/admin/content/chapters/{chapter_id}/translate")
async def admin_translate_chapter(chapter_id: str, data: dict = Body(default={}), admin: dict = Depends(get_admin_user)):
    chapter = await db.chapters.find_one({"id": chapter_id}, {"_id": 0})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    target_lang = (data.get("target_lang") or "as-IN").strip()
    source_content = chapter.get("content", "")
    if not source_content or not source_content.strip():
        raise HTTPException(status_code=400, detail="No English content to translate. Generate notes first.")

    translated = await _translate_text_sarvam(source_content, "en-IN", target_lang)
    if not translated or len(translated.strip()) < 50:
        raise HTTPException(status_code=502, detail="Translation returned empty or too short")

    field_name = "content_as" if "as" in target_lang else f"content_{target_lang.split('-')[0]}"
    await db.chapters.update_one(
        {"id": chapter_id},
        {"$set": {
            field_name: translated,
            f"{field_name}_generated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    _invalidate_content_cache("chapters")

    return {
        "chapter_id": chapter_id,
        "field": field_name,
        "word_count": len(translated.split()),
        "translated_text": translated,
        "message": f"Translated to {target_lang} successfully",
    }


@router.post("/admin/content/bulk-translate-subject")
async def admin_bulk_translate_subject(data: dict = Body(...), admin: dict = Depends(get_admin_user)):
    subject_id = data.get("subject_id")
    if not subject_id:
        raise HTTPException(status_code=400, detail="subject_id required")
    target_lang = data.get("target_lang", "as-IN")

    chapters = await db.chapters.find(
        {"subject_id": subject_id, "content": {"$exists": True, "$ne": ""}},
        {"_id": 0, "id": 1, "title": 1, "content": 1, "content_as": 1}
    ).to_list(200)

    results = []
    for ch in chapters:
        if ch.get("content_as") and not data.get("force"):
            results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "skipped", "reason": "already translated"})
            continue
        try:
            translated = await _translate_text_sarvam(ch["content"], "en-IN", target_lang)
            if translated and len(translated.strip()) >= 50:
                field_name = "content_as"
                await db.chapters.update_one(
                    {"id": ch["id"]},
                    {"$set": {
                        field_name: translated,
                        f"{field_name}_generated_at": datetime.now(timezone.utc).isoformat(),
                    }}
                )
                results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "translated", "words": len(translated.split())})
            else:
                results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "failed", "reason": "empty translation"})
        except Exception as e:
            results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "failed", "reason": str(e)[:100]})

    _invalidate_content_cache("chapters")
    touched_ids = [r["chapter_id"] for r in results if r["status"] == "translated"]
    indexnow_notified = await _schedule_indexnow_for_chapter_ids(
        touched_ids, source="admin_bulk_translate_subject")
    return {
        "subject_id": subject_id,
        "total": len(chapters),
        "translated": sum(1 for r in results if r["status"] == "translated"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "indexnow_notified": indexnow_notified,
        "results": results,
    }


@router.post("/admin/content/bulk-translate-all")
async def admin_bulk_translate_all(data: dict = Body(default={}), admin: dict = Depends(get_admin_user)):
    """Translate ALL chapters with English content to Assamese. Skips PYQ-only chapters."""
    target_lang = data.get("target_lang", "as-IN")
    force = data.get("force", False)

    query = {"content": {"$exists": True, "$ne": ""}}
    if not force:
        query["$or"] = [
            {"content_as": {"$exists": False}},
            {"content_as": ""},
            {"content_as": None},
        ]

    chapters = await db.chapters.find(
        query,
        {"_id": 0, "id": 1, "title": 1, "content": 1, "subject_id": 1}
    ).to_list(500)

    logger.info(f"Bulk translate all: {len(chapters)} chapters to translate")

    results = []
    for i, ch in enumerate(chapters):
        content = ch.get("content", "")
        if not content or len(content.strip()) < 100:
            results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "skipped", "reason": "content too short"})
            continue
        try:
            translated = await _translate_text_sarvam(content, "en-IN", target_lang)
            if translated and len(translated.strip()) >= 50:
                await db.chapters.update_one(
                    {"id": ch["id"]},
                    {"$set": {
                        "content_as": translated,
                        "content_as_generated_at": datetime.now(timezone.utc).isoformat(),
                    }}
                )
                results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "translated", "words": len(translated.split())})
                logger.info(f"Translated {i+1}/{len(chapters)}: {ch.get('title', '')[:40]} ({len(translated.split())} words)")
            else:
                results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "failed", "reason": "empty translation"})
        except Exception as e:
            logger.warning(f"Translate failed for {ch['id']}: {e}")
            results.append({"chapter_id": ch["id"], "title": ch.get("title", ""), "status": "failed", "reason": str(e)[:100]})

    _invalidate_content_cache("chapters")
    touched_ids = [r["chapter_id"] for r in results if r["status"] == "translated"]
    indexnow_notified = await _schedule_indexnow_for_chapter_ids(
        touched_ids, source="admin_bulk_translate_all")
    return {
        "total": len(chapters),
        "translated": sum(1 for r in results if r["status"] == "translated"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "indexnow_notified": indexnow_notified,
        "results": results,
    }


@router.post("/admin/content/bulk-regenerate-notes")
async def admin_bulk_regenerate_all_notes(admin: dict = Depends(get_admin_user)):
    """Regenerate notes for ALL chapters across ALL subjects. Sequential with progress."""
    all_subjects = await db.subjects.find(
        {"status": {"$ne": "archived"}},
        {"_id": 0, "id": 1, "name": 1}
    ).to_list(200)

    all_chapters = []
    for s in all_subjects:
        chs = await db.chapters.find(
            {"subject_id": s["id"]},
            {"_id": 0, "id": 1, "title": 1, "topics": 1, "description": 1, "subject_id": 1}
        ).to_list(50)
        for ch in chs:
            if ch.get("topics") or ch.get("description"):
                all_chapters.append(ch)

    results = []
    ok = 0
    fail = 0
    total = len(all_chapters)
    logger.info(f"[BULK-REGEN] Starting bulk regeneration for {total} chapters")

    for idx, ch in enumerate(all_chapters):
        ch_id = ch["id"]
        ch_title = ch.get("title", "")
        logger.info(f"[BULK-REGEN] [{idx+1}/{total}] Generating: {ch_title}")
        try:
            result = await admin_generate_chapter_notes(ch_id, admin)
            wc = result.get("word_count", 0)
            results.append({"chapter_id": ch_id, "title": ch_title, "word_count": wc, "status": "ok"})
            ok += 1
            logger.info(f"[BULK-REGEN] [{idx+1}/{total}] Done: {ch_title} ({wc} words)")
        except Exception as e:
            results.append({"chapter_id": ch_id, "title": ch_title, "status": "error", "error": str(e)[:200]})
            fail += 1
            logger.error(f"[BULK-REGEN] [{idx+1}/{total}] Failed: {ch_title} — {e}")
        if idx < total - 1:
            await asyncio.sleep(2)

    _invalidate_content_cache("chapters")
    touched_ids = [r["chapter_id"] for r in results if r.get("status") == "ok"]
    indexnow_notified = await _schedule_indexnow_for_chapter_ids(
        touched_ids, source="admin_bulk_regenerate_notes")
    return {
        "total": total,
        "succeeded": ok,
        "failed": fail,
        "indexnow_notified": indexnow_notified,
        "results": results,
    }


@router.post("/admin/content/normalize-headings")
async def admin_normalize_all_headings(admin: dict = Depends(get_admin_user)):
    """Normalize headings in all chapters with content: convert **bold** lines to ## headings."""
    total = 0
    updated = 0
    batch_size = 200
    skip = 0
    while True:
        chapters = await db.chapters.find(
            {"content": {"$exists": True, "$ne": ""}},
            {"_id": 0, "id": 1, "content": 1}
        ).skip(skip).limit(batch_size).to_list(batch_size)
        if not chapters:
            break
        total += len(chapters)
        for ch in chapters:
            original = ch.get("content", "")
            normalized = _normalize_headings(original)
            if normalized != original:
                await db.chapters.update_one(
                    {"id": ch["id"]},
                    {"$set": {"content": normalized}}
                )
                updated += 1
        skip += batch_size
    return {"total_chapters": total, "updated": updated}


@router.post("/admin/content/subject/{subject_id}/format-notes")
async def admin_format_subject_notes(subject_id: str, admin: dict = Depends(get_admin_user)):
    """Re-format all chapter content for a subject: convert raw markdown to
    well-structured, mobile-responsive, textbook-style HTML. No AI generation —
    only structural formatting of existing content."""
    subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    chapters = await db.chapters.find(
        {"subject_id": subject_id}, {"_id": 0}
    ).to_list(200)

    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters found for this subject")

    formatted = 0
    skipped = 0
    for ch in chapters:
        raw_content = (ch.get("content") or "").strip()
        if not raw_content or len(raw_content) < 30:
            skipped += 1
            continue

        normalized = _normalize_headings(raw_content).strip()

        content_html = _format_content_html(normalized)

        word_count = len(re.sub(r'<[^>]+>', '', content_html).split())

        await db.chapters.update_one(
            {"id": ch["id"]},
            {"$set": {
                "content": normalized,
                "content_html": content_html,
                "word_count": word_count,
                "formatted_at": datetime.now(timezone.utc).isoformat(),
            }}
        )
        formatted += 1

    _invalidate_content_cache("chapters")

    seo_pages = await db.seo_pages.find(
        {"subject_slug": subject.get("slug", ""), "status": "published"},
        {"_id": 0, "id": 1, "content": 1, "topic_id": 1},
    ).to_list(5000)

    seo_formatted = 0
    for page in seo_pages:
        raw = (page.get("content") or "").strip()
        if not raw or len(raw) < 30:
            continue
        html = _format_content_html(raw)
        await db.seo_pages.update_one(
            {"id": page["id"]},
            {"$set": {
                "content_html": html,
                "formatted_at": datetime.now(timezone.utc).isoformat(),
            }}
        )
        seo_formatted += 1

    return {
        "message": f"Formatted {formatted} chapters, {seo_formatted} SEO pages ({skipped} skipped — no content)",
        "chapters_formatted": formatted,
        "seo_pages_formatted": seo_formatted,
        "chapters_skipped": skipped,
    }


@router.get("/admin/content/thin-chapters")
async def admin_list_thin_chapters(
    min_words: int = Query(default=500, description="Chapters below this word count are thin"),
    admin: dict = Depends(get_admin_user),
):
    pipeline = [
        {"$match": {"content": {"$exists": True, "$ne": ""}}},
        {"$project": {
            "id": 1, "title": 1, "subject_id": 1, "content": 1,
            "needs_review": 1, "order_index": 1, "_id": 0,
        }},
    ]
    chapters = await db.chapters.aggregate(pipeline).to_list(500)

    thin = []
    for ch in chapters:
        wc = len((ch.get("content") or "").split())
        if wc < min_words:
            thin.append({
                "id": ch.get("id"),
                "title": ch.get("title", ""),
                "subject_id": ch.get("subject_id", ""),
                "word_count": wc,
                "needs_review": ch.get("needs_review", False),
            })
    thin.sort(key=lambda x: x["word_count"])
    return {"total": len(thin), "min_words": min_words, "chapters": thin}


_PIPELINE_CONCURRENCY = int(os.environ.get("PIPELINE_LLM_CONCURRENCY", 4))
_pipeline_sem = asyncio.Semaphore(_PIPELINE_CONCURRENCY)
_WAVE_SIZE = int(os.environ.get("CONTENT_WAVE_SIZE", 3))

# Polish-stage quiz pre-generation (Vertex Gemini) runs as a fire-and-forget
# background task per chapter. Without a dedicated semaphore the bulk
# generate-all flow would schedule N independent 3-language Vertex jobs
# in parallel (one per chapter in a wave × all waves) which both pile up
# Gemini calls and starves observability. This semaphore caps the
# concurrent polish-stage quiz jobs across the whole process.
_QUIZ_PREGEN_CONCURRENCY = int(
    os.environ.get("PIPELINE_QUIZ_PREGEN_CONCURRENCY", 2)
)
_quiz_pregen_sem = asyncio.Semaphore(_QUIZ_PREGEN_CONCURRENCY)


async def _polish_stage_quiz_pregen(chapter_doc: dict) -> None:
    """Bounded wrapper around `pregenerate_chapter_quiz(prefer_vertex=True)`
    used by the polish stage in `_generate_chapter_all`.

    * Acquires `_quiz_pregen_sem` so the bulk wave never schedules an
      unbounded number of concurrent Vertex Gemini quiz jobs.
    * Logs structured completion / failure so the otherwise-silent
      fire-and-forget scheduling is observable in production.
    * Never raises — `pregenerate_chapter_quiz` is best-effort by
      contract and the lazy fallback in `quiz_generate` recovers any
      miss the first time a student opens the quiz.
    """
    chapter_id = chapter_doc.get("id") or "<unknown>"
    title = (chapter_doc.get("title") or "")[:80]
    try:
        async with _quiz_pregen_sem:
            from routes.edu_study import pregenerate_chapter_quiz
            ok = await pregenerate_chapter_quiz(chapter_doc, prefer_vertex=True)
        logger.info(
            f"[POLISH] quiz pre-gen {'ok' if ok else 'partial'} for "
            f"chapter_id={chapter_id} title={title!r} provider=vertex"
        )
    except Exception as e:
        logger.warning(
            f"[POLISH] quiz pre-gen crashed for chapter_id={chapter_id} "
            f"title={title!r}: {e}"
        )


async def _resolve_board_context(subject: dict) -> tuple:
    board_label = ""
    class_name = subject.get("className", "")
    subject_desc = (subject.get("description") or "").strip()
    stream = None
    if subject.get("stream_id"):
        stream = await db.streams.find_one({"id": subject["stream_id"]}, {"_id": 0})
    if stream and stream.get("class_id"):
        cls = await db.classes.find_one({"id": stream["class_id"]}, {"_id": 0})
        if cls:
            if not class_name:
                class_name = cls.get("name", "")
            if cls.get("board_id"):
                board_doc = await db.boards.find_one({"id": cls["board_id"]}, {"_id": 0})
                if board_doc:
                    board_label = board_doc.get("name", "")
    return (board_label or "Degree", class_name or "FYUGP", subject_desc)


async def _regenerate_one_chapter(chapter: dict, subject: dict, min_words: int) -> dict:
    chapter_id = chapter.get("id", "")
    title = (chapter.get("title") or "").strip()
    if not title:
        return {"chapter_id": chapter_id, "status": "skipped", "reason": "no title"}

    subject_name = subject.get("name", "")
    topics = chapter.get("topics") or []
    topic_block = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(topics)) if topics else f"  {title}"

    board_ctx, class_ctx, subject_desc = await _resolve_board_context(subject)

    desc_block = ""
    ch_desc = (chapter.get("description") or "").strip()
    if ch_desc:
        desc_block += f"**Chapter Description:** {ch_desc}\n"
    if subject_desc:
        desc_block += f"**Subject Description:** {subject_desc}\n"

    prompt = f"""You are an expert academic content writer for {board_ctx} {class_ctx} students.

Generate topic-wise study notes for:
**Chapter:** {title}
**Subject:** {subject_name or "Degree Course"}
{desc_block}

**Topics to cover:**
{topic_block}

**INSTRUCTIONS:**
1. For EACH topic, write:
   - A ## Heading matching the topic name exactly
   - A **bold definition** (1-2 sentences) as the opening line
   - A focused explanation in 5-8 sentences covering definition, characteristics, causes/effects, and significance
   - 4-6 bullet points of key facts with **bold** keywords
2. Use ## headings for each topic (match topic names exactly), ### for subtopics
3. Use markdown formatting throughout. NO disclaimers, NO preamble.
4. NO introduction section. NO summary section. NO exam tips. NO extra examples. NO cross-references. Start directly with the first ## topic heading.
5. Maximum 3-4 lines before the first ## heading. Do NOT write a long introduction paragraph. The very first line of output should ideally be a ## heading.
6. Each topic section must be self-contained — one concept, one definition, one explanation, key facts. Nothing else."""

    try:
        async with _pipeline_sem:
            generated = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=6000)
        if generated and len(generated.split()) >= 200:
            generated = _normalize_headings(generated).strip()
            generated = _trim_long_intro(generated)
            generated = await _polish_notes_with_sarvam(generated, title, subject_name or "")
            generated = _normalize_headings(generated).strip()
            generated = _trim_long_intro(generated)
            wc = len(generated.split())
            update_fields = {
                "content": generated,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "needs_review": wc < min_words,
            }
            await db.chapters.update_one({"id": chapter_id}, {"$set": update_fields})
            try:
                await auto_chunk_content(chapter_id=chapter_id, content=generated, subject_id=chapter.get("subject_id"), category=chapter.get("category", "notes"), topics=topics)
            except Exception:
                pass
            return {"chapter_id": chapter_id, "title": title, "status": "ok", "word_count": wc}
        else:
            return {"chapter_id": chapter_id, "title": title, "status": "error", "reason": "AI returned too-short content"}
    except Exception as e:
        return {"chapter_id": chapter_id, "title": title, "status": "error", "reason": str(e)}


@router.post("/admin/content/regenerate-thin")
async def admin_regenerate_thin_chapters(
    min_words: int = Query(default=500, description="Regenerate chapters below this word count"),
    limit: int = Query(default=10, description="Max chapters to regenerate per call"),
    admin: dict = Depends(get_admin_user),
):
    pipeline = [
        {"$match": {"content": {"$exists": True, "$ne": ""}}},
        {"$project": {"id": 1, "title": 1, "subject_id": 1, "content": 1, "topics": 1, "description": 1, "category": 1, "_id": 0}},
    ]
    all_chapters = await db.chapters.aggregate(pipeline).to_list(500)

    thin = [ch for ch in all_chapters if len((ch.get("content") or "").split()) < min_words]
    thin.sort(key=lambda x: len((x.get("content") or "").split()))
    thin = thin[:limit]

    subject_cache: dict = {}
    async def get_subject(sid: str) -> dict:
        if sid not in subject_cache:
            subject_cache[sid] = await db.subjects.find_one({"id": sid}, {"_id": 0}) or {}
        return subject_cache[sid]

    tasks = []
    skipped = []
    for chapter in thin:
        title = (chapter.get("title") or "").strip()
        if not title:
            skipped.append({"chapter_id": chapter.get("id", ""), "status": "skipped", "reason": "no title"})
            continue
        subject = await get_subject(chapter.get("subject_id", ""))
        tasks.append(_regenerate_one_chapter(chapter, subject, min_words))

    t0 = time.time()
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = round(time.time() - t0, 1)

    valid_chapters = [ch for ch in thin if (ch.get("title") or "").strip()]
    results = list(skipped)
    for i, r in enumerate(batch_results):
        if isinstance(r, Exception):
            cid = valid_chapters[i].get("id", "unknown") if i < len(valid_chapters) else "unknown"
            results.append({"chapter_id": cid, "status": "error", "reason": str(r)})
        else:
            results.append(r)

    ok_count = sum(1 for r in results if r.get("status") == "ok")
    return {
        "total_thin": len(thin),
        "regenerated": ok_count,
        "elapsed_seconds": elapsed,
        "concurrency": _PIPELINE_CONCURRENCY,
        "results": results,
    }


@router.get("/admin/content/needs-review")
async def admin_needs_review_chapters(admin: dict = Depends(get_admin_user)):
    chapters = await db.chapters.find(
        {"needs_review": True},
        {"_id": 0, "id": 1, "title": 1, "subject_id": 1, "content": 1}
    ).to_list(100)
    items = []
    for ch in chapters:
        items.append({
            "id": ch.get("id"),
            "title": ch.get("title", ""),
            "subject_id": ch.get("subject_id", ""),
            "word_count": len((ch.get("content") or "").split()),
        })
    return {"total": len(items), "chapters": items}


@router.post("/admin/content/chapters/{chapter_id}/approve")
async def admin_approve_chapter(chapter_id: str, admin: dict = Depends(get_admin_user)):
    result = await db.chapters.update_one(
        {"id": chapter_id},
        {"$set": {"needs_review": False, "reviewed_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Chapter not found or already approved")
    return {"chapter_id": chapter_id, "status": "approved"}


@router.post("/admin/content/chapters/{chapter_id}/rechunk")
async def admin_rechunk_chapter(chapter_id: str, admin: dict = Depends(get_admin_user)):
    """
    Manually re-chunk a specific chapter.
    Useful for fixing chunking issues or after manual content edits.
    """
    try:
        result = await rechunk_chapter(chapter_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Re-chunking failed for chapter {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Re-chunking failed: {str(e)}")


@router.get("/admin/content/chapters/{chapter_id}/stats")
async def get_chapter_stats(chapter_id: str, admin: dict = Depends(get_admin_user)):
    """Get chunk, content, question, flashcard, and SEO stats for a single chapter."""
    chapter = await db.chapters.find_one({"id": chapter_id}, {"_id": 0})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    ch_slug = chapter.get("slug", "")
    seo_page_query = {"chapter_slug": ch_slug, "status": "published"} if ch_slug else {"_no_match_": True}
    chunk_count, pyq_doc, fc_doc, geo_blog_count, pyq_html_count, seo_topic_count, seo_pages_list = await asyncio.gather(
        db.chunks.count_documents({"chapter_id": chapter_id}),
        db.ai_pyq_collections.find_one({"chapter_id": chapter_id}, {"_id": 0, "total": 1, "mark_wise": 1}),
        db.flashcard_collections.find_one({"chapter_id": chapter_id}, {"_id": 0, "total": 1, "cards": 1, "flashcards": 1}),
        db.seo_pages.count_documents({"linked_chapter_id": chapter_id, "type": "geo_blog"}),
        db.pyq_html_pages.count_documents({"chapter_id": chapter_id}),
        db.seo_topics.count_documents({"linked_chapter_id": chapter_id}),
        db.seo_pages.find(seo_page_query, {"_id": 0, "page_type": 1, "title": 1, "quality": 1}).to_list(100),
    )
    if not pyq_doc:
        pyq_doc = await db.topic_pyq_collections.find_one({"chapter_id": chapter_id}, {"_id": 0, "total": 1})
    content_len = len(chapter.get("content", "") or "")
    mark_wise = pyq_doc.get("mark_wise", {}) if pyq_doc else {}
    pyq_count = pyq_doc.get("total", 0) if pyq_doc else 0
    if pyq_count == 0 and mark_wise:
        pyq_count = sum(len(v) for v in mark_wise.values())
    fc_total = fc_doc.get("total", 0) if fc_doc else 0
    if fc_total == 0 and fc_doc:
        fc_total = len(fc_doc.get("cards") or fc_doc.get("flashcards") or [])

    seo_page_types = {}
    for sp in seo_pages_list:
        pt = sp.get("page_type", "unknown")
        seo_page_types[pt] = seo_page_types.get(pt, 0) + 1

    linked_topic_ids = chapter.get("linked_topic_ids", [])
    linked_topics = []
    if linked_topic_ids:
        linked_topics = await db.seo_topics.find(
            {"id": {"$in": linked_topic_ids}},
            {"_id": 0, "id": 1, "title": 1, "slug": 1, "status": 1, "primary_keyword": 1}
        ).to_list(50)

    return {
        "chapter_id": chapter_id,
        "chapter_title": chapter.get("title", ""),
        "content_length": content_len,
        "chunk_count": chunk_count,
        "has_slug": bool(chapter.get("slug")),
        "content_type": chapter.get("content_type", "notes"),
        "attached_files": chapter.get("attached_files", []),
        "pyq_count": pyq_count,
        "mark_wise_counts": {k: len(v) for k, v in mark_wise.items()} if mark_wise else {},
        "flashcard_count": fc_total,
        "geo_blog_count": geo_blog_count,
        "pyq_html_count": pyq_html_count,
        "notes_generated": bool(chapter.get("notes_generated") or content_len > 100),
        "seo_topic_count": seo_topic_count,
        "seo_page_types": seo_page_types,
        "seo_pages_published": len(seo_pages_list),
        "linked_topics": linked_topics,
    }


@router.get("/admin/content/subject/{subject_id}/chapter-cards")
async def get_subject_chapter_cards(subject_id: str, admin: dict = Depends(get_admin_user)):
    """Return content-card data for every chapter in a subject — single batch call.

    Each card includes: notes status, mark-wise question counts, flashcard count,
    linked SEO topics, published SEO page types, and blog count.
    """
    chapters = await db.chapters.find(
        {"subject_id": subject_id},
        {"_id": 0, "id": 1, "title": 1, "slug": 1, "content": 1, "notes_generated": 1,
         "linked_topic_ids": 1, "order_index": 1, "description": 1, "coverage_score": 1,
         "content_type": 1}
    ).sort("order_index", 1).to_list(200)
    if not chapters:
        return {"cards": [], "subject_id": subject_id}

    ch_ids = [c["id"] for c in chapters]
    ch_slugs = [c.get("slug", "") for c in chapters if c.get("slug")]

    pyq_docs, fc_docs, seo_topic_docs, seo_pages_raw, blog_counts = await asyncio.gather(
        db.ai_pyq_collections.find(
            {"chapter_id": {"$in": ch_ids}},
            {"_id": 0, "chapter_id": 1, "total": 1, "mark_wise": 1}
        ).to_list(200),
        db.flashcard_collections.find(
            {"chapter_id": {"$in": ch_ids}},
            {"_id": 0, "chapter_id": 1, "total": 1, "cards": 1, "flashcards": 1}
        ).to_list(200),
        db.seo_topics.find(
            {"linked_chapter_id": {"$in": ch_ids}},
            {"_id": 0, "id": 1, "linked_chapter_id": 1, "title": 1, "slug": 1, "status": 1, "primary_keyword": 1}
        ).to_list(1000),
        db.seo_pages.find(
            {"chapter_slug": {"$in": ch_slugs}, "status": "published"},
            {"_id": 0, "chapter_slug": 1, "page_type": 1}
        ).to_list(5000),
        db.seo_pages.aggregate([
            {"$match": {"linked_chapter_id": {"$in": ch_ids}, "type": "geo_blog"}},
            {"$group": {"_id": "$linked_chapter_id", "count": {"$sum": 1}}}
        ]).to_list(200),
    )

    fallback_pyq_ids = [cid for cid in ch_ids if not any(p["chapter_id"] == cid for p in pyq_docs)]
    fallback_pyqs = []
    if fallback_pyq_ids:
        fallback_pyqs = await db.topic_pyq_collections.find(
            {"chapter_id": {"$in": fallback_pyq_ids}},
            {"_id": 0, "chapter_id": 1, "total": 1}
        ).to_list(200)

    pyq_map = {}
    for p in pyq_docs:
        mark_wise = p.get("mark_wise", {})
        total = p.get("total", 0)
        if total == 0 and mark_wise:
            total = sum(len(v) for v in mark_wise.values())
        pyq_map[p["chapter_id"]] = {
            "total": total,
            "mark_wise": {k: len(v) for k, v in mark_wise.items()} if mark_wise else {},
        }
    for fp in fallback_pyqs:
        if fp["chapter_id"] not in pyq_map:
            pyq_map[fp["chapter_id"]] = {"total": fp.get("total", 0), "mark_wise": {}}

    fc_map = {}
    for f in fc_docs:
        total = f.get("total", 0)
        if total == 0:
            total = len(f.get("cards") or f.get("flashcards") or [])
        fc_map[f["chapter_id"]] = total

    topic_map: dict[str, list] = {}
    for t in seo_topic_docs:
        ch_id = t.get("linked_chapter_id", "")
        if ch_id:
            topic_map.setdefault(ch_id, []).append({
                "id": t["id"], "title": t["title"], "slug": t.get("slug", ""),
                "status": t.get("status", ""), "primary_keyword": t.get("primary_keyword", ""),
            })

    seo_pages_by_slug: dict[str, dict] = {}
    for sp in seo_pages_raw:
        slug = sp.get("chapter_slug", "")
        if slug:
            pt = sp.get("page_type", "unknown")
            seo_pages_by_slug.setdefault(slug, {})
            seo_pages_by_slug[slug][pt] = seo_pages_by_slug[slug].get(pt, 0) + 1

    blog_map = {b["_id"]: b["count"] for b in blog_counts}

    cards = []
    for ch in chapters:
        content_len = len(ch.get("content", "") or "")
        has_notes = bool(ch.get("notes_generated") or content_len > 100)
        pyq_info = pyq_map.get(ch["id"], {"total": 0, "mark_wise": {}})
        cards.append({
            "chapter_id": ch["id"],
            "title": ch.get("title", ""),
            "slug": ch.get("slug", ""),
            "description": ch.get("description", ""),
            "order_index": ch.get("order_index", 0),
            "content_type": ch.get("content_type", "notes"),
            "notes_generated": has_notes,
            "word_count": len((ch.get("content", "") or "").split()) if has_notes else 0,
            "coverage_score": ch.get("coverage_score"),
            "pyq_count": pyq_info["total"],
            "mark_wise_counts": pyq_info["mark_wise"],
            "flashcard_count": fc_map.get(ch["id"], 0),
            "blog_count": blog_map.get(ch["id"], 0),
            "seo_topic_count": len(topic_map.get(ch["id"], [])),
            "linked_topics": topic_map.get(ch["id"], []),
            "seo_page_types": seo_pages_by_slug.get(ch.get("slug", ""), {}),
            "seo_pages_published": sum(seo_pages_by_slug.get(ch.get("slug", ""), {}).values()),
        })

    return {"cards": cards, "subject_id": subject_id, "total": len(cards)}


def _compute_topic_coverage(topics: list, content: str, questions: list = None, flashcards: list = None) -> dict:
    """Compute how many listed topics have meaningful representation in generated content."""
    if not topics:
        return {"score": 100, "total_topics": 0, "covered": 0, "missing": []}
    content_lower = (content or "").lower()
    q_text = " ".join(
        (q.get("question", "") if isinstance(q, dict) else str(q))
        for q in (questions or [])
    ).lower()
    fc_text = " ".join(
        ((f.get("front", "") + " " + f.get("back", "")) if isinstance(f, dict) else str(f))
        for f in (flashcards or [])
    ).lower()
    combined = content_lower + " " + q_text + " " + fc_text
    covered = []
    missing = []
    for topic in topics:
        t = str(topic).strip()
        if not t:
            continue
        t_lower = t.lower()
        if t_lower in combined:
            covered.append(t)
            continue
        words = [w for w in re.split(r'\W+', t_lower) if w]
        if not words:
            covered.append(t)
            continue
        matched_words = sum(1 for w in words if w in combined)
        threshold = max(1, len(words) * 0.4)
        if matched_words >= threshold:
            covered.append(t)
        else:
            missing.append(t)
    total = len(covered) + len(missing)
    score = round((len(covered) / total) * 100) if total > 0 else 100
    return {"score": score, "total_topics": total, "covered": len(covered), "missing": missing}


@router.get("/admin/content/chapters/{subject_id}/coverage")
async def admin_subject_coverage(subject_id: str, admin: dict = Depends(get_admin_user)):
    """Compute per-chapter syllabus topic coverage scores for a subject."""
    chapters = await db.chapters.find({"subject_id": subject_id}).sort("order_index", 1).to_list(500)
    if not chapters:
        return {"subject_id": subject_id, "chapters": []}
    chapter_ids = [c["id"] for c in chapters]
    pyq_docs, fc_docs = await asyncio.gather(
        db.ai_pyq_collections.find({"chapter_id": {"$in": chapter_ids}}, {"_id": 0}).to_list(500),
        db.flashcard_collections.find({"chapter_id": {"$in": chapter_ids}}, {"_id": 0}).to_list(500),
    )
    pyq_map = {d["chapter_id"]: d.get("pyqs", []) for d in pyq_docs}
    fc_map = {d["chapter_id"]: d.get("flashcards", []) for d in fc_docs}
    results = []
    for ch in chapters:
        cid = ch["id"]
        ch_questions = ch.get("important_questions") or pyq_map.get(cid, [])
        ch_flashcards = ch.get("memory_tricks") or fc_map.get(cid, [])
        cov = _compute_topic_coverage(
            ch.get("topics", []),
            ch.get("content", ""),
            ch_questions,
            ch_flashcards,
        )
        await db.chapters.update_one({"id": cid}, {"$set": {"coverage_score": cov["score"]}})
        results.append({
            "chapter_id": cid,
            "title": ch.get("title", ""),
            "coverage_score": cov["score"],
            "total_topics": cov["total_topics"],
            "covered": cov["covered"],
            "missing": cov["missing"],
            "flagged": cov["score"] < 60,
        })
    return {"subject_id": subject_id, "chapters": results}


@router.post("/admin/content/chapters/{chapter_id}/attach-file")
async def attach_file_to_chapter(
    chapter_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user)
):
    """Upload and attach a file (PDF/text) to a chapter."""
    chapter = await db.chapters.find_one({"id": chapter_id}, {"_id": 0, "id": 1, "subject_id": 1, "attached_files": 1})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    file_content = await file.read()
    max_file_size = 10 * 1024 * 1024
    if len(file_content) > max_file_size:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    file_ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'txt'
    if file_ext not in ('pdf', 'txt', 'md'):
        raise HTTPException(status_code=400, detail="Only PDF, TXT, and MD files are supported")
    file_id = str(uuid.uuid4())

    extracted_text = ""
    pdf_url = ""

    if file_ext == 'pdf' and supa:
        import time as _t
        storage_path = f"pdfs/{chapter['subject_id']}/{_t.time():.0f}_{file.filename.replace(' ', '_')}"
        try:
            supa.storage.from_("study-materials").upload(path=storage_path, file=file_content, file_options={"content-type": "application/pdf", "upsert": "false"})
            pdf_url = supa.storage.from_("study-materials").get_public_url(storage_path)
        except Exception as e:
            logger.warning(f"Supabase upload failed, storing base64: {e}")
            import base64
            pdf_url = f"data:application/pdf;base64,{base64.b64encode(file_content).decode()}"
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(file_content))
            extracted_text = "\n".join(p.extract_text() or "" for p in reader.pages).strip()
        except Exception:
            pass
    elif file_ext in ('txt', 'md'):
        extracted_text = file_content.decode('utf-8', errors='ignore')

    attachment = {
        "id": file_id,
        "file_name": file.filename,
        "file_ext": file_ext,
        "file_size": len(file_content),
        "url": pdf_url,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

    existing_files = chapter.get("attached_files", []) or []
    existing_files.append(attachment)
    update_fields = {"attached_files": existing_files}

    if extracted_text and len(extracted_text) > 50:
        old_content = (await db.chapters.find_one({"id": chapter_id}, {"content": 1})).get("content", "") or ""
        separator = "\n\n---\n\n"
        update_fields["content"] = old_content + separator + f"## {file.filename}\n\n{extracted_text}" if old_content else extracted_text
        update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.chapters.update_one({"id": chapter_id}, {"$set": update_fields})

    if extracted_text and len(extracted_text) > 100:
        try:
            await rechunk_chapter(chapter_id)
        except Exception:
            pass

    _invalidate_content_cache("chapters")
    return {"attachment": attachment, "text_extracted": len(extracted_text)}


@router.post("/admin/content/bulk-rechunk")
async def admin_bulk_rechunk_all_chapters(
    subject_id: Optional[str] = None,
    admin: dict = Depends(get_admin_user)
):
    """
    Bulk re-chunk all chapters (or chapters in a specific subject).
    
    Use cases:
    - Initial setup: chunk all existing chapters that have content
    - After algorithm improvements
    - Database migration
    
    Query params:
    - subject_id (optional): Only rechunk chapters from this subject
    """
    # Find chapters with content
    filter_query = {"content": {"$exists": True, "$ne": ""}}
    if subject_id:
        filter_query["subject_id"] = subject_id
    
    chapters = await db.chapters.find(filter_query, {"_id": 0, "id": 1, "title": 1, "subject_id": 1}).to_list(1000)
    
    if not chapters:
        return {
            "message": "No chapters with content found",
            "total": 0,
            "chunked": 0,
            "failed": 0
        }
    
    total = len(chapters)
    chunked = 0
    failed = 0
    failed_chapters = []
    
    for chapter in chapters:
        try:
            result = await rechunk_chapter(chapter["id"])
            if result["chunks_created"] > 0:
                chunked += 1
                logger.info(f"✅ Bulk rechunked: {chapter['title']} → {result['chunks_created']} chunks")
        except Exception as e:
            failed += 1
            failed_chapters.append({
                "chapter_id": chapter["id"],
                "title": chapter.get("title"),
                "error": str(e)
            })
            logger.error(f"❌ Bulk rechunk failed for {chapter.get('title')}: {e}")
    
    return {
        "message": f"Bulk re-chunking complete",
        "total_chapters": total,
        "successfully_chunked": chunked,
        "failed": failed,
        "failed_chapters": failed_chapters if failed > 0 else []
    }


@router.get("/admin/content/chunks/stats")
async def get_chunking_stats(admin: dict = Depends(get_admin_user)):
    """
    Get statistics about content chunking across the platform.
    Useful for monitoring RAG quality.
    """
    try:
        if not await is_mongo_available():
            return {"total_chunks": 0, "total_chapters": 0, "chapters_with_content": 0, "chapters_with_chunks": 0, "chapters_without_chunks": 0, "coverage_percent": 0, "top_subjects_by_chunks": [], "recommendation": "MongoDB unavailable"}
        total_chunks = await db.chunks.count_documents({})
        pipeline = [
            {"$group": {"_id": "$subject_id", "count": {"$sum": 1}, "avg_size": {"$avg": "$char_count"}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        chunks_by_subject = await db.chunks.aggregate(pipeline).to_list(10)
        if chunks_by_subject:
            subject_ids = [item["_id"] for item in chunks_by_subject if item["_id"]]
            subjects = await db.subjects.find({"id": {"$in": subject_ids}}, {"_id": 0, "id": 1, "name": 1}).to_list(20)
            subject_map = {s["id"]: s["name"] for s in subjects}
            for item in chunks_by_subject:
                item["subject_name"] = subject_map.get(item["_id"], "Unknown")
        total_chapters = await db.chapters.count_documents({})
        chapters_with_content = await db.chapters.count_documents({"content": {"$exists": True, "$ne": ""}})
        chunked_chapter_ids = await db.chunks.distinct("chapter_id")
        chapters_with_chunks = len(chunked_chapter_ids)
        chapters_without_chunks = chapters_with_content - chapters_with_chunks
        return {
            "total_chunks": total_chunks,
            "total_chapters": total_chapters,
            "chapters_with_content": chapters_with_content,
            "chapters_with_chunks": chapters_with_chunks,
            "chapters_without_chunks": chapters_without_chunks,
            "coverage_percent": round((chapters_with_chunks / chapters_with_content * 100) if chapters_with_content > 0 else 0, 1),
            "top_subjects_by_chunks": chunks_by_subject,
            "recommendation": "Run /admin/content/bulk-rechunk to chunk all chapters" if chapters_without_chunks > 0 else "All content is chunked"
        }
    except Exception:
        mark_mongo_down()
        return {"total_chunks": 0, "total_chapters": 0, "chapters_with_content": 0, "chapters_with_chunks": 0, "chapters_without_chunks": 0, "coverage_percent": 0, "top_subjects_by_chunks": [], "recommendation": "MongoDB unavailable"}



@router.patch("/admin/content/uploads/{content_id}")
async def update_content_upload(content_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    """Update uploaded content metadata"""
    allowed = {k: v for k, v in data.items() if k in ["title", "description", "content", "tags", "year", "exam_type", "category", "order", "status"]}
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    result = await db.content_uploads.update_one({"id": content_id}, {"$set": allowed})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Content not found")
    
    updated = await db.content_uploads.find_one({"id": content_id}, {"_id": 0})
    return updated



@router.delete("/admin/content/chapters/{chapter_id}")
async def admin_delete_chapter(chapter_id: str, admin: dict = Depends(get_admin_user)):
    """Delete chapter"""
    chapter = await db.chapters.find_one({"id": chapter_id})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    
    try:
        import server as _srv
        if _srv._syllabus_embedder:
            await _srv._syllabus_embedder.remove_chapter_embeddings(chapter_id)
    except Exception:
        pass
    await db.chapters.delete_one({"id": chapter_id})
    if chapter.get("subject_id"):
        await db.subjects.update_one(
            {"id": chapter["subject_id"]},
            {"$inc": {"chapter_count": -1}}
        )
    _invalidate_content_cache("chapters")
    _invalidate_content_cache("subjects")
    return {"message": "Chapter deleted"}

@router.post("/admin/content/chunks/backfill-embeddings")
async def admin_backfill_chunk_embeddings(
    batch_size: int = Query(10, ge=1, le=50),
    delay: float = Query(1.0, ge=0.0, le=10.0),
    admin: dict = Depends(get_admin_user),
):
    """
    Backfill embeddings for all chunks that don't have one yet.
    Processes in batches with rate limiting to avoid API quota issues.

    Query params:
    - batch_size: Number of chunks to embed per batch (default: 10, max: 50)
    - delay: Seconds to wait between batches (default: 1.0)
    """
    try:
        result = await backfill_chunk_embeddings(batch_size=batch_size, delay=delay)
        return result
    except Exception as e:
        logger.error(f"Chunk embedding backfill failed: {e}")
        raise HTTPException(status_code=500, detail=f"Backfill failed: {str(e)}")


@router.get("/admin/content/chunks/embedding-stats")
async def get_chunk_embedding_stats(admin: dict = Depends(get_admin_user)):
    """Get stats about chunk embeddings coverage."""
    try:
        total_chunks = await db.chunks.count_documents({})
        _needs_embedding = {
            "$or": [
                {"embedding": {"$exists": False}},
                {"embedding": None},
                {"embedding": []},
            ]
        }
        without_embeddings = await db.chunks.count_documents(_needs_embedding)
        with_embeddings = total_chunks - without_embeddings
        return {
            "total_chunks": total_chunks,
            "with_embeddings": with_embeddings,
            "without_embeddings": without_embeddings,
            "coverage_percent": round((with_embeddings / total_chunks * 100) if total_chunks > 0 else 0, 1),
        }
    except Exception as e:
        logger.error(f"Chunk embedding stats failed: {e}")
        return {"total_chunks": 0, "with_embeddings": 0, "without_embeddings": 0, "coverage_percent": 0}


@router.post("/admin/reseed-embeddings")
async def admin_reseed_embeddings(
    force: bool = Query(False),
    admin: dict = Depends(get_admin_user),
):
    import server as _srv
    embedder = _srv._syllabus_embedder
    if embedder is None:
        from syllabus_embedder import SyllabusEmbedder
        embedder = SyllabusEmbedder(db)
    result = await embedder.reseed_all(force=force)
    return result


@router.post("/admin/extract-keywords")
async def admin_extract_keywords(admin: dict = Depends(get_admin_user)):
    import re as _re
    stats = {"chapters": 0, "keywords_extracted": 0}
    async for chapter in db.chapters.find({}):
        ch_id = chapter.get("id")
        content = (chapter.get("content") or "").strip()
        if not ch_id or not content:
            continue
        headings = []
        bold_terms = []
        for line in content.split("\n"):
            h_match = _re.match(r'^#{2,3}\s+(.+)', line.strip())
            if h_match:
                heading = h_match.group(1).strip().rstrip(".")
                if len(heading) > 3 and heading.lower() not in ("summary", "introduction", "conclusion", "key points"):
                    headings.append(heading)
            for m in _re.finditer(r'\*\*([^*]{3,50})\*\*', line):
                term = m.group(1).strip().rstrip(".:,")
                if len(term) > 3:
                    bold_terms.append(term)
        keywords = list(dict.fromkeys(headings + bold_terms[:20]))
        if not keywords:
            continue
        for kw in keywords[:25]:
            # Task #349: route through the shared helper so created_at
            # is stamped exactly once via $setOnInsert.
            from seo_writes import upsert_seo_topic
            await upsert_seo_topic(
                db,
                {"linked_chapter_id": ch_id, "topic": kw},
                {
                    "linked_chapter_id": ch_id,
                    "topic": kw,
                    "primary_keyword": kw,
                    "source": "content_extraction",
                },
            )
            stats["keywords_extracted"] += 1
        stats["chapters"] += 1
    return stats


@router.post("/admin/seed")
async def admin_reseed(admin: dict = Depends(get_admin_user)):
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="MongoDB unavailable in production - seed data is pre-loaded")
        global _seeded
        _seeded = False
        await db.boards.delete_many({})
        await db.classes.delete_many({})
        await db.streams.delete_many({})
        await db.subjects.delete_many({})
        await db.chapters.delete_many({})
        await ensure_seeded()
        # Wiped & re-seeded the entire content tree — fire the deploy
        # hook immediately so prerendered pages don't stay stale (Task #398).
        try:
            from routes.admin_content import _trigger_prerender_now
            await _trigger_prerender_now("admin_reseed")
        except Exception as exc:
            logger.warning("admin_reseed: prerender trigger import/call failed: %s", exc)
        return {"message": "Content reseeded successfully"}
    except HTTPException:
        raise
    except Exception as e:
        mark_mongo_down()
        raise HTTPException(status_code=503, detail=f"MongoDB error: {str(e)[:50]}")



# ── Pinecone chunk embedding pipeline ────────────────────────────────────────

@router.post("/admin/vector/embed-chunks-bulk")
async def admin_embed_chunks_bulk(
    force_all: bool = Query(False, description="Re-embed chunks that already have embeddings"),
    subject_id: Optional[str] = Query(None, description="Scope to one subject"),
    limit:      Optional[int] = Query(None, description="Max chunks to process (test mode)"),
    batch_size: int           = Query(48,   description="Chunks per Pinecone API call"),
    admin: dict = Depends(get_admin_user),
):
    """Batch-embed all chunks (or missing ones) using Pinecone multilingual-e5-large.

    This activates MongoDB Atlas Vector Search — chunks without an `embedding`
    field are invisible to $vectorSearch.  Run this after generating chapter
    notes/QA to make content semantically searchable.
    """
    from providers.chunk_embedder import embed_chunks_bulk
    from llm import select_provider
    _embed_provider = select_provider("embed", lang="en")
    logger.info("[embed_dispatch] selected provider=%r for bulk chunk embed run", _embed_provider)
    result = await embed_chunks_bulk(
        db,
        batch_size=batch_size,
        force_all=force_all,
        subject_id=subject_id,
        limit=limit,
    )
    result["provider_selected"] = _embed_provider
    return result


@router.post("/admin/content/translate-assamese-bulk")
async def admin_translate_assamese_bulk(
    limit:         int  = Query(50,   description="Max chapters per run"),
    skip_existing: bool = Query(True, description="Skip chapters with content_as already set"),
    admin: dict = Depends(get_admin_user),
):
    """Translate chapter content to Assamese (content_as field) using Sarvam translate:v1.

    After translation, automatically re-embeds the chapters' chunks bilingually so
    Assamese queries find the right content via Atlas Vector Search.
    """
    from providers.chunk_embedder import translate_chapters_to_assamese
    result = await translate_chapters_to_assamese(
        db,
        limit=limit,
        skip_existing=skip_existing,
    )
    return result


@router.get("/admin/vector/chunks-stats")
async def admin_chunks_stats(admin: dict = Depends(get_admin_user)):
    """Return embedding coverage stats for the chunks collection."""
    total       = await db.chunks.count_documents({})
    embedded    = await db.chunks.count_documents({"embedding": {"$exists": True}})
    unembedded  = total - embedded
    ch_as_total = await db.chapters.count_documents({"status": "published"})
    ch_as_done  = await db.chapters.count_documents({"content_as": {"$exists": True, "$ne": ""}})
    return {
        "chunks": {
            "total":      total,
            "embedded":   embedded,
            "unembedded": unembedded,
            "coverage_pct": round(embedded / total * 100, 1) if total else 0,
        },
        "chapters": {
            "published":      ch_as_total,
            "with_content_as": ch_as_done,
            "missing_as":     ch_as_total - ch_as_done,
            "as_coverage_pct": round(ch_as_done / ch_as_total * 100, 1) if ch_as_total else 0,
        },
    }
