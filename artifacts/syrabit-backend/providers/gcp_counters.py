"""In-process call counters for GCP provider spend estimation.

Counters are async-safe (Python GIL + single asyncio event loop — no lock needed
for simple += on CPython). Counters reset automatically at the start of each
calendar month. They are NOT persisted to disk: a process restart zeroes all
counters (the admin panel displays a caveat when process_uptime < 1 day).

Services tracked:
  stt       — Google Cloud Speech-to-Text v2 Chirp_2  ($0.016 / min)
  tts       — Google Cloud TTS Neural2                ($16 / 1M chars)
  translate — Google Cloud Translation v3             ($20 / 1M chars)
  vision    — Google Cloud Vision DOCUMENT_TEXT_DET.  ($1.50 / 1K images)
  embed     — Vertex AI text-embedding-004            ($0.00013 / 1K chars)
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timezone

_PRICES = {
    "stt":       {"unit": "minutes",  "usd_per_unit": 0.016},
    "tts":       {"unit": "1M_chars", "usd_per_unit": 16.0},
    "translate": {"unit": "1M_chars", "usd_per_unit": 20.0},
    "vision":    {"unit": "1K_images","usd_per_unit": 1.50},
    "embed":     {"unit": "1K_chars", "usd_per_unit": 0.13},
}

_counters: dict[str, dict] = {
    "stt":       {"calls": 0, "audio_minutes": 0.0},
    "tts":       {"calls": 0, "chars": 0},
    "translate": {"calls": 0, "chars": 0},
    "vision":    {"calls": 0, "images": 0},
    "embed":     {"calls": 0, "chars": 0},
}

_process_start: float = time.time()
_reset_month: int = datetime.now(tz=timezone.utc).month
_reset_year: int = datetime.now(tz=timezone.utc).year


def _check_reset() -> None:
    global _reset_month, _reset_year
    now = datetime.now(tz=timezone.utc)
    if now.month != _reset_month or now.year != _reset_year:
        for svc in _counters.values():
            for k in list(svc.keys()):
                svc[k] = 0.0 if isinstance(svc[k], float) else 0
        _reset_month = now.month
        _reset_year = now.year


def inc_stt(audio_minutes: float) -> None:
    """Increment STT counters after a successful Chirp_2 transcription."""
    _check_reset()
    _counters["stt"]["calls"] += 1
    _counters["stt"]["audio_minutes"] += audio_minutes


def inc_tts(chars: int) -> None:
    """Increment TTS counters after a successful Neural2 synthesis."""
    _check_reset()
    _counters["tts"]["calls"] += 1
    _counters["tts"]["chars"] += chars


def inc_translate(chars: int) -> None:
    """Increment Translation counters after a successful v3 translate."""
    _check_reset()
    _counters["translate"]["calls"] += 1
    _counters["translate"]["chars"] += chars


def inc_vision() -> None:
    """Increment Vision counters after a successful DOCUMENT_TEXT_DETECTION."""
    _check_reset()
    _counters["vision"]["calls"] += 1
    _counters["vision"]["images"] += 1


def inc_embed(chars: int) -> None:
    """Increment Vertex Embed counters after a successful embed_text call."""
    _check_reset()
    _counters["embed"]["calls"] += 1
    _counters["embed"]["chars"] += chars


def _estimated_spend_usd(service: str) -> float:
    """Compute estimated spend in USD from current counter values."""
    c = _counters[service]
    p = _PRICES[service]
    unit = p["unit"]
    rate = p["usd_per_unit"]
    if unit == "minutes":
        return round(c.get("audio_minutes", 0.0) * rate, 4)
    if unit == "1M_chars":
        return round(c.get("chars", 0) / 1_000_000 * rate, 4)
    if unit == "1K_images":
        return round(c.get("images", 0) / 1_000 * rate, 4)
    if unit == "1K_chars":
        return round(c.get("chars", 0) / 1_000 * rate, 4)
    return 0.0


def snapshot() -> dict:
    """Return a snapshot of current counters with spend estimates.

    Does NOT trigger a month reset — safe to call at any frequency.
    """
    _check_reset()
    now = datetime.now(tz=timezone.utc)
    process_uptime_h = round((time.time() - _process_start) / 3600, 2)

    services: dict = {}
    total_spend = 0.0
    for svc, raw in _counters.items():
        spend = _estimated_spend_usd(svc)
        total_spend += spend
        services[svc] = {
            **copy.copy(raw),
            "estimated_spend_usd": spend,
            "unit": _PRICES[svc]["unit"],
            "rate": _PRICES[svc]["usd_per_unit"],
        }

    return {
        "period": f"{now.year}-{now.month:02d}",
        "process_uptime_hours": process_uptime_h,
        "counters_reset_on_restart": True,
        "services": services,
        "total_estimated_spend_usd": round(total_spend, 4),
    }
