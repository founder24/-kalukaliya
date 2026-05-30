"""
Hierarchy Sync - Rebuilds hierarchy JSONs in GCS from current MongoDB state.

Called after admin creates/updates boards, classes, streams, subjects.
Also rebuilds the library bundle in GCS.
"""

import copy
import logging
import re

from app.services.content.gcs_store import gcs_content_store

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert a name to a URL-friendly slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


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

        # Build nested library bundle
        bundle = _build_library_bundle(boards, classes, streams, subjects, chapters)

        # Write full bundle (with chapters)
        await gcs_content_store.write_library_bundle(bundle)
        logger.info("Written derived/library-bundle.json to GCS")

        # Write slim bundle (without chapters array in subjects)
        slim_bundle = _build_slim_bundle(bundle)
        await gcs_content_store.write_library_bundle_slim(slim_bundle)
        logger.info("Written derived/library-bundle-slim.json to GCS")

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


def _build_library_bundle(boards, classes, streams, subjects, chapters) -> dict:
    """Build the nested library bundle: boards > classes > streams > subjects > chapters."""

    # Index chapters by subject_id
    chapters_by_subject = {}
    for ch in chapters:
        sid = str(ch.subject_id)
        chapters_by_subject.setdefault(sid, []).append(ch)

    # Index subjects by stream_id
    subjects_by_stream = {}
    for subj in subjects:
        sid = str(subj.stream_id)
        subjects_by_stream.setdefault(sid, []).append(subj)

    # Index streams by class_id
    streams_by_class = {}
    for st in streams:
        cid = str(st.class_id)
        streams_by_class.setdefault(cid, []).append(st)

    # Index classes by board_id
    classes_by_board = {}
    for cls in classes:
        bid = str(cls.board_id)
        classes_by_board.setdefault(bid, []).append(cls)

    # Build nested structure
    bundle_boards = []
    for board in boards:
        board_id = str(board.id)
        board_classes = []

        for cls in classes_by_board.get(board_id, []):
            class_id = str(cls.id)
            class_streams = []

            for stream in streams_by_class.get(class_id, []):
                stream_id = str(stream.id)
                stream_subjects = []

                for subj in subjects_by_stream.get(stream_id, []):
                    subject_id = str(subj.id)
                    subj_chapters = chapters_by_subject.get(subject_id, [])

                    chapter_list = [
                        {
                            "id": str(ch.id),
                            "title": ch.title,
                            "slug": ch.slug,
                            "order": ch.chapter_number,
                            "topic_count": len(ch.published_topics or []),
                        }
                        for ch in sorted(subj_chapters, key=lambda c: c.chapter_number)
                    ]

                    stream_subjects.append(
                        {
                            "id": str(subj.id),
                            "name": subj.name,
                            "slug": _slugify(subj.name),
                            "chapter_count": len(chapter_list),
                            "chapters": chapter_list,
                        }
                    )

                class_streams.append(
                    {
                        "id": str(stream.id),
                        "name": stream.name,
                        "slug": _slugify(stream.name),
                        "subjects": stream_subjects,
                    }
                )

            board_classes.append(
                {
                    "id": str(cls.id),
                    "name": cls.name,
                    "slug": _slugify(cls.name),
                    "streams": class_streams,
                }
            )

        bundle_boards.append(
            {
                "id": str(board.id),
                "name": board.name,
                "slug": board.slug,
                "classes": board_classes,
            }
        )

    return {"boards": bundle_boards}


def _build_slim_bundle(bundle: dict) -> dict:
    """Build slim bundle - same as full but subjects exclude chapters list."""
    slim = copy.deepcopy(bundle)
    for board in slim["boards"]:
        for cls in board["classes"]:
            for stream in cls["streams"]:
                for subject in stream["subjects"]:
                    subject.pop("chapters", None)
    return slim
