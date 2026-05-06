# infra/gcp

GCP is retained **only** for inference APIs (Vertex AI / Gemini, Vision,
STT, TTS, Discovery Engine, Web Risk). All GCP **hosting / cron / CI**
workloads were decommissioned in Task #335 — see
`infra/v4-locked-architecture.md` for the inventory and the dates each
resource was removed.

This directory must remain limited to AI-API-related configuration
(IAM bindings, service-account key rotation, quota requests). Any new
hosting, queue, scheduler, or build configuration belongs under
`infra/aws/` or `infra/azure/`.
