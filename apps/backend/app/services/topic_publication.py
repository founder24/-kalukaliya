"""Eligibility rules shared by public topic discovery and SEO surfaces."""

from beanie import PydanticObjectId

MIN_STANDALONE_TOPIC_WORDS = 40


def flex_id_query_values(value):
    """Match reference fields stored as either ObjectIds or legacy strings."""
    values = [value]
    try:
        object_id = PydanticObjectId(value)
    except Exception:
        return values
    if object_id != value:
        values.append(object_id)
    return values


def indexable_topics(topics):
    """Return reviewed, substantial topics with duplicate entries removed."""
    result = []
    seen = set()
    for topic in topics or []:
        definition = " ".join((getattr(topic, "definition", None) or "").split())
        slug = (getattr(topic, "topic_slug", None) or "").strip()
        if (
            getattr(topic, "definition_status", None) != "ok"
            or not slug
            or len(definition.split()) < MIN_STANDALONE_TOPIC_WORDS
        ):
            continue
        identity = " ".join(definition.lower().split())
        if identity in seen:
            continue
        seen.add(identity)
        result.append(topic)
    return result


def is_indexable_assamese_topic(topic):
    """Assamese topic pages need their own title, slug, and substantial definition."""
    title = " ".join((getattr(topic, "title_as", None) or "").split())
    definition = " ".join((getattr(topic, "definition_as", None) or "").split())
    slug = (getattr(topic, "topic_slug_as", None) or "").strip()
    return bool(title and slug and len(definition.split()) >= MIN_STANDALONE_TOPIC_WORDS)
