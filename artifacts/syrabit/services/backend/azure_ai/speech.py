"""Azure AI Speech wrapper — STT + Neural TTS + Custom Neural Voice.

Two roles in the existing voice chains:

1. **Fallback tier** — when Google STT/TTS throttles or returns
   high-WER for an Indic locale, the chain in
   ``artifacts/syrabit-backend/voice/router.py`` rolls forward to
   the functions here. Selection is feature-flag gated; nothing
   here decides on its own to take over.

2. **"Syra" branded voice** — the opt-in Custom Neural Voice
   profile rendered through ``synthesize(..., voice="syra")``.
   The CNV training corpus lives in S3 (NOT Azure Blob); the
   training job mints a presigned URL the Speech service ingests
   over its public endpoint. CNV runtime usage is billed per
   character — admin panel surfaces the meter.

Custom Neural Voice deployment IDs are created out-of-band in
Speech Studio after voice talent consent + Microsoft access
review. The mapping below stays in code so the gateway can refuse
unknown voices at submit time rather than at synthesis.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Optional

from . import _resolver

# Locale → Neural voice short name. Keep regional voices ordered
# by perceived quality; routing.py picks the first available.
NEURAL_VOICES = {
    "en-IN":  ["en-IN-NeerjaNeural",   "en-IN-PrabhatNeural"],
    "hi-IN":  ["hi-IN-SwaraNeural",    "hi-IN-MadhurNeural"],
    "bn-IN":  ["bn-IN-TanishaaNeural", "bn-IN-BashkarNeural"],
    "as-IN":  ["as-IN-PriyomNeural",   "as-IN-YashicaNeural"],
    "ta-IN":  ["ta-IN-PallaviNeural",  "ta-IN-ValluvarNeural"],
}

# Custom Neural Voice deployments. Empty until the access-review
# clears; the admin panel hides the "Syra voice" toggle while this
# map is empty.
CUSTOM_VOICES: dict[str, str] = {
    # "syra": "syra-cnv-prod-2026q3",
}


@dataclass
class SynthesisResult:
    audio_mp3_b64: str
    voice_short_name: str
    char_count: int
    is_custom_neural: bool


def _token() -> str:
    """Fetch an AAD bearer token scoped to the Speech resource."""
    cred = _resolver.get_credential()
    return cred.get_token("https://cognitiveservices.azure.com/.default").token


def synthesize(
    text: str,
    *,
    locale: str = "en-IN",
    voice: Optional[str] = None,
    rate: str = "0%",
) -> SynthesisResult:
    """Render ``text`` as MP3 via Azure Neural TTS.

    ``voice`` may be a Custom Neural Voice key (e.g. ``"syra"``) or
    a Neural voice short name. ``None`` picks the first regional
    voice for ``locale``.
    """
    import requests

    is_custom = voice in CUSTOM_VOICES
    if is_custom:
        voice_name = CUSTOM_VOICES[voice]
    elif voice:
        voice_name = voice
    else:
        candidates = NEURAL_VOICES.get(locale)
        if not candidates:
            raise ValueError(f"No Neural voice configured for locale {locale!r}")
        voice_name = candidates[0]

    endpoint = _resolver.endpoint_for("speech").rstrip("/")
    ssml = (
        f'<speak version="1.0" xml:lang="{locale}">'
        f'<voice name="{voice_name}">'
        f'<prosody rate="{rate}">{text}</prosody>'
        f"</voice></speak>"
    )

    resp = requests.post(
        f"{endpoint}/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-160kbitrate-mono-mp3",
            "User-Agent": "syrabit-azure-tts/1.0",
        },
        timeout=30,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-speech: throttled (429)")
    resp.raise_for_status()

    return SynthesisResult(
        audio_mp3_b64=base64.b64encode(resp.content).decode("ascii"),
        voice_short_name=voice_name,
        char_count=len(text),
        is_custom_neural=is_custom,
    )


def transcribe(audio_bytes: bytes, *, locale: str = "en-IN") -> str:
    """Synchronous STT for short clips (<= 60 s).

    Long-form transcription uses the Batch Transcription API which
    writes results to S3 via presigned URL — handled by the
    ``stt_long_form`` cron job, not this synchronous path.
    """
    import requests

    endpoint = _resolver.endpoint_for("speech").rstrip("/")
    resp = requests.post(
        f"{endpoint}/speech/recognition/conversation/cognitiveservices/v1"
        f"?language={locale}&format=detailed",
        data=audio_bytes,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
        },
        timeout=60,
    )
    if resp.status_code == 429:
        raise RuntimeError("azure-speech: STT throttled (429)")
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("RecognitionStatus") != "Success":
        raise RuntimeError(f"azure-speech: STT failed ({payload.get('RecognitionStatus')})")
    return payload.get("DisplayText", "")
