"""
KnowledgeGraphService - Traverses topic relationships for learning paths and clusters.
"""

import json
import logging

from beanie import PydanticObjectId

from app.models.topic_hub import TopicHub, TopicRelation

logger = logging.getLogger(__name__)


class KnowledgeGraphService:
    """Service for traversing and managing the topic knowledge graph."""

    async def get_related_topics(self, topic_slug: str) -> list[dict]:
        """Get all topics connected to this one with their metadata."""
        hub = await TopicHub.find_one({"topic_slug": topic_slug})
        if not hub or not hub.relations:
            return []

        related = []
        for relation in hub.relations:
            related_hub = await TopicHub.find_one(
                {"topic_slug": relation.related_topic_slug}
            )
            related.append(
                {
                    "topic_slug": relation.related_topic_slug,
                    "relation_type": relation.relation_type,
                    "strength": relation.strength,
                    "description": relation.description,
                    "title": related_hub.title if related_hub else None,
                    "definition": related_hub.definition if related_hub else None,
                }
            )
        return related

    async def get_prerequisite_chain(
        self, topic_slug: str, max_depth: int = 10
    ) -> list[dict]:
        """Return the learning path - what to study first (follows prerequisite/builds_on edges)."""
        chain = []
        visited = set()
        current_slug = topic_slug

        for _ in range(max_depth):
            if current_slug in visited:
                break
            visited.add(current_slug)

            hub = await TopicHub.find_one({"topic_slug": current_slug})
            if not hub:
                break

            # Find prerequisite relations
            prereqs = [
                r
                for r in hub.relations
                if r.relation_type in ("prerequisite", "builds_on")
            ]
            if not prereqs:
                break

            # Take the strongest prerequisite
            prereqs.sort(key=lambda r: r.strength, reverse=True)
            best = prereqs[0]

            prereq_hub = await TopicHub.find_one(
                {"topic_slug": best.related_topic_slug}
            )
            chain.append(
                {
                    "topic_slug": best.related_topic_slug,
                    "title": prereq_hub.title
                    if prereq_hub
                    else best.related_topic_slug,
                    "definition": prereq_hub.definition if prereq_hub else None,
                    "relation_type": best.relation_type,
                    "strength": best.strength,
                }
            )
            current_slug = best.related_topic_slug

        # Reverse so prerequisites come first (learning order)
        chain.reverse()
        return chain

    async def get_topic_cluster(self, topic_slug: str, depth: int = 2) -> dict:
        """BFS traversal returning a subgraph of related concepts."""
        nodes = {}
        edges = []
        queue = [(topic_slug, 0)]
        visited = set()

        while queue:
            current_slug, current_depth = queue.pop(0)
            if current_slug in visited or current_depth > depth:
                continue
            visited.add(current_slug)

            hub = await TopicHub.find_one({"topic_slug": current_slug})
            if not hub:
                continue

            nodes[current_slug] = {
                "topic_slug": current_slug,
                "title": hub.title,
                "definition": hub.definition,
                "difficulty_level": hub.difficulty_level,
                "importance": hub.importance,
            }

            for relation in hub.relations:
                edges.append(
                    {
                        "source": current_slug,
                        "target": relation.related_topic_slug,
                        "relation_type": relation.relation_type,
                        "strength": relation.strength,
                    }
                )
                if relation.related_topic_slug not in visited:
                    queue.append((relation.related_topic_slug, current_depth + 1))

        return {"nodes": list(nodes.values()), "edges": edges}

    async def auto_generate_relations(self, chapter_id: str) -> list[dict]:
        """Use Vertex AI to infer relationships between topics in the same chapter."""
        from app.services.ai.vertex_client import vertex_client

        hubs = await TopicHub.find(
            {"chapter_id": PydanticObjectId(chapter_id)}
        ).to_list()
        if len(hubs) < 2:
            return []

        topic_list = "\n".join(
            f"- {hub.topic_slug}: {hub.title} - {hub.definition or 'No definition'}"
            for hub in hubs
        )

        system_prompt = (
            "You are an educational content expert. Analyze the following topics from the same chapter "
            "and identify semantic relationships between them. Return a JSON array of relationships.\n"
            "Each relationship should have: source_slug, target_slug, relation_type "
            "(one of: prerequisite, builds_on, related, contrasts, part_of, leads_to), "
            "strength (0.0-1.0), and description."
        )
        user_message = f"Topics:\n{topic_list}\n\nReturn only valid JSON array."

        try:
            response = await vertex_client.generate(system_prompt, user_message)
            # Parse JSON from response
            # Strip markdown code fences if present
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                clean = clean.strip()
            relations = json.loads(clean)
            if not isinstance(relations, list):
                return []

            # Validate each relation against the TopicRelation schema
            validated_relations = []
            for item in relations:
                try:
                    validated = TopicRelation(
                        related_topic_slug=item.get(
                            "target_slug", item.get("related_topic_slug", "")
                        ),
                        relation_type=item.get("relation_type", "related"),
                        strength=float(item.get("strength", 0.5)),
                        description=item.get("description"),
                    )
                    validated_relations.append(validated.model_dump())
                except (ValueError, TypeError) as e:
                    logger.warning(f"Skipping invalid relation: {e}")
                    continue
            return validated_relations
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Failed to generate relations for chapter {chapter_id}: {e}")
            return []


knowledge_graph_service = KnowledgeGraphService()
