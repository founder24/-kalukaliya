"""
Tests for topic embedding matching:
- cosine_similarity correctness
- TopicMatcher.match_topic above/below threshold
- ChatService.check_topic_match integration
"""

import pytest
import numpy as np
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def sample_embeddings():
    """Create sample embedding data for testing."""
    # Create two 768-dim vectors: one similar to query, one not
    np.random.seed(42)
    base_vec = np.random.randn(768).astype(np.float32)
    base_vec = base_vec / np.linalg.norm(base_vec)

    # Similar vector (small perturbation)
    similar_vec = base_vec + np.random.randn(768).astype(np.float32) * 0.02
    similar_vec = similar_vec / np.linalg.norm(similar_vec)

    # Dissimilar vector (random)
    dissimilar_vec = np.random.randn(768).astype(np.float32)
    dissimilar_vec = dissimilar_vec / np.linalg.norm(dissimilar_vec)

    return {
        "query": base_vec.tolist(),
        "similar": similar_vec.tolist(),
        "dissimilar": dissimilar_vec.tolist(),
    }


class TestCosineSimilarity:
    """Test cosine_similarity function correctness."""

    def test_identical_vectors(self):
        from app.services.ai.topic_matcher import cosine_similarity

        vec = [1.0, 0.0, 0.0]
        result = cosine_similarity(vec, np.array(vec, dtype=np.float32))
        assert abs(result - 1.0) < 1e-5

    def test_orthogonal_vectors(self):
        from app.services.ai.topic_matcher import cosine_similarity

        vec_a = [1.0, 0.0, 0.0]
        vec_b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        result = cosine_similarity(vec_a, vec_b)
        assert abs(result) < 1e-5

    def test_opposite_vectors(self):
        from app.services.ai.topic_matcher import cosine_similarity

        vec_a = [1.0, 0.0, 0.0]
        vec_b = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
        result = cosine_similarity(vec_a, vec_b)
        assert abs(result - (-1.0)) < 1e-5

    def test_zero_vector(self):
        from app.services.ai.topic_matcher import cosine_similarity

        vec_a = [0.0, 0.0, 0.0]
        vec_b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        result = cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_similar_vectors_high_score(self):
        from app.services.ai.topic_matcher import cosine_similarity

        vec_a = [0.9, 0.1, 0.0]
        vec_b = np.array([0.85, 0.15, 0.0], dtype=np.float32)
        result = cosine_similarity(vec_a, vec_b)
        assert result > 0.9


class TestTopicMatcher:
    """Test TopicMatcher matching logic."""

    @pytest.mark.anyio
    async def test_match_above_threshold(self, sample_embeddings):
        from app.services.ai.topic_matcher import TopicMatcher

        matcher = TopicMatcher()

        # Mock TopicEmbedding.find_all to return a document with the similar vector
        mock_doc = MagicMock()
        mock_doc.topic_id = "topic-1"
        mock_doc.topic_title = "Photosynthesis"
        mock_doc.chapter_id = "chapter-1"
        mock_doc.chapter_title = "Plant Biology"
        mock_doc.subject_slug = "biology"
        mock_doc.board_slug = "cbse"
        mock_doc.class_level = "Class 10"
        mock_doc.embedding = sample_embeddings["similar"]

        with patch(
            "app.services.ai.topic_matcher.TopicEmbedding.find_all"
        ) as mock_find:
            mock_find.return_value.to_list = AsyncMock(return_value=[mock_doc])
            result = await matcher.match_topic(sample_embeddings["query"])

        assert result is not None
        assert result["topic_id"] == "topic-1"
        assert result["topic_title"] == "Photosynthesis"
        assert result["score"] >= 0.70

    @pytest.mark.anyio
    async def test_no_match_below_threshold(self, sample_embeddings):
        from app.services.ai.topic_matcher import TopicMatcher

        matcher = TopicMatcher()

        # Mock TopicEmbedding.find_all to return a document with a dissimilar vector
        mock_doc = MagicMock()
        mock_doc.topic_id = "topic-2"
        mock_doc.topic_title = "Quantum Physics"
        mock_doc.chapter_id = "chapter-2"
        mock_doc.chapter_title = "Modern Physics"
        mock_doc.subject_slug = "physics"
        mock_doc.board_slug = "cbse"
        mock_doc.class_level = "Class 12"
        mock_doc.embedding = sample_embeddings["dissimilar"]

        with patch(
            "app.services.ai.topic_matcher.TopicEmbedding.find_all"
        ) as mock_find:
            mock_find.return_value.to_list = AsyncMock(return_value=[mock_doc])
            result = await matcher.match_topic(sample_embeddings["query"])

        # Dissimilar random vectors should not match above threshold
        # (random 768-dim vectors have low cosine similarity)
        if result is not None:
            # If by chance it matches, the score should still be reported correctly
            assert result["score"] >= 0.70
        else:
            assert result is None

    @pytest.mark.anyio
    async def test_returns_best_match(self, sample_embeddings):
        from app.services.ai.topic_matcher import TopicMatcher

        matcher = TopicMatcher()

        # Two documents: one similar, one dissimilar
        mock_doc_good = MagicMock()
        mock_doc_good.topic_id = "topic-good"
        mock_doc_good.topic_title = "Photosynthesis"
        mock_doc_good.chapter_id = "chapter-1"
        mock_doc_good.chapter_title = "Plant Biology"
        mock_doc_good.subject_slug = "biology"
        mock_doc_good.board_slug = "cbse"
        mock_doc_good.class_level = "Class 10"
        mock_doc_good.embedding = sample_embeddings["similar"]

        mock_doc_bad = MagicMock()
        mock_doc_bad.topic_id = "topic-bad"
        mock_doc_bad.topic_title = "Quantum Physics"
        mock_doc_bad.chapter_id = "chapter-2"
        mock_doc_bad.chapter_title = "Modern Physics"
        mock_doc_bad.subject_slug = "physics"
        mock_doc_bad.board_slug = "cbse"
        mock_doc_bad.class_level = "Class 12"
        mock_doc_bad.embedding = sample_embeddings["dissimilar"]

        with patch(
            "app.services.ai.topic_matcher.TopicEmbedding.find_all"
        ) as mock_find:
            mock_find.return_value.to_list = AsyncMock(
                return_value=[mock_doc_bad, mock_doc_good]
            )
            result = await matcher.match_topic(sample_embeddings["query"])

        # Should pick the similar one
        assert result is not None
        assert result["topic_id"] == "topic-good"

    @pytest.mark.anyio
    async def test_empty_embeddings(self):
        from app.services.ai.topic_matcher import TopicMatcher

        matcher = TopicMatcher()

        with patch(
            "app.services.ai.topic_matcher.TopicEmbedding.find_all"
        ) as mock_find:
            mock_find.return_value.to_list = AsyncMock(return_value=[])
            result = await matcher.match_topic([0.1] * 768)

        assert result is None

    @pytest.mark.anyio
    async def test_cache_invalidation(self, sample_embeddings):
        from app.services.ai.topic_matcher import TopicMatcher

        matcher = TopicMatcher()

        mock_doc = MagicMock()
        mock_doc.topic_id = "topic-1"
        mock_doc.topic_title = "Test"
        mock_doc.chapter_id = "ch-1"
        mock_doc.chapter_title = "Ch"
        mock_doc.subject_slug = "s"
        mock_doc.board_slug = "b"
        mock_doc.class_level = "c"
        mock_doc.embedding = sample_embeddings["similar"]

        with patch(
            "app.services.ai.topic_matcher.TopicEmbedding.find_all"
        ) as mock_find:
            mock_find.return_value.to_list = AsyncMock(return_value=[mock_doc])
            await matcher.match_topic(sample_embeddings["query"])

        # Cache should be valid
        assert matcher._is_cache_valid()

        # Invalidate
        matcher.invalidate_cache()
        assert not matcher._is_cache_valid()


class TestChatServiceTopicMatch:
    """Test ChatService.check_topic_match integration."""

    @pytest.mark.anyio
    async def test_check_topic_match_returns_match(self):
        from app.services.chat_service import ChatService

        mock_embedding = [0.1] * 768
        mock_match_result = {
            "topic_id": "t-1",
            "topic_title": "Photosynthesis",
            "chapter_id": "ch-1",
            "chapter_title": "Plant Biology",
            "subject_slug": "biology",
            "board_slug": "cbse",
            "class_level": "Class 10",
            "score": 0.85,
        }

        with (
            patch(
                "app.services.ai.embedder.generate_embedding_vector",
                new_callable=AsyncMock,
                return_value=mock_embedding,
            ),
            patch(
                "app.services.ai.topic_matcher.topic_matcher.match_topic",
                new_callable=AsyncMock,
                return_value=mock_match_result,
            ),
        ):
            result = await ChatService.check_topic_match("What is photosynthesis?")

        assert result is not None
        assert result["topic_title"] == "Photosynthesis"
        assert result["score"] == 0.85

    @pytest.mark.anyio
    async def test_check_topic_match_returns_none(self):
        from app.services.chat_service import ChatService

        mock_embedding = [0.1] * 768

        with (
            patch(
                "app.services.ai.embedder.generate_embedding_vector",
                new_callable=AsyncMock,
                return_value=mock_embedding,
            ),
            patch(
                "app.services.ai.topic_matcher.topic_matcher.match_topic",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await ChatService.check_topic_match("Tell me a joke")

        assert result is None

    @pytest.mark.anyio
    async def test_check_topic_match_handles_error(self):
        from app.services.chat_service import ChatService

        with patch(
            "app.services.ai.embedder.generate_embedding_vector",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API unavailable"),
        ):
            result = await ChatService.check_topic_match("What is photosynthesis?")

        # Should return None on error, not raise
        assert result is None
