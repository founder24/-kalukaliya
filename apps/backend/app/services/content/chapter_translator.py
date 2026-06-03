"""
ChapterTranslator — Translates Chapter content_en → content_as and
title → title_as using Sarvam AI.

Used by the /admin/corpus/assamese/backfill endpoint.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.models.content import Chapter
from app.services.ai.sarvam_client import sarvam_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a translator for educational content. Translate the following "
    "English text to Assamese (অসমীয়া). "
    "Preserve all mathematical formulas, chemical equations, proper nouns, "
    "and technical terms in English. Maintain the markdown formatting exactly. "
    "The translation should be natural and academically appropriate for "
    "college students in Assam. Only output the translation, nothing else."
)

TITLE_SYSTEM_PROMPT = (
    "Translate this English chapter title to Assamese (অসমীয়া). "
    "Keep any technical terms, unit codes, and acronyms in English. "
    "Output only the translated title, nothing else."
)

CHUNK_WORD_LIMIT = 700


class ChapterTranslator:
    """Translates Chapter documents from English to Assamese via Sarvam AI."""

    async def _translate(self, system_prompt: str, text: str, retries: int = 3) -> str:
        if not text or not text.strip():
            return text
        for attempt in range(retries):
            try:
                return await sarvam_client.generate(system_prompt, text)
            except Exception as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(f"Sarvam attempt {attempt+1} failed: {e}. Retry in {wait}s")
                await asyncio.sleep(wait)
        raise RuntimeError("Sarvam translation failed after all retries")

    def _chunk(self, text: str) -> list[str]:
        paragraphs = text.split("\n\n")
        chunks, cur, cur_words = [], [], 0
        for para in paragraphs:
            pw = len(para.split())
            if cur_words + pw > CHUNK_WORD_LIMIT and cur:
                chunks.append("\n\n".join(cur))
                cur, cur_words = [para], pw
            else:
                cur.append(para)
                cur_words += pw
        if cur:
            chunks.append("\n\n".join(cur))
        return chunks

    async def translate_markdown(self, text: str) -> str:
        words = text.split()
        if len(words) <= CHUNK_WORD_LIMIT:
            return await self._translate(SYSTEM_PROMPT, text)
        chunks = self._chunk(text)
        translated = []
        for chunk in chunks:
            translated.append(await self._translate(SYSTEM_PROMPT, chunk))
        return "\n\n".join(translated)

    async def translate_chapter(self, chapter: Chapter) -> bool:
        """
        Translate a single chapter in-place (updates the DB document).
        Returns True on success, False on failure.
        """
        try:
            title_as = await self._translate(TITLE_SYSTEM_PROMPT, chapter.title)
            content_as = await self.translate_markdown(chapter.content_en or "")

            await chapter.update({
                "$set": {
                    "title_as": title_as.strip(),
                    "content_as": content_as,
                    "updated_at": datetime.now(timezone.utc),
                }
            })
            logger.info(f"Translated chapter: {chapter.slug}")
            return True
        except Exception as e:
            logger.error(f"Failed to translate chapter {chapter.slug}: {e}")
            return False

    async def bulk_translate(
        self,
        app_state,
        max_docs: int = 100,
        force: bool = False,
    ) -> dict:
        """
        Translate English Chapter documents that have content_en but no content_as.
        Updates app_state.corpus_assamese_progress with live progress.
        """
        results = {
            "collection": "chapters",
            "translated": 0,
            "skipped": 0,
            "failed": 0,
            "remaining": 0,
            "duration_s": 0,
            "reject_reasons": {},
        }
        started = datetime.now(timezone.utc)

        query = {"content_en": {"$nin": [None, ""]}}
        if not force:
            query["$or"] = [
                {"content_as": None},
                {"content_as": ""},
                {"content_as": {"$exists": False}},
            ]

        chapters = await Chapter.find(query).limit(max_docs).to_list()
        total = len(chapters)

        total_with_en = await Chapter.find(
            {"content_en": {"$nin": [None, ""]}}
        ).count()
        total_missing_as = await Chapter.find({
            "content_en": {"$nin": [None, ""]},
            "$or": [
                {"content_as": None},
                {"content_as": ""},
                {"content_as": {"$exists": False}},
            ],
        }).count()

        try:
            coll_progress = getattr(app_state, "corpus_assamese_progress", {})
            coll_progress["chapters"] = {
                "running": True,
                "total": total_with_en,
                "remaining": total_missing_as,
                "done": 0,
            }
            app_state.corpus_assamese_progress = coll_progress

            for i, chapter in enumerate(chapters):
                if not chapter.content_en:
                    results["skipped"] += 1
                    results["reject_reasons"]["no_content_en"] = (
                        results["reject_reasons"].get("no_content_en", 0) + 1
                    )
                    continue

                ok = await self.translate_chapter(chapter)
                if ok:
                    results["translated"] += 1
                else:
                    results["failed"] += 1

                coll_progress["chapters"]["done"] = i + 1
                coll_progress["chapters"]["remaining"] = max(0, total_missing_as - results["translated"])
                app_state.corpus_assamese_progress = coll_progress

                await asyncio.sleep(1.2)

        finally:
            finished = datetime.now(timezone.utc)
            results["duration_s"] = (finished - started).total_seconds()

            remaining_after = await Chapter.find({
                "content_en": {"$nin": [None, ""]},
                "$or": [
                    {"content_as": None},
                    {"content_as": ""},
                    {"content_as": {"$exists": False}},
                ],
            }).count()
            results["remaining"] = remaining_after

            coll_progress["chapters"]["running"] = False
            coll_progress["chapters"]["remaining"] = remaining_after
            app_state.corpus_assamese_progress = coll_progress

        return results


chapter_translator = ChapterTranslator()
