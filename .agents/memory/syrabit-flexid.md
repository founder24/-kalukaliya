---
name: Syrabit content model FlexId
description: DB reference fields use legacy short/UUID strings not MongoDB ObjectIds — must use FlexId union type
---

## Rule
All `PydanticObjectId` reference fields in `apps/backend/app/models/content.py` must use `FlexId = Union[PydanticObjectId, str]` — never bare `PydanticObjectId`.

**Why:** The production Atlas DB has non-ObjectId values in reference fields:
- `Subject.stream_id` → short strings like `'s13'`
- `Chapter.subject_id` → UUID strings like `'0bd48cd1-3912-47f8-8f66-43dcd62ef116'`
- `Class.board_id`, `Stream.class_id`, `TopicEmbedding.chapter_id` → same pattern

Using bare `PydanticObjectId` causes `ValidationError` on every `find().to_list()` call, which crashes the `library-bundle` endpoint silently (caught exception → returns `{"boards": []}`).

**How to apply:** When adding any new Document model with a foreign-key field, use:
```python
FlexId = Union[PydanticObjectId, str]  # defined at top of content.py
my_ref_id: FlexId
my_optional_ref: Optional[FlexId] = None
```

## Fixed models (June 2026)
- `Class.board_id` → FlexId
- `Stream.class_id` → FlexId
- `Subject.stream_id` → Optional[FlexId]
- `Chapter.subject_id` → FlexId
- `TopicEmbedding.chapter_id` → FlexId
