"""Azure AI Translator wrapper — Sarvam fallback for Indic <-> English.

Wired into ``artifacts/syrabit-backend/lang/router.py`` as the third
tier (Sarvam → Bhashini → Azure Translator). The language toggle in
the chapter reader, comments, and chat all flow through that
router; nothing here decides on its own to take over.

Translator is a *global* Cognitive Services account so the endpoint
URL is the standard `https://api.cognitive.microsofttranslator.com`
host rather than the per-region subdomain emitted for regional
services. The resolver returns whatever the Terraform output wrote
to Key Vault — overriding here would let the resolver code path
drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import _resolver

API_VERSION = "3.0"


@dataclass
class TranslationResult:
    text: str
    source_lang: str
    target_lang: str
    char_count: int


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token


def translate(
    text: str,
    *,
    source: str,
    target: str,
) -> TranslationResult:
    import requests

    endpoint = _resolver.endpoint_for("translator").rstrip("/")
    resp = requests.post(
        f"{endpoint}/translate",
        params={"api-version": API_VERSION, "from": source, "to": target},
        json=[{"text": text}],
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            # Translator requires the resource region as a header for
            # AAD auth on regional accounts; ``global`` is accepted by
            # the data plane and matches the SKU pinned in TF.
            "Ocp-Apim-Subscription-Region": "global",
        },
        timeout=15,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-translator: throttled (429)")
    resp.raise_for_status()
    payload = resp.json()
    translation = payload[0]["translations"][0]["text"]
    return TranslationResult(
        text=translation,
        source_lang=source,
        target_lang=target,
        char_count=len(text),
    )


def translate_batch(
    texts: Iterable[str],
    *,
    source: str,
    target: str,
) -> list[TranslationResult]:
    """Best-effort batch.

    Translator caps a single request at 100 segments + 50 000 chars;
    the caller is expected to chunk before invoking. The router does
    that today via ``utils.batch_for_translator``.
    """
    import requests

    items = [{"text": t} for t in texts]
    if not items:
        return []
    endpoint = _resolver.endpoint_for("translator").rstrip("/")
    resp = requests.post(
        f"{endpoint}/translate",
        params={"api-version": API_VERSION, "from": source, "to": target},
        json=items,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Region": "global",
        },
        timeout=30,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-translator: throttled (429)")
    resp.raise_for_status()
    out = []
    for src, row in zip(texts, resp.json()):
        out.append(
            TranslationResult(
                text=row["translations"][0]["text"],
                source_lang=source,
                target_lang=target,
                char_count=len(src),
            )
        )
    return out
