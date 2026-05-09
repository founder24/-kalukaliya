"""Azure-native advanced AI feature wrappers (Task #338).

Each submodule wraps one Azure AI service as an *additional* path in
the existing provider chain — never as a replacement. Selection,
failover, and feature-flag gating live in the gateway / OCR / TTS /
moderation / search / personalisation modules; this package only
exposes thin SDK surfaces with consistent telemetry and managed-
identity auth.

Hosting-plan invariants (carried over from
``docs/infra/cloud-allocation-plan.md``):

* **No Azure Blob Storage.** Any artefact persistence references S3
  through a presigned URL passed at call time.
* **No static API keys.** All accounts run with ``local_auth_enabled
  = false`` (see ``infra/azure/ai-services.tf``); auth is the
  user-assigned managed identity attached to the Container Apps Job
  or backend container, fetched via ``DefaultAzureCredential``.
* **Endpoint URLs come from Key Vault**, not env vars, so rotation
  matches the Phase 1c secret model. ``_resolver.endpoint_for``
  caches per-process.

Submodules:

* ``openai`` — GPT-4o / GPT-4o-mini / o-series under Azure quota,
  registered in the AI Gateway as ``llm/azure-openai``.
* ``document_intelligence`` — Layout-aware OCR for past papers and
  marks sheets.
* ``vision`` — General image analysis (tags, captions, OCR) used as
  a fallback in the OCR + image-understanding chain.
* ``content_safety`` — Synchronous moderation on chat I/O, comments
  and uploaded text; integrates with the admin moderation queue.
* ``language`` — Summaries, key phrases, NER, and PII detection for
  Topic Discovery and the SEO Manager.
* ``search`` — Hybrid keyword + vector retriever; alternative to
  Pinecone in the RAG path under a feature flag.
* ``anomaly_detector`` — Watches credit-burn, error-rate, and R2-
  cost time series; emits the ``ai_anomaly_detected`` App Insights
  metric that the Slack action group fires from.
* ``personalizer`` — Rank/reward loop powering the next-best-quiz
  surface under a feature flag, A/B against the deterministic
  ranker.
"""

from ._resolver import endpoint_for, get_credential

__all__ = ["endpoint_for", "get_credential"]
