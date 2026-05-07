"""providers.aws_native — Task #337 thin wrappers around AWS-managed services.

Every entry below is wired as an *additional* path in an existing failover
chain. Nothing here replaces a primary provider; the underlying chain still
falls through to its existing GCP / Sarvam / ElevenLabs primary if the AWS
path is unconfigured or fails.

Modules
-------
``aws_polly``       Polly Neural / Generative TTS — third-tier voice fallback.
``aws_transcribe``  Transcribe streaming STT — third-tier voice fallback.
``aws_textract``    Structured-document OCR (past papers, marks sheets).
``aws_rekognition`` Image moderation pre-R2 commit (closed-by-default on outage).
``aws_comprehend``  Sampled PII + sentiment job (analytics warehouse only).
``aws_translate``   Indic ↔ EN translate fallback when Sarvam returns 429/5xx.
``aws_personalize`` Recs surface (home + Continue Learning) with deterministic fallback.
``aws_fraud``       Risk score on signup + payment intent.
(Task #491 — legacy AWS embed/rerank module removed.)

Calling pattern
---------------
boto3 picks up credentials via the AWS managed-identity chain — the DO API
container assumes the per-feature ``syrabit-aws-native-<feature>-prod`` role
declared in ``infra/aws/aws-native-features.tf`` via the GitHub-OIDC
federation. No static AWS access keys are issued. When boto3 isn't installed
(dev shells, tests) every helper raises ``RuntimeError`` so the caller's
fallback chain advances.

Admin toggles
-------------
``ENABLED_FLAGS`` is the in-memory toggle map ``routes/admin_aws_native.py``
mutates from ``POST /admin/aws-native/toggle``. Callers must check
``is_enabled(<key>)`` before invoking the helper so the admin off-switch
works without redeploys. The default is *on* for every feature so a fresh
container behaves like the runbook describes.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("providers.aws_native")

try:
    import boto3  # type: ignore
    from botocore.exceptions import BotoCoreError, ClientError  # type: ignore
    _HAS_BOTO3 = True
except ImportError:  # pragma: no cover — dev shells without boto3
    boto3 = None  # type: ignore
    BotoCoreError = ClientError = Exception  # type: ignore
    _HAS_BOTO3 = False


FEATURE_KEYS: Tuple[str, ...] = (
    "polly",
    "transcribe",
    "textract",
    "rekognition",
    "comprehend",
    "translate",
    "personalize",
    "fraud_detector",
)

# Region mapping mirrors infra/aws/aws-native-features.tf locals.
PRIMARY_REGION = os.environ.get("AWS_NATIVE_PRIMARY_REGION", "ap-south-1")
SECONDARY_REGION = os.environ.get("AWS_NATIVE_SECONDARY_REGION", "us-east-1")

_FEATURE_REGIONS: Dict[str, str] = {
    "polly":           PRIMARY_REGION,
    "transcribe":      PRIMARY_REGION,
    "textract":        PRIMARY_REGION,
    "rekognition":     PRIMARY_REGION,
    "comprehend":      PRIMARY_REGION,
    "translate":       PRIMARY_REGION,
    "personalize":     PRIMARY_REGION,
    "fraud_detector":  PRIMARY_REGION,
}

# Defaults to on per the runbook; admin can flip per feature without redeploy.
ENABLED_FLAGS: Dict[str, bool] = {k: True for k in FEATURE_KEYS}
_TOGGLE_LOCK = threading.Lock()


def is_enabled(key: str) -> bool:
    """True if the AWS-native feature is allowed to run.

    Disabled by env var ``AWS_NATIVE_<KEY>_DISABLED=1`` or by an admin
    toggle persisted in :data:`ENABLED_FLAGS`. The env-var path takes
    precedence so deploy-time guardrails always beat runtime toggles.
    """
    if os.environ.get(f"AWS_NATIVE_{key.upper()}_DISABLED", "").strip() in ("1", "true", "yes"):
        return False
    return bool(ENABLED_FLAGS.get(key, False))


def set_enabled(key: str, enabled: bool) -> bool:
    """Mutate the in-memory enabled flag. Returns the new value.

    Raises ``ValueError`` for unknown features so the admin route returns
    a 4xx instead of silently writing to a typo'd key.
    """
    if key not in FEATURE_KEYS:
        raise ValueError(f"unknown AWS-native feature: {key!r}")
    with _TOGGLE_LOCK:
        ENABLED_FLAGS[key] = bool(enabled)
        return ENABLED_FLAGS[key]


def is_configured() -> bool:
    """True when boto3 is importable. Distinct from per-feature enable."""
    return _HAS_BOTO3


# ── Outcome telemetry (admin panel reads from here) ──────────────────────
#
# Per-feature rolling counters (last 5 min) the admin panel renders. We
# keep them in-process for simplicity — every replica reports its own
# slice and the admin route just reads the local counters; the
# CloudWatch dashboard is the source of truth for cross-replica
# aggregates.

@dataclass
class _Telemetry:
    invocations: int = 0
    failures: int = 0
    throttles: int = 0
    latency_ms: List[float] = field(default_factory=list)
    last_error: Optional[str] = None

    def record(self, ok: bool, latency_ms: float, error: Optional[BaseException] = None) -> None:
        self.invocations += 1
        if not ok:
            self.failures += 1
            self.last_error = type(error).__name__ if error else "unknown"
            if error is not None and "Throttl" in type(error).__name__:
                self.throttles += 1
        # bound the latency window so memory doesn't grow without bound
        self.latency_ms.append(latency_ms)
        if len(self.latency_ms) > 256:
            self.latency_ms = self.latency_ms[-256:]

    def snapshot(self) -> Dict[str, Any]:
        if not self.latency_ms:
            p95 = None
        else:
            ordered = sorted(self.latency_ms)
            p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
        throttled_pct = (self.throttles / self.invocations) if self.invocations else 0.0
        return {
            "invocations": self.invocations,
            "failures": self.failures,
            "throttledPct": round(throttled_pct, 4),
            "p95LatencyMs": round(p95, 1) if p95 is not None else None,
            "lastError": self.last_error,
        }


_TELEMETRY: Dict[str, _Telemetry] = {k: _Telemetry() for k in FEATURE_KEYS}
_TELEMETRY_LOCK = threading.Lock()


def record_outcome(key: str, ok: bool, latency_ms: float, error: Optional[BaseException] = None) -> None:
    """Append an outcome to the per-feature rolling window."""
    if key not in _TELEMETRY:
        return
    with _TELEMETRY_LOCK:
        _TELEMETRY[key].record(ok, latency_ms, error)


def telemetry_snapshot() -> Dict[str, Dict[str, Any]]:
    """Return a JSON-friendly snapshot of every feature's rolling window."""
    with _TELEMETRY_LOCK:
        return {k: t.snapshot() for k, t in _TELEMETRY.items()}


def reset_telemetry() -> None:
    """Used by tests to keep the rolling window deterministic."""
    with _TELEMETRY_LOCK:
        for k in FEATURE_KEYS:
            _TELEMETRY[k] = _Telemetry()


# ── boto3 client cache ───────────────────────────────────────────────────

_CLIENT_CACHE: Dict[Tuple[str, str], Any] = {}
_CLIENT_LOCK = threading.Lock()


def _client(service: str, feature: str):
    """Return a cached boto3 client for the per-feature region.

    Raises ``RuntimeError`` when boto3 is not importable so the caller's
    failover chain catches the exception and excludes the AWS path.
    """
    if not _HAS_BOTO3:
        raise RuntimeError(f"boto3 not installed — AWS {service} path unavailable")
    region = _FEATURE_REGIONS.get(feature, PRIMARY_REGION)
    cache_key = (service, region)
    with _CLIENT_LOCK:
        if cache_key not in _CLIENT_CACHE:
            _CLIENT_CACHE[cache_key] = boto3.client(service, region_name=region)
        return _CLIENT_CACHE[cache_key]


def _reset_client_cache() -> None:
    """Used by tests to drop cached clients between cases."""
    with _CLIENT_LOCK:
        _CLIENT_CACHE.clear()


# ── Cost Explorer hydration (admin spendUsd7d) ───────────────────────────
#
# Cost Explorer is global (us-east-1 only). We map AWS service codes to
# our ``FEATURE_KEYS`` so the admin tile shows real per-feature 7-day
# spend instead of a placeholder. Failure is non-fatal — the route
# falls back to ``None`` so the tile renders "—".

_COST_EXPLORER_SERVICE_MAP: Dict[str, str] = {
    "Amazon Polly":                          "polly",
    "Amazon Transcribe":                     "transcribe",
    "Amazon Textract":                       "textract",
    "Amazon Rekognition":                    "rekognition",
    "Amazon Comprehend":                     "comprehend",
    "Amazon Translate":                      "translate",
    "Amazon Personalize":                    "personalize",
    "Amazon Fraud Detector":                 "fraud_detector",
}


def fetch_cost_explorer_7d() -> Dict[str, Optional[float]]:
    """Return ``{feature_key: usd_last_7d}`` from AWS Cost Explorer.

    Cost Explorer must be queried in ``us-east-1``; the 7-day window
    matches what the admin runbook expects. Raises ``RuntimeError``
    when boto3 is unavailable so the caller treats every value as
    ``None`` (which the tile renders as "—" — explicit, not silent).
    """
    if not _HAS_BOTO3:
        raise RuntimeError("boto3 not installed — Cost Explorer unavailable")
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=7)
    client = boto3.client("ce", region_name="us-east-1")
    resp = client.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    totals: Dict[str, float] = {k: 0.0 for k in FEATURE_KEYS}
    for day in resp.get("ResultsByTime", []) or []:
        for grp in day.get("Groups", []) or []:
            keys = grp.get("Keys", []) or []
            if not keys:
                continue
            feature = _COST_EXPLORER_SERVICE_MAP.get(keys[0])
            if not feature:
                continue
            amt = float(grp.get("Metrics", {}).get("UnblendedCost", {}).get("Amount", 0.0) or 0.0)
            totals[feature] = totals.get(feature, 0.0) + amt
    return {k: round(v, 2) for k, v in totals.items()}


# ── Per-feature helpers ──────────────────────────────────────────────────
#
# Every helper observes the same contract:
#   * Raises ``RuntimeError("disabled")`` when ``is_enabled`` is False.
#   * Raises ``RuntimeError`` (catchable) on any AWS error so the caller
#     can advance to the next provider.
#   * Records outcome + latency into the per-feature telemetry window.

def _timed(key: str, fn, *args, **kwargs):
    import time
    if not is_enabled(key):
        raise RuntimeError(f"aws_native:{key} disabled")
    start = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        record_outcome(key, True, (time.monotonic() - start) * 1000)
        return result
    except (BotoCoreError, ClientError, RuntimeError) as exc:
        record_outcome(key, False, (time.monotonic() - start) * 1000, exc)
        raise RuntimeError(f"aws_native:{key} failed: {exc}") from exc


# 3.2 Polly — third-tier TTS

def synthesize_polly(text: str, *, voice_id: str = "Joanna", language_code: str = "en-US") -> bytes:
    """Synthesize ``text`` to MP3 via Amazon Polly.

    Voice defaults to the neural English ``Joanna``; callers should pass
    ``voice_id="Kajal"`` (Hindi) or ``voice_id="Aditi"`` (Tamil) for the
    documented Indic coverage. Polly does not yet ship Assamese — Sarvam
    stays primary for ``as``.
    """
    def _call() -> bytes:
        client = _client("polly", "polly")
        resp = client.synthesize_speech(
            Text=text,
            VoiceId=voice_id,
            OutputFormat="mp3",
            Engine="neural",
            LanguageCode=language_code,
        )
        body = resp.get("AudioStream")
        if body is None:
            raise RuntimeError("polly: empty AudioStream")
        return body.read()
    return _timed("polly", _call)


# 3.3 Transcribe — third-tier STT (synchronous job for short clips)

def transcribe_audio(s3_uri: str, *, language_code: str = "en-IN", job_name: Optional[str] = None) -> str:
    """Submit an async transcription job. Returns the job name.

    The caller polls ``GetTranscriptionJob`` separately. We do not block
    on a streaming socket here because the existing voice.py loop calls
    Transcribe only as a third-tier fallback after Deepgram + Google
    Chirp; the extra round-trip is acceptable in that path.
    """
    import uuid
    name = job_name or f"syrabit-transcribe-{uuid.uuid4().hex[:12]}"

    def _call() -> str:
        client = _client("transcribe", "transcribe")
        client.start_transcription_job(
            TranscriptionJobName=name,
            LanguageCode=language_code,
            Media={"MediaFileUri": s3_uri},
            MediaFormat=s3_uri.rsplit(".", 1)[-1] if "." in s3_uri else "wav",
        )
        return name
    return _timed("transcribe", _call)


# 3.4 Textract — structured-document OCR

def analyze_document(s3_bucket: str, s3_key: str, *, feature_types: Iterable[str] = ("TABLES", "FORMS")) -> Dict[str, Any]:
    """Run Textract ``AnalyzeDocument`` with the given feature types.

    Returned blocks are written next to the original upload by the OCR
    pipeline branch (see runbook §3.4). A ``RuntimeError`` here causes
    the pipeline to fall back to the generic Vision OCR path with a
    ``degraded=true`` flag stored on the result row.
    """
    def _call() -> Dict[str, Any]:
        client = _client("textract", "textract")
        return client.analyze_document(
            Document={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}},
            FeatureTypes=list(feature_types),
        )
    return _timed("textract", _call)


def analyze_document_bytes(image_bytes: bytes, *, feature_types: Iterable[str] = ("TABLES", "FORMS")) -> Dict[str, Any]:
    """Run Textract ``AnalyzeDocument`` synchronously on raw bytes.

    Used by the PYQ structured-OCR branch (runbook §3.4): for small
    single-page papers we pass the bytes inline rather than staging
    via S3. Documents > ~10 MB or multi-page PDFs must go through the
    S3-backed ``analyze_document`` path because Textract's sync API
    enforces the size limit.
    """
    def _call() -> Dict[str, Any]:
        client = _client("textract", "textract")
        return client.analyze_document(
            Document={"Bytes": image_bytes},
            FeatureTypes=list(feature_types),
        )
    return _timed("textract", _call)


# 3.5 Rekognition — image moderation

# Labels that trigger a quarantine. Confidence ≥ 70 % is the runbook
# default; admin can shift the threshold via the toggle endpoint.
MODERATION_BLOCK_LABELS = frozenset({
    "Explicit Nudity",
    "Violence",
    "Hate Symbols",
    "Drugs & Tobacco Paraphernalia",
})
MODERATION_DEFAULT_THRESHOLD = 70.0


def moderate_image(image_bytes: bytes, *, threshold: float = MODERATION_DEFAULT_THRESHOLD) -> Dict[str, Any]:
    """Detect moderation labels on the raw bytes of an uploaded image.

    Returns ``{"flagged": bool, "labels": [...], "max_confidence": float}``.
    The caller decides what to do with ``flagged=True`` — the runbook
    routes them to the admin moderation queue rather than auto-blocking.
    """
    def _call() -> Dict[str, Any]:
        client = _client("rekognition", "rekognition")
        resp = client.detect_moderation_labels(
            Image={"Bytes": image_bytes},
            MinConfidence=threshold,
        )
        labels = resp.get("ModerationLabels", []) or []
        flagged = False
        max_conf = 0.0
        for lbl in labels:
            name = lbl.get("Name") or ""
            parent = lbl.get("ParentName") or ""
            conf = float(lbl.get("Confidence") or 0.0)
            max_conf = max(max_conf, conf)
            if conf >= threshold and (name in MODERATION_BLOCK_LABELS or parent in MODERATION_BLOCK_LABELS):
                flagged = True
        return {
            "flagged": flagged,
            "labels": labels,
            "max_confidence": max_conf,
        }
    return _timed("rekognition", _call)


# 3.6 Comprehend — sampled PII + sentiment (background job only)

def detect_pii(text: str, *, language_code: str = "en") -> List[Dict[str, Any]]:
    def _call() -> List[Dict[str, Any]]:
        client = _client("comprehend", "comprehend")
        resp = client.detect_pii_entities(Text=text, LanguageCode=language_code)
        return resp.get("Entities", []) or []
    return _timed("comprehend", _call)


def detect_sentiment(text: str, *, language_code: str = "en") -> Dict[str, Any]:
    def _call() -> Dict[str, Any]:
        client = _client("comprehend", "comprehend")
        resp = client.detect_sentiment(Text=text, LanguageCode=language_code)
        return {
            "sentiment": resp.get("Sentiment"),
            "scores": resp.get("SentimentScore", {}),
        }
    return _timed("comprehend", _call)


# 3.7 Translate — Sarvam fallback

def translate_text(text: str, *, source_lang: str = "auto", target_lang: str = "en") -> str:
    def _call() -> str:
        client = _client("translate", "translate")
        resp = client.translate_text(
            Text=text,
            SourceLanguageCode=source_lang,
            TargetLanguageCode=target_lang,
        )
        out = resp.get("TranslatedText")
        if not out:
            raise RuntimeError("translate: empty TranslatedText")
        return out
    return _timed("translate", _call)


# 3.8 Personalize — recs (with deterministic fallback)

def get_recommendations(
    *,
    campaign_arn: str,
    user_id: str,
    num_results: int = 12,
) -> List[str]:
    """Fetch personalised item ids. Empty list when the campaign returns
    < 3 items — the home rail then renders the deterministic ranker."""
    def _call() -> List[str]:
        client = _client("personalize-runtime", "personalize")
        resp = client.get_recommendations(
            campaignArn=campaign_arn,
            userId=user_id,
            numResults=num_results,
        )
        items = [it.get("itemId") for it in resp.get("itemList", []) if it.get("itemId")]
        return items if len(items) >= 3 else []
    return _timed("personalize", _call)


def deterministic_recommendations(items: List[Dict[str, Any]], *, num_results: int = 12) -> List[str]:
    """Always-on fallback ranker: popularity × recency × subject affinity.

    The dispatcher in the home page handler calls this when Personalize
    is disabled, fails, returns < 3 items, or the user has no event
    history (cold start). Pure function — no AWS calls — so it stays
    available even during a full AWS outage.
    """
    def _score(it: Dict[str, Any]) -> float:
        pop = float(it.get("popularity_score", 0.0))
        rec = float(it.get("recency_score", 0.0))
        aff = float(it.get("subject_affinity", 0.0))
        return pop * 0.5 + rec * 0.3 + aff * 0.2

    ranked = sorted(items, key=_score, reverse=True)
    return [it.get("id") for it in ranked[:num_results] if it.get("id")]


# 3.9 Fraud Detector — risk score

@dataclass(frozen=True)
class FraudOutcome:
    score: float           # 0–1000
    outcome: str           # "approve" | "review" | "block"
    raw: Dict[str, Any] = field(default_factory=dict)


def get_fraud_score(
    *,
    detector_id: str,
    detector_version_id: str,
    event_id: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    event_variables: Dict[str, str],
) -> FraudOutcome:
    """Score a signup or payment event. Returns approve/review/block.

    Outage policy mirrors the runbook: the caller catches the
    ``RuntimeError`` and treats the score as 0 (allow) with an
    ``fd_unavailable=True`` flag for analytics backfill.
    """
    import time
    def _call() -> FraudOutcome:
        client = _client("frauddetector", "fraud_detector")
        resp = client.get_event_prediction(
            detectorId=detector_id,
            detectorVersionId=detector_version_id,
            eventId=event_id,
            eventTypeName=event_type,
            entities=[{"entityType": entity_type, "entityId": entity_id}],
            eventTimestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            eventVariables=event_variables,
        )
        scores = resp.get("modelScores", []) or []
        score = float(scores[0].get("scores", {}).get("syrabit_risk_score", 0.0)) if scores else 0.0
        outcomes = resp.get("ruleResults", []) or []
        # Pick the most severe outcome label — rule order is defined
        # admin-side; we trust the first matching rule.
        outcome_label = "approve"
        for r in outcomes:
            for o in (r.get("outcomes") or []):
                if o in ("block", "review"):
                    outcome_label = o
                    break
            if outcome_label != "approve":
                break
        return FraudOutcome(score=score, outcome=outcome_label, raw=resp)
    return _timed("fraud_detector", _call)


# Task #491 — 3.1 legacy AWS embed + rerank module removed.
# Embedding stack collapsed to workers_ai_custom (Gemma-300M + Qwen3-0.6B
# mean-pool, 1024-dim). Rerank is Pinecone-only.
