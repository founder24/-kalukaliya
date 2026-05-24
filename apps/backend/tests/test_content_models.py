"""Unit tests for content hierarchy models (no DB required)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from beanie import PydanticObjectId

from app.models.content import Board, Class, Stream, Subject, Chapter, Topic
from app.api.v1.admin_content import _slugify


@pytest.fixture(autouse=True)
def mock_beanie_collections():
    """Patch Beanie collection initialization for all Document models."""
    with (
        patch.object(Board, "get_pymongo_collection", return_value=MagicMock()),
        patch.object(Class, "get_pymongo_collection", return_value=MagicMock()),
        patch.object(Stream, "get_pymongo_collection", return_value=MagicMock()),
        patch.object(Subject, "get_pymongo_collection", return_value=MagicMock()),
        patch.object(Chapter, "get_pymongo_collection", return_value=MagicMock()),
    ):
        yield


class TestSlugify:
    """Test the _slugify helper function."""

    def test_basic_slugify(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_characters_removed(self):
        assert _slugify("Hello! World@#$") == "hello-world"

    def test_multiple_spaces_collapsed(self):
        assert _slugify("Hello   World") == "hello-world"

    def test_leading_trailing_stripped(self):
        assert _slugify("  Hello World  ") == "hello-world"

    def test_underscores_become_hyphens(self):
        assert _slugify("hello_world") == "hello-world"

    def test_multiple_hyphens_collapsed(self):
        assert _slugify("hello---world") == "hello-world"

    def test_mixed_case_lowered(self):
        assert _slugify("SEBA Board Class 10") == "seba-board-class-10"

    def test_empty_string(self):
        assert _slugify("") == ""

    def test_only_special_chars(self):
        assert _slugify("!@#$%") == ""


class TestBoardModel:
    """Test Board model instantiation and defaults."""

    def test_board_creation_with_required_fields(self):
        board = Board(name="SEBA", slug="seba")
        assert board.name == "SEBA"
        assert board.slug == "seba"

    def test_board_default_status(self):
        board = Board(name="SEBA", slug="seba")
        assert board.status == "active"

    def test_board_custom_status(self):
        board = Board(name="CBSE", slug="cbse", status="inactive")
        assert board.status == "inactive"

    def test_board_timestamps_set(self):
        before = datetime.now(timezone.utc)
        board = Board(name="SEBA", slug="seba")
        after = datetime.now(timezone.utc)
        assert before <= board.created_at <= after
        assert before <= board.updated_at <= after

    def test_board_settings_name(self):
        assert Board.Settings.name == "boards"


class TestClassModel:
    """Test Class model instantiation."""

    def test_class_creation(self):
        board_id = PydanticObjectId()
        cls = Class(name="Class 10", board_id=board_id)
        assert cls.name == "Class 10"
        assert cls.board_id == board_id

    def test_class_default_status(self):
        cls = Class(name="Class 10", board_id=PydanticObjectId())
        assert cls.status == "active"

    def test_class_timestamps_set(self):
        cls = Class(name="Class 10", board_id=PydanticObjectId())
        assert cls.created_at is not None
        assert cls.updated_at is not None

    def test_class_settings_name(self):
        assert Class.Settings.name == "classes"


class TestStreamModel:
    """Test Stream model instantiation."""

    def test_stream_creation(self):
        class_id = PydanticObjectId()
        stream = Stream(name="Science", class_id=class_id)
        assert stream.name == "Science"
        assert stream.class_id == class_id

    def test_stream_default_status(self):
        stream = Stream(name="Science", class_id=PydanticObjectId())
        assert stream.status == "active"

    def test_stream_timestamps_set(self):
        stream = Stream(name="Arts", class_id=PydanticObjectId())
        assert stream.created_at is not None
        assert stream.updated_at is not None

    def test_stream_settings_name(self):
        assert Stream.Settings.name == "streams"


class TestSubjectModel:
    """Test Subject model instantiation."""

    def test_subject_creation(self):
        stream_id = PydanticObjectId()
        subject = Subject(name="Physics", stream_id=stream_id)
        assert subject.name == "Physics"
        assert subject.stream_id == stream_id

    def test_subject_default_status(self):
        subject = Subject(name="Chemistry", stream_id=PydanticObjectId())
        assert subject.status == "active"

    def test_subject_timestamps_set(self):
        subject = Subject(name="Biology", stream_id=PydanticObjectId())
        assert subject.created_at is not None
        assert subject.updated_at is not None

    def test_subject_settings_name(self):
        assert Subject.Settings.name == "subjects"


class TestTopicModel:
    """Test Topic embedded model creation."""

    def test_topic_creation(self):
        topic = Topic(title="Photosynthesis", topic_slug="photosynthesis")
        assert topic.title == "Photosynthesis"
        assert topic.topic_slug == "photosynthesis"

    def test_topic_auto_generated_id(self):
        topic = Topic(title="Respiration", topic_slug="respiration")
        assert topic.id is not None
        assert len(topic.id) > 0

    def test_topic_unique_ids(self):
        t1 = Topic(title="Topic A", topic_slug="topic-a")
        t2 = Topic(title="Topic B", topic_slug="topic-b")
        assert t1.id != t2.id

    def test_topic_optional_definition(self):
        topic = Topic(title="Cell", topic_slug="cell")
        assert topic.definition is None

    def test_topic_with_definition(self):
        topic = Topic(
            title="Cell",
            topic_slug="cell",
            definition="Basic unit of life",
        )
        assert topic.definition == "Basic unit of life"

    def test_topic_default_definition_status(self):
        topic = Topic(title="Atom", topic_slug="atom")
        assert topic.definition_status == "pending"

    def test_topic_slug_from_slugify(self):
        slug = _slugify("Photosynthesis in Plants")
        topic = Topic(title="Photosynthesis in Plants", topic_slug=slug)
        assert topic.topic_slug == "photosynthesis-in-plants"


class TestChapterModel:
    """Test Chapter model instantiation."""

    def test_chapter_creation_with_required_fields(self):
        subject_id = PydanticObjectId()
        chapter = Chapter(
            title="Cell Biology",
            slug="cell-biology",
            subject_id=subject_id,
            chapter_number=1,
        )
        assert chapter.title == "Cell Biology"
        assert chapter.slug == "cell-biology"
        assert chapter.subject_id == subject_id
        assert chapter.chapter_number == 1

    def test_chapter_default_status(self):
        chapter = Chapter(
            title="Test",
            slug="test",
            subject_id=PydanticObjectId(),
            chapter_number=1,
        )
        assert chapter.status == "draft"

    def test_chapter_default_empty_published_topics(self):
        chapter = Chapter(
            title="Test",
            slug="test",
            subject_id=PydanticObjectId(),
            chapter_number=1,
        )
        assert chapter.published_topics == []

    def test_chapter_optional_content_fields(self):
        chapter = Chapter(
            title="Test",
            slug="test",
            subject_id=PydanticObjectId(),
            chapter_number=1,
        )
        assert chapter.content_en is None
        assert chapter.content_as is None
        assert chapter.meta_description is None
        assert chapter.keywords is None
        assert chapter.word_count is None
        assert chapter.faq_jsonld is None

    def test_chapter_with_content(self):
        chapter = Chapter(
            title="Genetics",
            slug="genetics",
            subject_id=PydanticObjectId(),
            chapter_number=5,
            content_en="English content here",
            content_as="Assamese content here",
            meta_description="Learn about genetics",
            keywords="genetics, DNA, RNA",
            word_count=1500,
        )
        assert chapter.content_en == "English content here"
        assert chapter.content_as == "Assamese content here"
        assert chapter.meta_description == "Learn about genetics"
        assert chapter.keywords == "genetics, DNA, RNA"
        assert chapter.word_count == 1500

    def test_chapter_with_topics(self):
        topics = [
            Topic(title="DNA", topic_slug="dna"),
            Topic(title="RNA", topic_slug="rna"),
        ]
        chapter = Chapter(
            title="Genetics",
            slug="genetics",
            subject_id=PydanticObjectId(),
            chapter_number=5,
            published_topics=topics,
        )
        assert len(chapter.published_topics) == 2
        assert chapter.published_topics[0].title == "DNA"
        assert chapter.published_topics[1].title == "RNA"

    def test_chapter_timestamps_set(self):
        chapter = Chapter(
            title="Test",
            slug="test",
            subject_id=PydanticObjectId(),
            chapter_number=1,
        )
        assert chapter.created_at is not None
        assert chapter.updated_at is not None

    def test_chapter_settings_name(self):
        assert Chapter.Settings.name == "chapters"

    def test_chapter_with_faq_jsonld(self):
        faq = [{"@type": "Question", "name": "What is DNA?", "acceptedAnswer": {"@type": "Answer", "text": "DNA is..."}}]
        chapter = Chapter(
            title="Test",
            slug="test",
            subject_id=PydanticObjectId(),
            chapter_number=1,
            faq_jsonld=faq,
        )
        assert chapter.faq_jsonld == faq
