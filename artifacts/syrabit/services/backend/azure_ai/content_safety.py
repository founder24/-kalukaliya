"""Azure AI Content Safety wrapper — sync moderation.

Runs synchronously on:

* Inbound chat messages (before they reach the LLM router).
* Outbound LLM completions (before they're streamed to the user).
* Comment submissions on chapter / past-paper pages.
* Uploaded text (extracted OCR output, attached transcripts).

Categories scored: ``Hate``, ``SelfHarm``, ``Sexual``, ``Violence``.
A score >= ``BLOCK_THRESHOLD`` short-circuits the request and routes
the payload into the admin moderation queue (the same queue that
Rekognition feeds for image flags). Borderline scores
(``REVIEW_THRESHOLD <= score < BLOCK_THRESHOLD``) pass through but
are queued for admin review — Content Safety alone never makes the
final automated block decision (per the task's out-of-scope clause).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import _resolver

API_VERSION = "2024-09-01"

CATEGORIES = ("Hate", "SelfHarm", "Sexual", "Violence")

# Severity is 0..6 in steps of 2. Calibrated against the existing
# Rekognition queue's manual review rate; tighten only with admin
# panel telemetry to back the change.
BLOCK_THRESHOLD = 6
REVIEW_THRESHOLD = 4


@dataclass
class ModerationVerdict:
    blocked: bool
    review: bool
    scores: dict[str, int]
    flagged_categories: list[str]


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token


def analyze_text(text: str, *, blocklists: Iterable[str] = ()) -> ModerationVerdict:
    import requests

    endpoint = _resolver.endpoint_for("content_safety").rstrip("/")
    resp = requests.post(
        f"{endpoint}/contentsafety/text:analyze?api-version={API_VERSION}",
        json={
            "text": text,
            "categories": list(CATEGORIES),
            "blocklistNames": list(blocklists),
            "haltOnBlocklistHit": True,
            "outputType": "FourSeverityLevels",
        },
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-content-safety: throttled (429)")
    resp.raise_for_status()
    body = resp.json()

    scores = {
        item["category"]: int(item["severity"])
        for item in body.get("categoriesAnalysis", [])
    }
    blocklist_hit = bool(body.get("blocklistsMatch") or [])

    blocked = blocklist_hit or any(s >= BLOCK_THRESHOLD for s in scores.values())
    review = (
        not blocked
        and any(s >= REVIEW_THRESHOLD for s in scores.values())
    )
    flagged = [c for c, s in scores.items() if s >= REVIEW_THRESHOLD]

    return ModerationVerdict(
        blocked=blocked,
        review=review,
        scores=scores,
        flagged_categories=flagged,
    )


def analyze_image(image_bytes: bytes) -> ModerationVerdict:
    """Image moderation — used in parallel with Rekognition.

    Whichever returns a verdict first wins; if they disagree on a
    block decision the borderline case is escalated to the admin
    queue. The admin moderation router handles the merge.
    """
    import base64

    import requests

    endpoint = _resolver.endpoint_for("content_safety").rstrip("/")
    resp = requests.post(
        f"{endpoint}/contentsafety/image:analyze?api-version={API_VERSION}",
        json={
            "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
            "categories": list(CATEGORIES),
            "outputType": "FourSeverityLevels",
        },
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-content-safety: image throttled (429)")
    resp.raise_for_status()
    body = resp.json()

    scores = {
        item["category"]: int(item["severity"])
        for item in body.get("categoriesAnalysis", [])
    }
    blocked = any(s >= BLOCK_THRESHOLD for s in scores.values())
    review = not blocked and any(s >= REVIEW_THRESHOLD for s in scores.values())
    return ModerationVerdict(
        blocked=blocked,
        review=review,
        scores=scores,
        flagged_categories=[c for c, s in scores.items() if s >= REVIEW_THRESHOLD],
    )
