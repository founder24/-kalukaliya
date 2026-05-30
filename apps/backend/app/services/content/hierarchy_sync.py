"""
Hierarchy Sync - Writes the full content hierarchy (boards, classes, streams, subjects)
and the library bundle JSON to GCS for static site generation.
"""

import logging
import re

from app.models.content import Board, Chapter, Class, Stream, Subject
from app.services.content.gcs_store import gcs_content_store

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Generate a URL-friendly slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


async def sync_hierarchy_to_gcs() -> dict:
    """
    Read all active hierarchy entities from MongoDB and write them to GCS.

    Writes individual entity files and rebuilds the library-bundle.json.
    Returns a summary dict of what was synced.
    """
    if not gcs_content_store._configured:
        logger.warning("GCS content store not configured, skipping hierarchy sync")
        return {"status": "skipped", "reason": "not_configured"}

    try:
        # Fetch all active entities
        boards = await Board.find({"status": "active"}).to_list()
        classes = await Class.find({"status": "active"}).to_list()
        streams = await Stream.find({"status": "active"}).to_list()
        subjects = await Subject.find({"status": "active"}).to_list()
        chapters = await Chapter.find_all().to_list()

        # Write individual hierarchy entities
        for board in boards:
            await gcs_content_store.write_hierarchy(
                "boards",
                str(board.id),
                {"id": str(board.id), "name": board.name, "slug": board.slug},
            )

        for cls in classes:
            await gcs_content_store.write_hierarchy(
                "classes",
                str(cls.id),
                {
                    "id": str(cls.id),
                    "name": cls.name,
                    "slug": _slugify(cls.name),
                    "board_id": str(cls.board_id),
                },
            )

        for stream in streams:
            await gcs_content_store.write_hierarchy(
                "streams",
                str(stream.id),
                {
                    "id": str(stream.id),
                    "name": stream.name,
                    "slug": _slugify(stream.name),
                    "class_id": str(stream.class_id),
                },
            )

        for subj in subjects:
            await gcs_content_store.write_hierarchy(
                "subjects",
                str(subj.id),
                {
                    "id": str(subj.id),
                    "name": subj.name,
                    "slug": _slugify(subj.name),
                    "stream_id": str(subj.stream_id),
                },
            )

        # Build library bundle (same structure as public_content.py get_library_bundle)
        classes_by_board: dict[str, list] = {}
        for cls in classes:
            key = str(cls.board_id)
            classes_by_board.setdefault(key, []).append(cls)

        streams_by_class: dict[str, list] = {}
        for stream in streams:
            key = str(stream.class_id)
            streams_by_class.setdefault(key, []).append(stream)

        subjects_by_stream: dict[str, list] = {}
        for subj in subjects:
            key = str(subj.stream_id)
            subjects_by_stream.setdefault(key, []).append(subj)

        chapters_by_subject: dict[str, list] = {}
        for ch in chapters:
            key = str(ch.subject_id)
            chapters_by_subject.setdefault(key, []).append(ch)

        result_boards = []
        for board in boards:
            board_id = str(board.id)
            board_classes = classes_by_board.get(board_id, [])

            result_classes = []
            for cls in board_classes:
                cls_id = str(cls.id)
                cls_streams = streams_by_class.get(cls_id, [])

                result_streams = []
                for stream in cls_streams:
                    stream_id = str(stream.id)
                    stream_subjects = subjects_by_stream.get(stream_id, [])

                    result_subjects = []
                    for subj in stream_subjects:
                        subj_id = str(subj.id)
                        subj_chapters = chapters_by_subject.get(subj_id, [])
                        subj_chapters.sort(key=lambda c: c.chapter_number)

                        chapter_list = []
                        for ch in subj_chapters:
                            ch_data = {
                                "id": str(ch.id),
                                "title": ch.title,
                                "slug": ch.slug,
                                "order": ch.chapter_number,
                                "topic_count": len(ch.published_topics or []),
                            }
                            chapter_list.append(ch_data)

                        result_subjects.append(
                            {
                                "id": subj_id,
                                "name": subj.name,
                                "slug": _slugify(subj.name),
                                "chapter_count": len(subj_chapters),
                                "chapters": chapter_list,
                            }
                        )

                    result_streams.append(
                        {
                            "id": stream_id,
                            "name": stream.name,
                            "slug": _slugify(stream.name),
                            "subjects": result_subjects,
                        }
                    )

                result_classes.append(
                    {
                        "id": cls_id,
                        "name": cls.name,
                        "slug": _slugify(cls.name),
                        "streams": result_streams,
                    }
                )

            result_boards.append(
                {
                    "id": board_id,
                    "name": board.name,
                    "slug": board.slug,
                    "classes": result_classes,
                }
            )

        library_bundle = {"boards": result_boards}
        await gcs_content_store.write_library_bundle(library_bundle)

        # Build slim library bundle (same structure but subjects exclude chapters)
        slim_boards = []
        for board_data in result_boards:
            slim_board = {**board_data, "classes": []}
            for cls_data in board_data["classes"]:
                slim_cls = {**cls_data, "streams": []}
                for stream_data in cls_data["streams"]:
                    slim_stream = {**stream_data, "subjects": []}
                    for subj_data in stream_data["subjects"]:
                        slim_subj = {
                            "id": subj_data["id"],
                            "name": subj_data["name"],
                            "slug": subj_data["slug"],
                            "chapter_count": subj_data["chapter_count"],
                        }
                        slim_stream["subjects"].append(slim_subj)
                    slim_cls["streams"].append(slim_stream)
                slim_board["classes"].append(slim_cls)
            slim_boards.append(slim_board)

        library_bundle_slim = {"boards": slim_boards}
        await gcs_content_store.write_library_bundle_slim(library_bundle_slim)

        summary = {
            "status": "synced",
            "boards": len(boards),
            "classes": len(classes),
            "streams": len(streams),
            "subjects": len(subjects),
            "chapters": len(chapters),
        }
        logger.info(f"Hierarchy sync complete: {summary}")
        return summary

    except Exception as e:
        logger.error(f"Hierarchy sync failed: {e}")
        return {"status": "error", "detail": str(e)}
