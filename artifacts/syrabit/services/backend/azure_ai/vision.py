"""Azure AI Vision wrapper — generic image OCR + tags + captions.

Tier in the OCR + image-understanding chain alongside Google Vision
and Document Intelligence. Used for general photos, screenshots, and
diagrams where layout-aware parsing is overkill but a quick OCR +
caption pass is useful (e.g. user-uploaded clarification images in
chat). Selection lives in ``artifacts/syrabit-backend/ocr/router.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import _resolver

API_VERSION = "2024-02-01"


@dataclass
class VisionAnalysis:
    caption: str
    caption_confidence: float
    tags: list[str] = field(default_factory=list)
    ocr_text: str = ""


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token


def analyze(image_bytes: bytes, *, features: tuple[str, ...] = ("caption", "tags", "read")) -> VisionAnalysis:
    import requests

    endpoint = _resolver.endpoint_for("vision").rstrip("/")
    resp = requests.post(
        f"{endpoint}/computervision/imageanalysis:analyze"
        f"?api-version={API_VERSION}&features={','.join(features)}",
        data=image_bytes,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/octet-stream",
        },
        timeout=20,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-vision: throttled (429)")
    resp.raise_for_status()
    body = resp.json()

    cap = (body.get("captionResult") or {})
    tags = [t["name"] for t in (body.get("tagsResult") or {}).get("values", [])]
    read_blocks = (body.get("readResult") or {}).get("blocks", [])
    ocr_lines: list[str] = []
    for block in read_blocks:
        for line in block.get("lines", []):
            ocr_lines.append(line.get("text", ""))

    return VisionAnalysis(
        caption=cap.get("text", ""),
        caption_confidence=float(cap.get("confidence", 0.0)),
        tags=tags,
        ocr_text="\n".join(ocr_lines),
    )
