"""
Hierarchy Sync - Rebuilds hierarchy JSONs in GCS from current MongoDB state.

Called after admin creates/updates boards, classes, streams, subjects.
Also rebuilds the library bundle in GCS.
"""

import logging

from app.services.content.gcs_store import gcs_content_store

logger = logging.getLogger(__name__)


async def sync_hierarchy_to_gcs():
    """Rebuild all hierarchy JSONs in GCS from current MongoDB state."""
    from app.models.content import Board, Class, Stream, Subject, Chapter

    try:
        # Boards
        boards = await Board.find({"status": "active"}).to_list()
        await gcs_content_store.write_hierarchy(
            "boards", [b.model_dump(mode="json") for b in boards]
        )
        logger.info(f"Synced {len(boards)} boards to GCS")

        # Classes
        classes = await Class.find({"status": "active"}).to_list()
        await gcs_content_store.write_hierarchy(
            "classes", [c.model_dump(mode="json") for c in classes]
        )
        logger.info(f"Synced {len(classes)} classes to GCS")

        # Streams
        streams = await Stream.find({"status": "active"}).to_list()
        await gcs_content_store.write_hierarchy(
            "streams", [s.model_dump(mode="json") for s in streams]
        )
        logger.info(f"Synced {len(streams)} streams to GCS")

        # Subjects
        subjects = await Subject.find({"status": "active"}).to_list()
        await gcs_content_store.write_hierarchy(
            "subjects", [s.model_dump(mode="json") for s in subjects]
        )
        logger.info(f"Synced {len(subjects)} subjects to GCS")

        # Chapters (published only)
        chapters = await Chapter.find({"status": "published"}).to_list()
        await gcs_content_store.write_hierarchy(
            "chapters", [ch.model_dump(mode="json") for ch in chapters]
        )
        logger.info(f"Synced {len(chapters)} published chapters to GCS")

        return {
            "status": "success",
            "counts": {
                "boards": len(boards),
                "classes": len(classes),
                "streams": len(streams),
                "subjects": len(subjects),
                "chapters": len(chapters),
            },
        }
    except Exception as e:
        logger.error(f"Hierarchy sync to GCS failed: {e}")
        return {"status": "error", "detail": str(e)}
