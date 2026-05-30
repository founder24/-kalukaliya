"""Tests for ContentPublisherService hierarchy enrichment and topic micro-documents."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.content import Board, Chapter, Class, Stream, Subject, Topic
from app.services.content_publisher import ContentPublisherService


# ---- Fixtures ----


def _make_topic(id="topic-1", title="Osmosis", definition="Movement of water through a membrane", slug="osmosis"):
    return Topic(
        id=id,
        title=title,
        definition=definition,
        topic_slug=slug,
        definition_status="approved",
    )


def _make_chapter(subject_id="subject-123", topics=None):
    chapter = MagicMock(spec=Chapter)
    chapter.id = "chapter-abc"
    chapter.title = "Cell Biology"
    chapter.slug = "cell-biology"
    chapter.subject_id = subject_id
    chapter.content_en = "Cells are the basic unit of life."
    chapter.meta_description = "Learn about cells"
    chapter.keywords = "cell, biology, life"
    chapter.published_topics = topics if topics is not None else [_make_topic()]
    return chapter


def _make_hierarchy():
    """Create mock Board, Class, Stream, Subject."""
    board = MagicMock(spec=Board)
    board.name = "AHSEC"

    cls = MagicMock(spec=Class)
    cls.name = "Class 12"
    cls.board_id = "board-1"

    stream = MagicMock(spec=Stream)
    stream.name = "Science"
    stream.class_id = "class-1"

    subject = MagicMock(spec=Subject)
    subject.name = "Biology"
    subject.stream_id = "stream-1"

    return board, cls, stream, subject


# ---- _resolve_hierarchy tests ----


@pytest.mark.anyio
async def test_resolve_hierarchy_returns_full_chain():
    """Test that _resolve_hierarchy returns correct values when all documents exist."""
    board, cls, stream, subject = _make_hierarchy()
    chapter = _make_chapter()

    service = ContentPublisherService()

    with (
        patch("app.models.content.Subject.get", new_callable=AsyncMock, return_value=subject),
        patch("app.models.content.Stream.get", new_callable=AsyncMock, return_value=stream),
        patch("app.models.content.Class.get", new_callable=AsyncMock, return_value=cls),
        patch("app.models.content.Board.get", new_callable=AsyncMock, return_value=board),
    ):
        result = await service._resolve_hierarchy(chapter)

    assert result["subject"] == subject
    assert result["stream"] == stream
    assert result["cls"] == cls
    assert result["board"] == board


@pytest.mark.anyio
async def test_resolve_hierarchy_handles_missing_subject():
    """Test that _resolve_hierarchy handles None subject gracefully.

    Note: subject_id is non-optional on the Chapter model in production, so
    subject_id=None cannot occur with valid documents. This guard exists for
    defensive programming and the test validates that robustness.
    """
    chapter = _make_chapter(subject_id=None)

    service = ContentPublisherService()

    with (
        patch("app.models.content.Subject.get", new_callable=AsyncMock) as mock_subject_get,
    ):
        result = await service._resolve_hierarchy(chapter)

    mock_subject_get.assert_not_called()
    assert result["subject"] is None
    assert result["stream"] is None
    assert result["cls"] is None
    assert result["board"] is None


@pytest.mark.anyio
async def test_resolve_hierarchy_handles_missing_stream():
    """Test that _resolve_hierarchy handles subject with no stream_id."""
    subject = MagicMock(spec=Subject)
    subject.name = "Biology"
    subject.stream_id = None

    chapter = _make_chapter()
    service = ContentPublisherService()

    with (
        patch("app.models.content.Subject.get", new_callable=AsyncMock, return_value=subject),
        patch("app.models.content.Stream.get", new_callable=AsyncMock) as mock_stream_get,
    ):
        result = await service._resolve_hierarchy(chapter)

    mock_stream_get.assert_not_called()
    assert result["subject"] == subject
    assert result["stream"] is None
    assert result["cls"] is None
    assert result["board"] is None


@pytest.mark.anyio
async def test_publish_skips_empty_hierarchy_segments():
    """Verify hierarchy string filters out empty segments when ancestors are None."""
    # Only subject is present; stream, cls, board are all None
    subject = MagicMock(spec=Subject)
    subject.name = "Biology"
    subject.stream_id = None

    chapter = _make_chapter()

    service = ContentPublisherService()

    captured_structs = []

    from google.protobuf import struct_pb2

    class FakeDocument:
        def __init__(self, **kwargs):
            self.id = kwargs.get("id", "")
            self.struct_data = kwargs.get("struct_data")
            self.name = ""

    class FakeUpdateRequest:
        def __init__(self, **kwargs):
            self.document = kwargs.get("document")
            self.allow_missing = kwargs.get("allow_missing", False)

    mock_discoveryengine = MagicMock()
    mock_discoveryengine.Document = FakeDocument
    mock_discoveryengine.UpdateDocumentRequest = FakeUpdateRequest

    def capture_update(request):
        struct_dict = dict(request.document.struct_data)
        captured_structs.append(struct_dict)
        return MagicMock()

    mock_client = MagicMock()
    mock_client.branch_path.return_value = "projects/p/locations/l/dataStores/ds/branches/default_branch"
    mock_client.update_document = capture_update

    import sys
    with (
        patch.object(service, "_get_vertex_client", return_value=mock_client),
        patch.object(service, "_resolve_hierarchy", new_callable=AsyncMock, return_value={
            "subject": subject, "stream": None, "cls": None, "board": None
        }),
        patch("app.services.content_publisher.settings") as mock_settings,
        patch.dict(sys.modules, {"google.cloud.discoveryengine_v1": mock_discoveryengine, "google.cloud": MagicMock(discoveryengine_v1=mock_discoveryengine)}),
        patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=lambda fn, **kwargs: fn(**kwargs)),
    ):
        mock_settings.VERTEX_PROJECT_ID = "test-project"
        mock_settings.GOOGLE_APPLICATION_CREDENTIALS_JSON = '{"key": "val"}'
        mock_settings.VERTEX_SEARCH_DATASTORE_ID = "test-ds"
        mock_settings.VERTEX_SEARCH_LOCATION = "global"

        result = await service.publish_to_vertex_search(chapter)

    assert result["status"] == "uploaded"
    chunk_struct = captured_structs[0]
    # Should NOT have leading " > " or empty segments like " > > > Biology > Cell Biology"
    assert chunk_struct["hierarchy"] == "Biology > Cell Biology"
    assert " >  >" not in chunk_struct["hierarchy"]
    assert chunk_struct["hierarchy"].startswith("Biology")

    # Topic micro-doc hierarchy should also be clean
    topic_structs = [s for s in captured_structs if s.get("is_topic_doc") == "true"]
    assert topic_structs[0]["hierarchy"] == "Biology > Cell Biology > Osmosis"


@pytest.mark.anyio
async def test_publish_no_content_skips_topic_micro_docs():
    """Verify topic micro-docs are NOT uploaded when content_en is empty."""
    board, cls, stream, subject = _make_hierarchy()
    chapter = _make_chapter()
    chapter.content_en = ""  # Empty content

    service = ContentPublisherService()

    captured_structs = []

    from google.protobuf import struct_pb2

    class FakeDocument:
        def __init__(self, **kwargs):
            self.id = kwargs.get("id", "")
            self.struct_data = kwargs.get("struct_data")
            self.name = ""

    class FakeUpdateRequest:
        def __init__(self, **kwargs):
            self.document = kwargs.get("document")
            self.allow_missing = kwargs.get("allow_missing", False)

    mock_discoveryengine = MagicMock()
    mock_discoveryengine.Document = FakeDocument
    mock_discoveryengine.UpdateDocumentRequest = FakeUpdateRequest

    def capture_update(request):
        struct_dict = dict(request.document.struct_data)
        captured_structs.append(struct_dict)
        return MagicMock()

    mock_client = MagicMock()
    mock_client.branch_path.return_value = "projects/p/locations/l/dataStores/ds/branches/default_branch"
    mock_client.update_document = capture_update

    import sys
    with (
        patch.object(service, "_get_vertex_client", return_value=mock_client),
        patch.object(service, "_resolve_hierarchy", new_callable=AsyncMock, return_value={
            "subject": subject, "stream": stream, "cls": cls, "board": board
        }),
        patch("app.services.content_publisher.settings") as mock_settings,
        patch.dict(sys.modules, {"google.cloud.discoveryengine_v1": mock_discoveryengine, "google.cloud": MagicMock(discoveryengine_v1=mock_discoveryengine)}),
        patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=lambda fn, **kwargs: fn(**kwargs)),
    ):
        mock_settings.VERTEX_PROJECT_ID = "test-project"
        mock_settings.GOOGLE_APPLICATION_CREDENTIALS_JSON = '{"key": "val"}'
        mock_settings.VERTEX_SEARCH_DATASTORE_ID = "test-ds"
        mock_settings.VERTEX_SEARCH_LOCATION = "global"

        result = await service.publish_to_vertex_search(chapter)

    # No documents uploaded, no topic micro-docs uploaded
    assert result["status"] == "no_content"
    assert len(captured_structs) == 0


# ---- publish_to_vertex_search tests ----


@pytest.mark.anyio
async def test_publish_enriches_chunks_with_hierarchy():
    """Verify publish_to_vertex_search enriches chunk struct_data with hierarchy fields."""
    board, cls, stream, subject = _make_hierarchy()
    chapter = _make_chapter()

    service = ContentPublisherService()

    captured_structs = []

    def mock_update_document(request):
        # Capture the struct_data from each document update
        struct_dict = dict(request.document.struct_data)
        captured_structs.append(struct_dict)
        return MagicMock()

    mock_client = MagicMock()
    mock_client.branch_path.return_value = "projects/p/locations/l/dataStores/ds/branches/default_branch"
    mock_client.update_document = mock_update_document

    # Mock google.cloud.discoveryengine_v1 and struct_pb2
    from google.protobuf import struct_pb2

    mock_discoveryengine = MagicMock()
    mock_discoveryengine.Document = MagicMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    mock_discoveryengine.UpdateDocumentRequest = MagicMock(side_effect=lambda **kwargs: MagicMock(**kwargs))

    # Use real struct_pb2.Struct for capturing
    class FakeDocument:
        def __init__(self, **kwargs):
            self.id = kwargs.get("id", "")
            self.struct_data = kwargs.get("struct_data")
            self.name = ""

    class FakeUpdateRequest:
        def __init__(self, **kwargs):
            self.document = kwargs.get("document")
            self.allow_missing = kwargs.get("allow_missing", False)

    mock_discoveryengine.Document = FakeDocument
    mock_discoveryengine.UpdateDocumentRequest = FakeUpdateRequest

    def capture_update(request):
        struct_dict = dict(request.document.struct_data)
        captured_structs.append(struct_dict)
        return MagicMock()

    mock_client.update_document = capture_update

    import sys
    with (
        patch.object(service, "_get_vertex_client", return_value=mock_client),
        patch.object(service, "_resolve_hierarchy", new_callable=AsyncMock, return_value={
            "subject": subject, "stream": stream, "cls": cls, "board": board
        }),
        patch("app.services.content_publisher.settings") as mock_settings,
        patch.dict(sys.modules, {"google.cloud.discoveryengine_v1": mock_discoveryengine, "google.cloud": MagicMock(discoveryengine_v1=mock_discoveryengine)}),
        patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=lambda fn, **kwargs: fn(**kwargs)),
    ):
        mock_settings.VERTEX_PROJECT_ID = "test-project"
        mock_settings.GOOGLE_APPLICATION_CREDENTIALS_JSON = '{"key": "val"}'
        mock_settings.VERTEX_SEARCH_DATASTORE_ID = "test-ds"
        mock_settings.VERTEX_SEARCH_LOCATION = "global"

        result = await service.publish_to_vertex_search(chapter)

    assert result["status"] == "uploaded"
    assert result["chunks"] == 1
    assert result["topic_docs"] == 1

    # The first struct is the chunk, second is the topic micro-doc
    chunk_struct = captured_structs[0]
    assert chunk_struct["subject_name"] == "Biology"
    assert chunk_struct["class_name"] == "Class 12"
    assert chunk_struct["board_name"] == "AHSEC"
    assert chunk_struct["stream_name"] == "Science"
    assert "AHSEC > Class 12 > Science > Biology > Cell Biology" == chunk_struct["hierarchy"]
    assert "Osmosis" in chunk_struct["topics"]
    assert "Osmosis: Movement of water through a membrane" in chunk_struct["topic_definitions"]


@pytest.mark.anyio
async def test_publish_creates_topic_micro_documents():
    """Verify topic micro-documents are created for each published_topic."""
    board, cls, stream, subject = _make_hierarchy()
    topics = [
        _make_topic(id="t1", title="Osmosis", definition="Water movement", slug="osmosis"),
        _make_topic(id="t2", title="Diffusion", definition="Particle spread", slug="diffusion"),
    ]
    chapter = _make_chapter(topics=topics)

    service = ContentPublisherService()

    captured_structs = []

    from google.protobuf import struct_pb2

    class FakeDocument:
        def __init__(self, **kwargs):
            self.id = kwargs.get("id", "")
            self.struct_data = kwargs.get("struct_data")
            self.name = ""

    class FakeUpdateRequest:
        def __init__(self, **kwargs):
            self.document = kwargs.get("document")
            self.allow_missing = kwargs.get("allow_missing", False)

    mock_discoveryengine = MagicMock()
    mock_discoveryengine.Document = FakeDocument
    mock_discoveryengine.UpdateDocumentRequest = FakeUpdateRequest

    def capture_update(request):
        struct_dict = dict(request.document.struct_data)
        captured_structs.append(struct_dict)
        return MagicMock()

    mock_client = MagicMock()
    mock_client.branch_path.return_value = "projects/p/locations/l/dataStores/ds/branches/default_branch"
    mock_client.update_document = capture_update

    import sys
    with (
        patch.object(service, "_get_vertex_client", return_value=mock_client),
        patch.object(service, "_resolve_hierarchy", new_callable=AsyncMock, return_value={
            "subject": subject, "stream": stream, "cls": cls, "board": board
        }),
        patch("app.services.content_publisher.settings") as mock_settings,
        patch.dict(sys.modules, {"google.cloud.discoveryengine_v1": mock_discoveryengine, "google.cloud": MagicMock(discoveryengine_v1=mock_discoveryengine)}),
        patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=lambda fn, **kwargs: fn(**kwargs)),
    ):
        mock_settings.VERTEX_PROJECT_ID = "test-project"
        mock_settings.GOOGLE_APPLICATION_CREDENTIALS_JSON = '{"key": "val"}'
        mock_settings.VERTEX_SEARCH_DATASTORE_ID = "test-ds"
        mock_settings.VERTEX_SEARCH_LOCATION = "global"

        result = await service.publish_to_vertex_search(chapter)

    assert result["topic_docs"] == 2

    # Topic micro-docs are the ones with is_topic_doc = "true"
    topic_structs = [s for s in captured_structs if s.get("is_topic_doc") == "true"]
    assert len(topic_structs) == 2

    osmosis_doc = topic_structs[0]
    assert osmosis_doc["topic_title"] == "Osmosis"
    assert osmosis_doc["topic_slug"] == "osmosis"
    assert osmosis_doc["content"] == "Water movement"
    assert "Osmosis" in osmosis_doc["hierarchy"]
    assert osmosis_doc["subject_name"] == "Biology"

    diffusion_doc = topic_structs[1]
    assert diffusion_doc["topic_title"] == "Diffusion"
    assert diffusion_doc["content"] == "Particle spread"


# ---- chat_service build_system_prompt tests ----


@pytest.mark.anyio
async def test_build_system_prompt_includes_hierarchy():
    """Verify build_system_prompt includes hierarchy in context when available."""
    from app.services.chat_service import ChatService

    chunks = [
        {
            "title": "Cell Biology",
            "content": "Cells are the basic unit of life.",
            "hierarchy": "AHSEC > Class 12 > Science > Biology > Cell Biology",
        }
    ]

    result = ChatService.build_system_prompt("en", chunks)

    assert "[1] Cell Biology (AHSEC > Class 12 > Science > Biology > Cell Biology): Cells are the basic unit of life." in result


@pytest.mark.anyio
async def test_build_system_prompt_omits_empty_hierarchy():
    """Verify build_system_prompt omits hierarchy parenthetical when hierarchy is empty/missing."""
    from app.services.chat_service import ChatService

    chunks = [
        {
            "title": "Cell Biology",
            "content": "Cells are the basic unit of life.",
        }
    ]

    result = ChatService.build_system_prompt("en", chunks)

    assert "[1] Cell Biology: Cells are the basic unit of life." in result
    assert "()" not in result
