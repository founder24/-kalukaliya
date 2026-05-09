"""
Syrabit.ai — Pydantic request/response models.
Imported by server.py and any future router modules.
"""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import List, Literal, Optional

VALID_CATEGORIES = ("notes", "important_questions", "question_paper")


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    consent_dpdp: bool = False


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    plan: str = "free"
    credits_used: int = 0
    credits_limit: int = 0
    onboarding_done: bool = False
    is_admin: bool = False
    role: str = "student"
    board_id: Optional[str] = None
    class_id: Optional[str] = None
    stream_id: Optional[str] = None
    created_at: str
    avatar_url: Optional[str] = ""
    ads_opt_out: bool = False


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class OnboardingData(BaseModel):
    board_id: str
    board_name: str
    class_id: str
    class_name: str
    stream_id: Optional[str] = None
    stream_name: Optional[str] = None
    course_type: Optional[str] = None
    selected_subjects: Optional[list] = None


class ChatMessage(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    subject_id: Optional[str] = None
    subject_name: Optional[str] = None
    chapter_id: Optional[str] = None
    chapter_name: Optional[str] = None
    board_id: Optional[str] = None
    board_name: Optional[str] = None
    class_id: Optional[str] = None
    class_name: Optional[str] = None
    stream_name: Optional[str] = None
    model: Optional[str] = None
    document_id: Optional[str] = None
    card_context: Optional[str] = None   # Tier 0 — card content scraped from library page
    response_lang: Optional[str] = None


class ConversationCreate(BaseModel):
    title: Optional[str] = "New Conversation"
    subject_id: Optional[str] = None
    subject_name: Optional[str] = None


class AdminLoginReq(BaseModel):
    email: str
    password: str


class SubjectCreate(BaseModel):
    name: str
    stream_id: str = ""
    stream_name: Optional[str] = ""
    description: Optional[str] = ""
    tags: Optional[str] = ""
    thumbnail_url: Optional[str] = ""
    status: Optional[str] = "published"


class ChapterCreate(BaseModel):
    subject_id: str
    title: str
    slug: Optional[str] = ""
    # Task #295 — Assamese URL slug used by the dedicated /as/<…>/<slug_as>
    # path. Populated by scripts/backfill_assamese_slugs.py from the
    # translated chapter title. Empty until backfilled.
    slug_as: Optional[str] = ""
    description: Optional[str] = ""
    content: Optional[str] = ""
    content_as: Optional[str] = ""
    content_type: Optional[str] = "notes"
    category: Optional[Literal["notes", "important_questions", "question_paper"]] = "notes"
    chapter_number: Optional[int] = 1
    order_index: Optional[int] = 0
    order: Optional[int] = 1
    status: Optional[str] = "published"
    topics: Optional[List[str]] = []

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, v: str | None) -> str:
        if v is None:
            return "notes"
        v = v.strip().lower()
        if v not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}, got '{v}'")
        return v


class ChunkCreate(BaseModel):
    chapter_id: str
    content: str
    content_type: Optional[str] = "notes"
    category: Optional[Literal["notes", "important_questions", "question_paper"]] = "notes"
    tags: Optional[List[str]] = []

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, v: str | None) -> str:
        if v is None:
            return "notes"
        v = v.strip().lower()
        if v not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}, got '{v}'")
        return v


class DocumentUpload(BaseModel):
    subject_id: str
    document_name: str
    document_text: str
    document_type: Optional[str] = "text"


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    board_name: Optional[str] = None
    class_name: Optional[str] = None
    stream_name: Optional[str] = None
    course_type: Optional[str] = None
    selected_subjects: Optional[list] = None
    ads_opt_out: Optional[bool] = None


class PasswordResetReq(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


class UserStatusUpdate(BaseModel):
    status: str


class UserPlanUpdate(BaseModel):
    plan: str
    credits_used: Optional[int] = None


class UserRoleUpdate(BaseModel):
    role: str
    reason: Optional[str] = None


class UserCreditsUpdate(BaseModel):
    action: str = "add"
    amount: Optional[int] = None
    reason: Optional[str] = None


class SettingsUpdate(BaseModel):
    registrations_open: Optional[bool] = None
    maintenance_mode: Optional[bool] = None
    app_name: Optional[str] = None
    tagline: Optional[str] = None
    crawl_coverage_red: Optional[int] = Field(None, ge=0, le=100)
    crawl_coverage_yellow: Optional[int] = Field(None, ge=0, le=100)
    bot_missing_days: Optional[int] = Field(None, ge=1, le=90)


class RoadmapItemCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    status: Optional[str] = "planned"
    priority: Optional[str] = "medium"
    category: Optional[str] = "feature"
    phase: Optional[str] = ""
    effort: Optional[str] = "medium"
    impact: Optional[str] = "medium"


class BoardOut(BaseModel):
    id: str
    name: str
    slug: Optional[str] = ""
    description: Optional[str] = ""


class ClassOut(BaseModel):
    id: str
    name: str
    board_id: str
    slug: Optional[str] = ""


class StreamOut(BaseModel):
    id: str
    name: str
    class_id: str
    slug: Optional[str] = ""


class SubjectOut(BaseModel):
    id: str
    name: str
    stream_id: Optional[str] = ""
    description: Optional[str] = ""
    tags: Optional[str] = ""
    status: Optional[str] = "published"
    thumbnailUrl: Optional[str] = ""
    thumbnail_url: Optional[str] = ""


class LibraryBundleOut(BaseModel):
    boards: List[dict]
    classes: List[dict]
    streams: List[dict]
    subjects: List[dict]
    chapters: List[dict] = []
    # Boot tier: chapters[] is scoped to a single board id; client should
    # still fetch the full bundle for cross-board search.
    boot: Optional[str] = None
    chapters_partial: Optional[bool] = None


class ChatResponseOut(BaseModel):
    answer: str
    conversation_id: str
    credits_remaining: int = 0
    credits_used: int = 0
    rag_source: str = "none"
    rag_chunks_used: int = 0
    sources: List[dict] = []


class SearchResultOut(BaseModel):
    query: str
    results: List[dict]
    count: int


class HealthDependency(BaseModel):
    status: str
    latencyMs: Optional[int] = None


class HealthOut(BaseModel):
    status: str
    version: str
    service: str
    workers: int
    uptime_seconds: int
    dependencies: dict
    chat_latency: Optional[dict] = None


class ReadyOut(BaseModel):
    status: str
    checks: dict


class ErrorOut(BaseModel):
    error: bool = True
    status: int
    detail: str
    path: str


# ── Task #12 — AEO Answer-Card + FAQ materializer (chapter_faqs collection) ──
#
# Schema for the authoritative ``db.chapter_faqs`` collection written
# by ``aca_jobs/materialize_chapter_faqs.py``. Two ``kind`` discriminator
# values are persisted per chapter:
#
#   * ``quick_answer`` — the 40–60 word AEO Answer-Card body. Exactly
#     one row per chapter (the per-chapter fingerprint is stable).
#   * ``faq`` — one row per Q→A pair. The job writes 5–10 of these per
#     chapter, idempotent on ``(chapter_id, kind, fingerprint)``.
#
# Index contract (created at job start by
# ``aca_jobs.materialize_chapter_faqs.ensure_indexes``):
#   * Unique index on ``(chapter_id, kind, fingerprint)`` so the
#     upsert key is collision-safe across re-runs.
#   * TTL index on ``updated_at`` with ``expireAfterSeconds = 90 * 86400``.
#     IMPORTANT: ``updated_at`` MUST be a BSON datetime (not an ISO
#     string) — Mongo's TTL monitor silently ignores string values,
#     and the 90-day auto-expiry would otherwise never fire.
#
# This Pydantic model is the single source of truth for the
# document shape; the job and any future read-side code should
# import it rather than restate the field set.

ChapterFaqKind = Literal["faq", "quick_answer"]


class ChapterFaqDoc(BaseModel):
    """One document in ``db.chapter_faqs``.

    Mongo TTL on ``updated_at`` requires the BSON-date type, so the
    field is typed as ``datetime`` (not ``str``). The ``question`` and
    ``position`` fields are only populated for ``kind="faq"`` rows;
    quick-answer rows omit them.
    """
    chapter_id: str
    kind: ChapterFaqKind
    fingerprint: str  # 32-hex output of `cache_fingerprint.fingerprint(...)`
    answer: str
    rendered: Optional[str] = None
    formatted_by: str = "deterministic_template"

    # Quick-answer-only context (denormalised so admin tooling can
    # render the row without re-walking the syllabus graph).
    chapter_title: Optional[str] = None
    subject_name: Optional[str] = None
    board_name: Optional[str] = None
    class_name: Optional[str] = None

    # FAQ-only fields.
    question: Optional[str] = None
    source: Optional[str] = None  # one of: "syllabus" | "pyq" | "evergreen"
    position: Optional[int] = None

    # BSON dates — required for the TTL index to fire.
    created_at: datetime
    updated_at: datetime
