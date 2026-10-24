from types import SimpleNamespace

from app.services.topic_publication import (
    flex_id_query_values,
    indexable_topics,
    is_indexable_assamese_topic,
)


def test_indexable_topics_requires_review_and_standalone_definition():
    long_definition = " ".join(f"concept{i}" for i in range(40))
    topics = [
        SimpleNamespace(
            title="Published concept",
            definition=long_definition,
            topic_slug="published-concept",
            definition_status="ok",
        ),
        SimpleNamespace(
            title="Short concept",
            definition="Too short to stand alone.",
            topic_slug="short-concept",
            definition_status="ok",
        ),
        SimpleNamespace(
            title="Unreviewed concept",
            definition=long_definition,
            topic_slug="unreviewed-concept",
            definition_status="pending",
        ),
    ]

    assert indexable_topics(topics) == [topics[0]]


def test_indexable_topics_removes_duplicate_explanations():
    definition = " ".join(f"term{i}" for i in range(40))
    first = SimpleNamespace(
        title="First title", definition=definition, topic_slug="first", definition_status="ok"
    )
    duplicate = SimpleNamespace(
        title="Different title", definition=definition, topic_slug="duplicate", definition_status="ok"
    )

    assert indexable_topics([first, duplicate]) == [first]


def test_reference_lookup_matches_legacy_strings_and_object_ids():
    legacy_id = "0bd48cd1-3912-47f8-8f66-43dcd62ef116"
    assert flex_id_query_values(legacy_id) == [legacy_id]

    mongo_id = "0123456789abcdef01234567"
    values = flex_id_query_values(mongo_id)
    assert values[0] == mongo_id
    assert len(values) == 2


def test_assamese_topic_requires_title_slug_and_at_least_40_words():
    long_definition = " ".join(f"অসমীয়া{i}" for i in range(40))
    complete = SimpleNamespace(
        title_as="অসমীয়া বিষয়",
        topic_slug_as="অসমীয়া-বিষয়",
        definition_as=long_definition,
    )
    assert is_indexable_assamese_topic(complete)
    assert not is_indexable_assamese_topic(SimpleNamespace(
        title_as="অসমীয়া বিষয়",
        topic_slug_as="অসমীয়া-বিষয়",
        definition_as="চুটি",
    ))
    assert not is_indexable_assamese_topic(SimpleNamespace(
        title_as="অসমীয়া বিষয়",
        topic_slug_as="",
        definition_as=long_definition,
    ))
    assert not is_indexable_assamese_topic(SimpleNamespace(
        title_as="",
        topic_slug_as="অসমীয়া-বিষয়",
        definition_as=long_definition,
    ))


async def test_admin_topic_update_persists_assamese_fields(monkeypatch):
    from app.api.v1 import admin_content
    from app.models.content import Topic

    topic = Topic(id="topic-1", title="English title", topic_slug="english-title")

    class ChapterStub:
        published_topics = [topic]

        async def save(self):
            return None

    chapter = ChapterStub()

    class QueryStub:
        async def to_list(self, length=None):
            return [chapter]

    monkeypatch.setattr(admin_content.Chapter, "find", lambda query: QueryStub())
    response = await admin_content.update_topic(
        None,
        "topic-1",
        admin_content.TopicUpdate(
            title_as="অসমীয়া শিৰোনাম",
            topic_slug_as="অসমীয়া-শিৰোনাম",
            definition_as="দীঘল সংজ্ঞা",
        ),
    )

    assert response == {"status": "updated", "topic_id": "topic-1"}
    assert topic.title_as == "অসমীয়া শিৰোনাম"
    assert topic.topic_slug_as == "অসমীয়া-শিৰোনাম"
    assert topic.definition_as == "দীঘল সংজ্ঞা"
