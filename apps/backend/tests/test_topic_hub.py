"""
Tests for the TopicHub Authority Layer + Knowledge Graph Layer:
- TopicHub model creation and validation
- Sub-model validation (TopicSource, TopicMCQ, TopicPYQ, TopicRelation)
- KnowledgeGraphService methods (mocked DB)
- AuthorityGeneratorService methods (mocked DB + Vertex AI)
- Public API endpoints (mocked DB)
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone


# ================================
# Model Validation Tests
# ================================


class TestTopicHubModel:
    """Test TopicHub and sub-model creation/validation."""

    def test_topic_source_creation(self):
        """Test TopicSource model creation."""
        from app.models.topic_hub import TopicSource

        source = TopicSource(
            source_type="ncert",
            title="NCERT Class 12 Biology",
            url="https://ncert.nic.in/textbook.php",
            year=2023,
            description="Official NCERT textbook reference",
        )
        assert source.source_type == "ncert"
        assert source.title == "NCERT Class 12 Biology"
        assert source.url is not None
        assert source.year == 2023

    def test_topic_mcq_creation(self):
        """Test TopicMCQ model creation with all fields."""
        from app.models.topic_hub import TopicMCQ

        mcq = TopicMCQ(
            question="What is osmosis?",
            options=[
                "Movement of solute",
                "Movement of solvent through semipermeable membrane",
                "Movement of gas",
                "None of the above",
            ],
            correct_index=1,
            explanation="Osmosis is the movement of solvent molecules",
            source="NCERT Exercise",
            difficulty="easy",
        )
        assert mcq.correct_index == 1
        assert len(mcq.options) == 4
        assert mcq.difficulty == "easy"
        assert mcq.question == "What is osmosis?"
        assert mcq.source == "NCERT Exercise"

    def test_topic_mcq_defaults(self):
        """Test TopicMCQ default values."""
        from app.models.topic_hub import TopicMCQ

        mcq = TopicMCQ(
            question="Test?",
            options=["A", "B", "C", "D"],
            correct_index=0,
        )
        assert mcq.difficulty == "medium"
        assert mcq.explanation is None
        assert mcq.source is None

    def test_topic_pyq_creation(self):
        """Test TopicPYQ model creation."""
        from app.models.topic_hub import TopicPYQ

        pyq = TopicPYQ(
            question="Define osmosis with an example.",
            year=2023,
            board="AHSEC",
            marks=5,
            answer_hint="Mention semipermeable membrane",
        )
        assert pyq.year == 2023
        assert pyq.board == "AHSEC"
        assert pyq.marks == 5
        assert pyq.solution is None

    def test_topic_relation_creation(self):
        """Test TopicRelation model with all fields."""
        from app.models.topic_hub import TopicRelation

        relation = TopicRelation(
            related_topic_slug="diffusion",
            relation_type="prerequisite",
            strength=0.8,
            description="Understanding osmosis requires knowledge of diffusion",
        )
        assert relation.relation_type == "prerequisite"
        assert relation.strength == 0.8
        assert relation.related_topic_slug == "diffusion"

    def test_topic_relation_defaults(self):
        """Test TopicRelation default values."""
        from app.models.topic_hub import TopicRelation

        relation = TopicRelation(
            related_topic_slug="cell-membrane",
            relation_type="related",
        )
        assert relation.strength == 0.5
        assert relation.description is None

    def test_topic_hub_model_dump(self):
        """Test TopicHub sub-models can be serialized with model_dump."""
        from app.models.topic_hub import TopicMCQ, TopicRelation, TopicSource

        mcq = TopicMCQ(
            question="What is osmosis?",
            options=["A", "B", "C", "D"],
            correct_index=0,
            explanation="Test",
            difficulty="hard",
        )
        data = mcq.model_dump()
        assert data["question"] == "What is osmosis?"
        assert data["correct_index"] == 0
        assert data["difficulty"] == "hard"
        assert len(data["options"]) == 4

        relation = TopicRelation(
            related_topic_slug="diffusion",
            relation_type="prerequisite",
            strength=0.85,
            description="Requires diffusion knowledge",
        )
        data = relation.model_dump()
        assert data["related_topic_slug"] == "diffusion"
        assert data["strength"] == 0.85

        source = TopicSource(
            source_type="ncert",
            title="NCERT Biology",
            url="https://example.com",
            year=2023,
        )
        data = source.model_dump()
        assert data["source_type"] == "ncert"
        assert data["year"] == 2023

    def test_topic_hub_settings(self):
        """Test TopicHub document settings are correct."""
        from app.models.topic_hub import TopicHub

        assert TopicHub.Settings.name == "topic_hubs"
        assert len(TopicHub.Settings.indexes) == 4
        # Check compound index is present
        assert [("topic_slug", 1), ("chapter_id", 1)] in TopicHub.Settings.indexes


# ================================
# Knowledge Graph Service Tests
# ================================


class TestKnowledgeGraphService:
    """Test KnowledgeGraphService with mocked database calls."""

    @pytest.mark.asyncio
    @patch("app.services.knowledge_graph.TopicHub")
    async def test_get_related_topics_empty(self, mock_hub_class):
        """Test get_related_topics when hub not found."""
        from app.services.knowledge_graph import KnowledgeGraphService

        mock_hub_class.find_one = AsyncMock(return_value=None)

        service = KnowledgeGraphService()
        result = await service.get_related_topics("nonexistent-topic")
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.knowledge_graph.TopicHub")
    async def test_get_related_topics_no_relations(self, mock_hub_class):
        """Test get_related_topics when hub exists but has no relations."""
        from app.services.knowledge_graph import KnowledgeGraphService

        mock_hub = MagicMock()
        mock_hub.relations = []

        mock_hub_class.find_one = AsyncMock(return_value=mock_hub)

        service = KnowledgeGraphService()
        result = await service.get_related_topics("osmosis")
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.knowledge_graph.TopicHub")
    async def test_get_related_topics_with_relations(self, mock_hub_class):
        """Test get_related_topics returns enriched relation data."""
        from app.services.knowledge_graph import KnowledgeGraphService
        from app.models.topic_hub import TopicRelation

        mock_hub = MagicMock()
        mock_hub.relations = [
            TopicRelation(
                related_topic_slug="diffusion",
                relation_type="prerequisite",
                strength=0.9,
                description="Prerequisite concept",
            )
        ]

        mock_related_hub = MagicMock()
        mock_related_hub.title = "Diffusion"
        mock_related_hub.definition = "Movement of particles"

        mock_hub_class.find_one = AsyncMock(side_effect=[mock_hub, mock_related_hub])

        service = KnowledgeGraphService()
        result = await service.get_related_topics("osmosis")
        assert len(result) == 1
        assert result[0]["topic_slug"] == "diffusion"
        assert result[0]["title"] == "Diffusion"
        assert result[0]["strength"] == 0.9
        assert result[0]["relation_type"] == "prerequisite"
        assert result[0]["definition"] == "Movement of particles"

    @pytest.mark.asyncio
    @patch("app.services.knowledge_graph.TopicHub")
    async def test_get_prerequisite_chain(self, mock_hub_class):
        """Test prerequisite chain follows prerequisite edges."""
        from app.services.knowledge_graph import KnowledgeGraphService
        from app.models.topic_hub import TopicRelation

        # osmosis has prerequisite -> diffusion
        mock_osmosis = MagicMock()
        mock_osmosis.relations = [
            TopicRelation(
                related_topic_slug="diffusion",
                relation_type="prerequisite",
                strength=0.9,
            )
        ]

        # diffusion has no prerequisites
        mock_diffusion = MagicMock()
        mock_diffusion.title = "Diffusion"
        mock_diffusion.definition = "Particle movement"
        mock_diffusion.relations = []

        mock_hub_class.find_one = AsyncMock(
            side_effect=[mock_osmosis, mock_diffusion, mock_diffusion]
        )

        service = KnowledgeGraphService()
        result = await service.get_prerequisite_chain("osmosis")
        assert len(result) == 1
        assert result[0]["topic_slug"] == "diffusion"
        assert result[0]["relation_type"] == "prerequisite"
        assert result[0]["title"] == "Diffusion"

    @pytest.mark.asyncio
    @patch("app.services.knowledge_graph.TopicHub")
    async def test_get_prerequisite_chain_empty(self, mock_hub_class):
        """Test prerequisite chain returns empty when no prerequisites exist."""
        from app.services.knowledge_graph import KnowledgeGraphService

        mock_hub = MagicMock()
        mock_hub.relations = []

        mock_hub_class.find_one = AsyncMock(return_value=mock_hub)

        service = KnowledgeGraphService()
        result = await service.get_prerequisite_chain("osmosis")
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.knowledge_graph.TopicHub")
    async def test_get_topic_cluster(self, mock_hub_class):
        """Test topic cluster BFS traversal."""
        from app.services.knowledge_graph import KnowledgeGraphService
        from app.models.topic_hub import TopicRelation

        mock_hub = MagicMock()
        mock_hub.title = "Osmosis"
        mock_hub.definition = "Solvent movement"
        mock_hub.difficulty_level = "medium"
        mock_hub.importance = "high"
        mock_hub.relations = [
            TopicRelation(
                related_topic_slug="diffusion",
                relation_type="related",
                strength=0.8,
            )
        ]

        # Second call for diffusion - returns None to stop traversal
        mock_hub_class.find_one = AsyncMock(side_effect=[mock_hub, None])

        service = KnowledgeGraphService()
        result = await service.get_topic_cluster("osmosis", depth=1)
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["topic_slug"] == "osmosis"
        assert result["nodes"][0]["title"] == "Osmosis"
        assert len(result["edges"]) == 1
        assert result["edges"][0]["source"] == "osmosis"
        assert result["edges"][0]["target"] == "diffusion"

    @pytest.mark.asyncio
    @patch("app.services.knowledge_graph.TopicHub")
    async def test_get_topic_cluster_not_found(self, mock_hub_class):
        """Test topic cluster returns empty when root not found."""
        from app.services.knowledge_graph import KnowledgeGraphService

        mock_hub_class.find_one = AsyncMock(return_value=None)

        service = KnowledgeGraphService()
        result = await service.get_topic_cluster("nonexistent")
        assert result == {"nodes": [], "edges": []}


# ================================
# Authority Generator Tests
# ================================


class TestAuthorityGeneratorService:
    """Test AuthorityGeneratorService with mocked Vertex AI."""

    @pytest.mark.asyncio
    @patch("app.services.authority_generator.vertex_client")
    @patch("app.services.authority_generator.TopicHub")
    async def test_generate_mcqs_success(self, mock_hub_class, mock_vertex):
        """Test successful MCQ generation."""
        from app.services.authority_generator import AuthorityGeneratorService

        mock_hub = MagicMock()
        mock_hub.title = "Osmosis"
        mock_hub.definition = "Solvent movement through membrane"
        mock_hub.key_points = ["Point 1", "Point 2"]
        mock_hub.definition_extended = None
        mock_hub.mcqs = []
        mock_hub.updated_at = datetime.now(timezone.utc)
        mock_hub.save = AsyncMock()

        mock_hub_class.get = AsyncMock(return_value=mock_hub)

        mock_vertex.generate = AsyncMock(
            return_value='[{"question": "What is osmosis?", "options": ["A", "B", "C", "D"], "correct_index": 1, "explanation": "Test explanation", "difficulty": "easy"}]'
        )

        service = AuthorityGeneratorService()
        result = await service.generate_mcqs("507f1f77bcf86cd799439011", count=1)
        assert len(result) == 1
        assert result[0].question == "What is osmosis?"
        assert result[0].correct_index == 1
        assert result[0].difficulty == "easy"
        mock_hub.save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.authority_generator.vertex_client")
    @patch("app.services.authority_generator.TopicHub")
    async def test_generate_mcqs_not_found(self, mock_hub_class, mock_vertex):
        """Test MCQ generation when hub not found."""
        from app.services.authority_generator import AuthorityGeneratorService

        mock_hub_class.get = AsyncMock(return_value=None)

        service = AuthorityGeneratorService()
        with pytest.raises(ValueError, match="not found"):
            await service.generate_mcqs("507f1f77bcf86cd799439011")

    @pytest.mark.asyncio
    @patch("app.services.authority_generator.vertex_client")
    @patch("app.services.authority_generator.TopicHub")
    async def test_generate_mcqs_malformed_response(self, mock_hub_class, mock_vertex):
        """Test MCQ generation handles malformed AI response gracefully."""
        from app.services.authority_generator import AuthorityGeneratorService

        mock_hub = MagicMock()
        mock_hub.title = "Osmosis"
        mock_hub.definition = "Test"
        mock_hub.key_points = []
        mock_hub.definition_extended = None
        mock_hub.mcqs = []
        mock_hub.save = AsyncMock()

        mock_hub_class.get = AsyncMock(return_value=mock_hub)
        mock_vertex.generate = AsyncMock(return_value="not valid json at all")

        service = AuthorityGeneratorService()
        result = await service.generate_mcqs("507f1f77bcf86cd799439011")
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.authority_generator.vertex_client")
    @patch("app.services.authority_generator.TopicHub")
    async def test_generate_mcqs_strips_markdown_fences(
        self, mock_hub_class, mock_vertex
    ):
        """Test MCQ generation strips markdown code fences from AI response."""
        from app.services.authority_generator import AuthorityGeneratorService

        mock_hub = MagicMock()
        mock_hub.title = "Osmosis"
        mock_hub.definition = "Solvent movement"
        mock_hub.key_points = []
        mock_hub.definition_extended = None
        mock_hub.mcqs = []
        mock_hub.updated_at = datetime.now(timezone.utc)
        mock_hub.save = AsyncMock()

        mock_hub_class.get = AsyncMock(return_value=mock_hub)
        mock_vertex.generate = AsyncMock(
            return_value='```json\n[{"question": "Test?", "options": ["A", "B", "C", "D"], "correct_index": 0, "explanation": "Explain", "difficulty": "medium"}]\n```'
        )

        service = AuthorityGeneratorService()
        result = await service.generate_mcqs("507f1f77bcf86cd799439011", count=1)
        assert len(result) == 1
        assert result[0].question == "Test?"


# ================================
# API Endpoint Tests
# ================================


class TestTopicsAPI:
    """Test public topics API endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.v1.topics.TopicHub")
    async def test_get_topic_hub_success(self, mock_hub_class):
        """Test GET /api/v1/topics/{slug} returns hub data."""
        from fastapi.testclient import TestClient
        from app.api.v1.topics import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/topics")

        mock_hub = MagicMock()
        mock_hub.model_dump = MagicMock(
            return_value={
                "topic_slug": "osmosis",
                "title": "Osmosis",
                "definition": "Solvent movement",
                "mcqs": [],
                "pyqs": [],
                "relations": [],
            }
        )
        mock_hub_class.find_one = AsyncMock(return_value=mock_hub)

        client = TestClient(app)
        response = client.get("/api/v1/topics/osmosis")
        assert response.status_code == 200
        data = response.json()
        assert data["topic_slug"] == "osmosis"
        assert data["title"] == "Osmosis"

    @pytest.mark.asyncio
    @patch("app.api.v1.topics.TopicHub")
    async def test_get_topic_hub_not_found(self, mock_hub_class):
        """Test GET /api/v1/topics/{slug} returns 404 when not found."""
        from fastapi.testclient import TestClient
        from app.api.v1.topics import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/topics")

        mock_hub_class.find_one = AsyncMock(return_value=None)

        client = TestClient(app)
        response = client.get("/api/v1/topics/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.v1.topics.TopicHub")
    async def test_get_topic_mcqs(self, mock_hub_class):
        """Test GET /api/v1/topics/{slug}/mcqs returns MCQ list."""
        from fastapi.testclient import TestClient
        from app.api.v1.topics import router
        from fastapi import FastAPI
        from app.models.topic_hub import TopicMCQ

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/topics")

        mock_hub = MagicMock()
        mock_hub.title = "Osmosis"
        mock_hub.mcqs = [
            TopicMCQ(
                question="What is osmosis?",
                options=["A", "B", "C", "D"],
                correct_index=1,
            )
        ]
        mock_hub_class.find_one = AsyncMock(return_value=mock_hub)

        client = TestClient(app)
        response = client.get("/api/v1/topics/osmosis/mcqs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["mcqs"][0]["question"] == "What is osmosis?"
        assert data["topic_slug"] == "osmosis"

    @pytest.mark.asyncio
    @patch("app.api.v1.topics.TopicHub")
    async def test_get_topic_pyqs(self, mock_hub_class):
        """Test GET /api/v1/topics/{slug}/pyqs returns PYQ list."""
        from fastapi.testclient import TestClient
        from app.api.v1.topics import router
        from fastapi import FastAPI
        from app.models.topic_hub import TopicPYQ

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/topics")

        mock_hub = MagicMock()
        mock_hub.title = "Osmosis"
        mock_hub.pyqs = [
            TopicPYQ(
                question="Define osmosis.",
                year=2022,
                board="AHSEC",
                marks=5,
            )
        ]
        mock_hub_class.find_one = AsyncMock(return_value=mock_hub)

        client = TestClient(app)
        response = client.get("/api/v1/topics/osmosis/pyqs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["pyqs"][0]["question"] == "Define osmosis."
        assert data["pyqs"][0]["year"] == 2022

    @pytest.mark.asyncio
    @patch("app.api.v1.topics.knowledge_graph_service")
    async def test_get_related_topics_endpoint(self, mock_kg_service):
        """Test GET /api/v1/topics/{slug}/related returns related topics."""
        from fastapi.testclient import TestClient
        from app.api.v1.topics import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/topics")

        mock_kg_service.get_related_topics = AsyncMock(
            return_value=[
                {
                    "topic_slug": "diffusion",
                    "relation_type": "prerequisite",
                    "strength": 0.9,
                    "description": "Required prerequisite",
                    "title": "Diffusion",
                    "definition": "Particle movement",
                }
            ]
        )

        client = TestClient(app)
        response = client.get("/api/v1/topics/osmosis/related")
        assert response.status_code == 200
        data = response.json()
        assert data["topic_slug"] == "osmosis"
        assert data["total"] == 1
        assert data["related"][0]["topic_slug"] == "diffusion"

    @pytest.mark.asyncio
    @patch("app.api.v1.topics.knowledge_graph_service")
    async def test_get_study_path_endpoint(self, mock_kg_service):
        """Test GET /api/v1/topics/{slug}/study-path returns prerequisite chain."""
        from fastapi.testclient import TestClient
        from app.api.v1.topics import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/topics")

        mock_kg_service.get_prerequisite_chain = AsyncMock(
            return_value=[
                {
                    "topic_slug": "diffusion",
                    "title": "Diffusion",
                    "definition": "Particle movement",
                    "relation_type": "prerequisite",
                    "strength": 0.9,
                }
            ]
        )

        client = TestClient(app)
        response = client.get("/api/v1/topics/osmosis/study-path")
        assert response.status_code == 200
        data = response.json()
        assert data["topic_slug"] == "osmosis"
        assert data["total_steps"] == 1
        assert data["study_path"][0]["topic_slug"] == "diffusion"
