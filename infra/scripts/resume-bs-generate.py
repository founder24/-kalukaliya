#!/usr/bin/env python3
"""Resume Business Studies note generation — skips already-published chapters."""
import asyncio, logging, sys
sys.path.insert(0, "apps/backend")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

async def main():
    from app.db.mongo import init_mongo
    from app.models.content import Subject, Chapter
    from app.services.content_generation import content_generation_service
    await init_mongo()

    subj = await Subject.find_one({"slug": "business-studies"})
    chs = await Chapter.find({"subject_id": subj.id}).to_list(None)
    chs.sort(key=lambda c: c.chapter_number or 99)

    drafts = [c for c in chs if c.status != "published"]
    log.info(f"Found {len(drafts)} draft chapters to process out of {len(chs)} total")

    ok = err = 0
    for c in drafts:
        log.info(f"[{c.chapter_number:02d}/11] Generating: {c.title!r}")
        try:
            result = await content_generation_service.generate_notes(str(c.id), force=True)
            pr = getattr(result, "_publish_result", {})
            log.info(
                f"  ✓ status={result.status}  en={len((result.content_en or '').split())}w"
                f"  gcs={pr.get('gcs',{}).get('status','?')}"
                f"  vtx={pr.get('vertex_search',{}).get('status','?')}"
                f"({pr.get('vertex_search',{}).get('chunks',0)}c+"
                f"{pr.get('vertex_search',{}).get('topic_docs',0)}t)"
                f"  emb={pr.get('topic_embeddings',{}).get('count',0)}"
            )
            ok += 1
        except Exception as e:
            log.error(f"  ✗ FAILED: {e}")
            err += 1

    log.info(f"\nDone. Published={ok}  Errors={err}")

asyncio.run(main())
