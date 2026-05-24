"""Integration tests for Content CRUD endpoints with mocked MongoDB."""

import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from beanie import PydanticObjectId
from datetime import datetime, timezone

from app.models.content import Board, Class, Stream, Subject, Chapter, Topic


# Shared test data
FAKE_BOARD_ID = PydanticObjectId()
FAKE_CHAPTER_ID = PydanticObjectId()
FAKE_SUBJECT_ID = PydanticObjectId()

ADMIN_PAYLOAD = {"sub": "admin-id", "type": "admin", "role": "admin"}


@pytest_asyncio.fixture
async def client():
    """Create async test client."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _make_mock_board(board_id=None, name="SEBA", slug="seba", status="active"):
    """Create a mock Board object."""
    mock = MagicMock(spec=Board)
    mock.id = board_id or PydanticObjectId()
    mock.name = name
    mock.slug = slug
    mock.status = status
    mock.created_at = datetime.now(timezone.utc)
    mock.updated_at = datetime.now(timezone.utc)
    mock.save = AsyncMock()
    mock.insert = AsyncMock()
    mock.delete = AsyncMock()
    return mock


def _make_mock_chapter(chapter_id=None, title="Cell Biology", slug="cell-biology",
                       status="draft", content_en=None, content_as=None,
                       published_topics=None):
    """Create a mock Chapter object."""
    mock = MagicMock(spec=Chapter)
    mock.id = chapter_id or PydanticObjectId()
    mock.title = title
    mock.slug = slug
    mock.subject_id = FAKE_SUBJECT_ID
    mock.chapter_number = 1
    mock.status = status
    mock.content_en = content_en
    mock.content_as = content_as
    mock.meta_description = None
    mock.keywords = None
    mock.word_count = None
    mock.published_topics = published_topics or []
    mock.faq_jsonld = None
    mock.created_at = datetime.now(timezone.utc)
    mock.updated_at = datetime.now(timezone.utc)
    mock.save = AsyncMock()
    mock.insert = AsyncMock()
    mock.delete = AsyncMock()
    return mock


class TestAuthRequired:
    """Test that admin endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_list_boards_requires_auth(self, client):
        """Request without session cookie should return 401."""
        response = await client.get("/api/v1/admin/content/boards")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_board_requires_auth(self, client):
        """POST without session cookie should return 401."""
        response = await client.post(
            "/api/v1/admin/content/boards",
            json={"name": "Test Board"},
        )
        # 401 from missing session OR 403 from CSRF/origin - both indicate auth enforcement
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_get_chapter_requires_auth(self, client):
        """GET chapter without session cookie should return 401."""
        fake_id = str(PydanticObjectId())
        response = await client.get(f"/api/v1/admin/content/chapters/{fake_id}")
        assert response.status_code == 401


class TestBoardEndpoints:
    """Test Board CRUD endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Board")
    async def test_create_board(self, mock_board_cls, mock_csrf, mock_auth, client):
        """Test board creation with mocked insert."""
        mock_instance = _make_mock_board()
        mock_board_cls.return_value = mock_instance
        mock_instance.insert = AsyncMock()

        response = await client.post(
            "/api/v1/admin/content/boards",
            json={"name": "SEBA"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "SEBA"
        assert "id" in data
        assert "slug" in data

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content.Board")
    async def test_list_boards(self, mock_board_cls, mock_auth, client):
        """Test listing boards."""
        mock_boards = [
            _make_mock_board(name="SEBA", slug="seba"),
            _make_mock_board(name="CBSE", slug="cbse"),
        ]
        mock_find_all = MagicMock()
        mock_find_all.to_list = AsyncMock(return_value=mock_boards)
        mock_board_cls.find_all.return_value = mock_find_all

        response = await client.get("/api/v1/admin/content/boards")
        assert response.status_code == 200
        data = response.json()
        assert "boards" in data
        assert len(data["boards"]) == 2
        assert data["boards"][0]["name"] == "SEBA"
        assert data["boards"][1]["name"] == "CBSE"

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Board")
    async def test_update_board(self, mock_board_cls, mock_csrf, mock_auth, client):
        """Test board update."""
        mock_board = _make_mock_board(board_id=FAKE_BOARD_ID, name="SEBA", slug="seba")
        mock_board_cls.get = AsyncMock(return_value=mock_board)

        response = await client.patch(
            f"/api/v1/admin/content/boards/{FAKE_BOARD_ID}",
            json={"name": "SEBA Updated"},
        )
        assert response.status_code == 200
        mock_board.save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Board")
    async def test_update_board_not_found(self, mock_board_cls, mock_csrf, mock_auth, client):
        """Test board update returns 404 when not found."""
        mock_board_cls.get = AsyncMock(return_value=None)
        fake_id = str(PydanticObjectId())

        response = await client.patch(
            f"/api/v1/admin/content/boards/{fake_id}",
            json={"name": "No Board"},
        )
        assert response.status_code == 404


class TestChapterEndpoints:
    """Test Chapter CRUD endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_create_chapter(self, mock_chapter_cls, mock_csrf, mock_auth, client):
        """Test chapter creation."""
        mock_instance = _make_mock_chapter()
        mock_chapter_cls.return_value = mock_instance
        mock_instance.insert = AsyncMock()

        response = await client.post(
            "/api/v1/admin/content/chapters",
            json={
                "title": "Cell Biology",
                "subject_id": str(FAKE_SUBJECT_ID),
                "chapter_number": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Cell Biology"
        assert "id" in data
        assert "slug" in data

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_get_chapter(self, mock_chapter_cls, mock_auth, client):
        """Test getting a single chapter."""
        mock_chapter = _make_mock_chapter(chapter_id=FAKE_CHAPTER_ID)
        # model_dump for published_topics
        for t in mock_chapter.published_topics:
            t.model_dump = MagicMock(return_value={})
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.get(f"/api/v1/admin/content/chapters/{FAKE_CHAPTER_ID}")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Cell Biology"
        assert data["status"] == "draft"

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_get_chapter_not_found(self, mock_chapter_cls, mock_auth, client):
        """Test get chapter returns 404 when not found."""
        mock_chapter_cls.get = AsyncMock(return_value=None)
        fake_id = str(PydanticObjectId())

        response = await client.get(f"/api/v1/admin/content/chapters/{fake_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_add_topics(self, mock_chapter_cls, mock_csrf, mock_auth, client):
        """Test adding topics to a chapter."""
        mock_chapter = _make_mock_chapter(chapter_id=FAKE_CHAPTER_ID)
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.post(
            f"/api/v1/admin/content/chapters/{FAKE_CHAPTER_ID}/topics",
            json={"topics": [{"title": "Photosynthesis"}, {"title": "Respiration"}]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["added"] == 2
        mock_chapter.save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_save_content_en(self, mock_chapter_cls, mock_csrf, mock_auth, client):
        """Test saving English content."""
        mock_chapter = _make_mock_chapter(chapter_id=FAKE_CHAPTER_ID)
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.put(
            f"/api/v1/admin/content/chapters/{FAKE_CHAPTER_ID}/content/en",
            json={"content": "This is the English content for testing."},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["word_count"] == 7
        mock_chapter.save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_save_content_as(self, mock_chapter_cls, mock_csrf, mock_auth, client):
        """Test saving Assamese content."""
        mock_chapter = _make_mock_chapter(chapter_id=FAKE_CHAPTER_ID)
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.put(
            f"/api/v1/admin/content/chapters/{FAKE_CHAPTER_ID}/content/as",
            json={"content": "Assamese content text here."},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        mock_chapter.save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_get_content(self, mock_chapter_cls, mock_auth, client):
        """Test getting content by language."""
        mock_chapter = _make_mock_chapter(
            chapter_id=FAKE_CHAPTER_ID,
            content_en="English notes here",
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.get(
            f"/api/v1/admin/content/chapters/{FAKE_CHAPTER_ID}/content/en"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["lang"] == "en"
        assert data["content"] == "English notes here"

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_topic_index(self, mock_chapter_cls, mock_auth, client):
        """Test getting topic index for a subject."""
        topic = Topic(title="DNA", topic_slug="dna", definition="Genetic material")
        mock_chapter = _make_mock_chapter(
            chapter_id=FAKE_CHAPTER_ID,
            published_topics=[topic],
        )
        mock_find = MagicMock()
        mock_find.to_list = AsyncMock(return_value=[mock_chapter])
        mock_chapter_cls.find.return_value = mock_find

        response = await client.get(
            f"/api/v1/admin/content/subjects/{FAKE_SUBJECT_ID}/topic-index"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["topics"][0]["title"] == "DNA"
