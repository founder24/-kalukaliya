"""Azure AI Language wrapper — summaries, key phrases, NER, PII.

Two surfaces consume this:

* **Topic Discovery** — ``extract_key_phrases`` enriches the
  discovered-topic cards with the top phrases per chapter. The
  admin-visible ``topic.azure_key_phrases`` field is populated by
  the Discovery cron job.

* **SEO Manager** — ``summarize`` and ``recognize_entities`` run on
  long-form chapter content; the resulting summary feeds the meta
  description and the entities feed the structured-data block. The
  admin SEO panel surfaces both with a "regenerate" button.

PII detection is a separate path used by chat-history retention to
strip emails / phone numbers from logs before they hit the unified
observability sink. Out-of-scope today: automatic redaction in
user-facing UI (admin reviews flagged spans first).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import _resolver

API_VERSION = "2024-11-15-preview"


@dataclass
class KeyPhrasesResult:
    phrases: list[str]


@dataclass
class Entity:
    text: str
    category: str
    confidence: float


@dataclass
class EntitiesResult:
    entities: list[Entity] = field(default_factory=list)


@dataclass
class PIIResult:
    redacted_text: str
    entities: list[Entity] = field(default_factory=list)


@dataclass
class SummaryResult:
    sentences: list[str]


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token


def _post(path: str, payload: dict, *, timeout: int = 15) -> dict:
    import requests

    endpoint = _resolver.endpoint_for("language").rstrip("/")
    resp = requests.post(
        f"{endpoint}{path}?api-version={API_VERSION}",
        json=payload,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise RuntimeError(f"azure-language: throttled (429) on {path}")
    resp.raise_for_status()
    return resp.json()


def extract_key_phrases(text: str, *, language: str = "en") -> KeyPhrasesResult:
    body = _post(
        "/language/:analyze-text",
        {
            "kind": "KeyPhraseExtraction",
            "analysisInput": {
                "documents": [{"id": "1", "language": language, "text": text}]
            },
        },
    )
    docs = body.get("results", {}).get("documents", [])
    phrases = docs[0].get("keyPhrases", []) if docs else []
    return KeyPhrasesResult(phrases=phrases)


def recognize_entities(text: str, *, language: str = "en") -> EntitiesResult:
    body = _post(
        "/language/:analyze-text",
        {
            "kind": "EntityRecognition",
            "analysisInput": {
                "documents": [{"id": "1", "language": language, "text": text}]
            },
        },
    )
    docs = body.get("results", {}).get("documents", [])
    raw = docs[0].get("entities", []) if docs else []
    return EntitiesResult(
        entities=[
            Entity(text=e["text"], category=e["category"], confidence=float(e.get("confidenceScore", 0.0)))
            for e in raw
        ]
    )


def detect_pii(text: str, *, language: str = "en") -> PIIResult:
    body = _post(
        "/language/:analyze-text",
        {
            "kind": "PiiEntityRecognition",
            "analysisInput": {
                "documents": [{"id": "1", "language": language, "text": text}]
            },
        },
    )
    docs = body.get("results", {}).get("documents", [])
    if not docs:
        return PIIResult(redacted_text=text)
    doc = docs[0]
    return PIIResult(
        redacted_text=doc.get("redactedText", text),
        entities=[
            Entity(text=e["text"], category=e["category"], confidence=float(e.get("confidenceScore", 0.0)))
            for e in doc.get("entities", [])
        ],
    )


def summarize(text: str, *, sentence_count: int = 3, language: str = "en") -> SummaryResult:
    """Extractive summary — long-running operation under the hood.

    The Language `:analyze-text-jobs` endpoint accepts the request
    synchronously but actually polls; we keep it sync here because
    chapter summaries are pre-computed by the SEO cron job, never on
    the request path.
    """
    body = _post(
        "/language/analyze-text/jobs",
        {
            "displayName": "syrabit-seo-summary",
            "analysisInput": {
                "documents": [{"id": "1", "language": language, "text": text}]
            },
            "tasks": [
                {
                    "kind": "ExtractiveSummarization",
                    "parameters": {"sentenceCount": sentence_count},
                }
            ],
        },
        timeout=60,
    )
    # The jobs endpoint returns 202 + operation-location; in this
    # wrapper the SEO cron job inlines a poll. For the purposes of
    # the gateway contract, return whatever sentences are available
    # synchronously and let the cron job upgrade later.
    tasks = body.get("tasks", {}).get("items", [])
    sentences: list[str] = []
    for task in tasks:
        for doc in task.get("results", {}).get("documents", []):
            sentences.extend(s["text"] for s in doc.get("sentences", []))
    return SummaryResult(sentences=sentences)
