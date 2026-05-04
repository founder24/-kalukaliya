# Azure-Native Advanced Features Runbook

**Status:** Live (Phase 5b of ADR-0001)
**Owner:** infra + backend
**Task:** #338
**Companion:** [`../infra/azure-landing-zone.md`](../infra/azure-landing-zone.md), [`../infra/cron-on-azure.md`](../infra/cron-on-azure.md), [`../infra/cloud-allocation-plan.md`](../infra/cloud-allocation-plan.md), [`./providers-architecture.md`](../infra/providers-architecture.md)
**Terraform:** [`../../infra/azure/ai-services.tf`](../../infra/azure/ai-services.tf)
**Backend wrappers:** [`../../services/backend/azure_ai/`](../../services/backend/azure_ai/)
**Admin panel:** [`../../src/components/admin/AdminAzureAiPanel.jsx`](../../src/components/admin/AdminAzureAiPanel.jsx)

---

## 1. Scope

This runbook covers the ten Azure-native AI services lit up on top of
the Phase 1c landing zone (Task #329) and the Phase 4 cron port (Task
#332). Every service is an **additional path** in an existing chain —
none of them replaces an existing GCP / Sarvam / Cohere / Pinecone /
Personalize provider. The hosting plan invariants in
[`../infra/cloud-allocation-plan.md`](../infra/cloud-allocation-plan.md)
carry over verbatim:

- **Azure Blob Storage is not used.** S3 is the sole object store —
  Custom Neural Voice corpora, Document Intelligence inputs, and AI
  Search ingest all reach the service via S3 presigned URLs. The
  Terraform root has zero `azurerm_storage_account` resources.
- **Azure OpenAI handles the GPT-4.1-mini chat/content roles.**
  Bedrock stays Cohere-only.
- **All authentication is managed identity.** Cognitive Services and
  Search both run with `local_auth_enabled = false`; a leaked API key
  cannot be used because the data plane refuses key auth.
- **Endpoint URLs live in Key Vault.** Rotation flows match Phase 1c.
  No API keys are written to Key Vault.

## 2. Feature catalogue

| Key | Service | Role in chain | Failure mode | Admin toggle |
|-----|---------|---------------|--------------|--------------|
| `openai` | Azure OpenAI | Additional LLM target in the AI Gateway routing (`llm/azure-openai`); primary for GPT-4.1-mini chat/content roles | Falls back to direct OpenAI → Bedrock-Cohere → Groq → Gemini per the gateway ladder | `azure.openai.enabled` |
| `speech` | Azure AI Speech | Tier in TTS + STT chains; opt-in "Syra" voice via Custom Neural Voice | Falls back to Google STT/TTS; "Syra" voice silently rolls forward to Neural voice for the locale | `azure.speech.enabled`, `azure.speech.syra_voice` |
| `translator` | Azure AI Translator | Indic ↔ English fallback when Sarvam throttles | Falls back to Bhashini → cached translations → English passthrough | `azure.translator.enabled` |
| `document_intel` | Azure Document Intelligence | Layout-aware OCR for past papers + marks sheets | Falls back to Textract → AI Vision; bulk PDF path stays on Textract | `azure.docintel.enabled` |
| `vision` | Azure AI Vision | Tier in OCR + image-understanding chain | Falls back to Google Vision; OCR-only path falls back to Tesseract | `azure.vision.enabled` |
| `content_safety` | Azure Content Safety | Sync moderation on chat I/O, comments, uploaded text | Borderline scores route to admin moderation queue; Content Safety alone never auto-blocks | `azure.content_safety.enabled` |
| `language` | Azure AI Language | Key phrases / NER / summaries / PII for Topic Discovery + SEO Manager | SEO/Topic surfaces fall back to last cached enrichment; PII redaction in observability sink falls back to regex | `azure.language.enabled` |
| `search` | Azure AI Search | Hybrid keyword + vector retriever, parallel to Pinecone | Retriever switch (`rag.retriever`) feature-flagged: `pinecone` (default), `azure-search`, `shadow` | `rag.retriever` |
| `anomaly_detector` | Azure Anomaly Detector | Watches credit-burn, error-rate, R2-cost time series | Cron job posts `ai_anomaly_detected` App Insights metric → existing Slack action group | `azure.anomaly.enabled` |
| `personalizer` | Azure Personalizer | Next-best-quiz A/B vs deterministic ranker | `recs.next_quiz_provider` flag: `deterministic` (default), `personalizer`, `shadow` | `recs.next_quiz_provider` |

## 3. Identity scope (per feature)

All AI services authenticate via the user-assigned managed identity
`syrabit-cron-jobs-runtime` (created in
`infra/azure/iam-github-oidc.tf`). The role assignments added in
`ai-services.tf`:

| Scope | Role | Why |
|-------|------|-----|
| Each `azurerm_cognitive_account.ai[*]` | `Cognitive Services User` | Data-plane calls only — cannot rotate keys or change SKU |
| `azurerm_search_service.library` | `Search Index Data Contributor` | Indexer cron job upserts documents |
| `azurerm_search_service.library` | `Search Index Data Reader` | Request-path retriever queries |
| `azurerm_application_insights.cron_obs` | `Monitoring Metrics Publisher` (already granted in Phase 1c) | Anomaly Detector cron emits `ai_anomaly_detected` |
| Each `azure-ai-*-endpoint` Key Vault secret | `Key Vault Secrets User` (granted vault-wide in Phase 1c) | Endpoint URL resolution |

A compromised Container Apps Job can call any AI service's data plane
but cannot mint new credentials, rotate the SKU, or change the access
policy. A compromised CI runner has zero AI access — the deploy SP is
not granted any role on these accounts.

## 4. Request-path wiring

Each wrapper in `services/backend/azure_ai/` is *registered* by the
matching router; the Azure path is never selected unilaterally:

```
artifacts/syrabit-backend/
├── ai_gateway/registry.py    → registers azure_ai.openai as `llm/azure-openai`
├── voice/router.py           → adds azure_ai.speech as a TTS + STT tier
├── lang/router.py            → adds azure_ai.translator as Sarvam fallback
├── ocr/router.py             → adds azure_ai.document_intelligence + azure_ai.vision
├── moderation/queue.py       → consumes azure_ai.content_safety verdicts
├── seo/enrich.py             → consumes azure_ai.language outputs
├── rag/retriever.py          → switches on rag.retriever feature flag
├── reco/next_quiz.py         → switches on recs.next_quiz_provider
└── observability/redact.py   → uses azure_ai.language.detect_pii for log scrubbing
```

The `ai_anomaly_detected` cron job lives at
`services/cron-jobs/azure_anomaly.py` and is registered in the
Container Apps Jobs catalogue (`infra/azure/container-apps-jobs.tf`)
on a 5-minute cron.

## 5. Failure modes

Each wrapper raises a typed exception on 429:

- `azure_ai.openai.ProviderThrottled` — gateway treats as "next tier".
- `RuntimeError("azure-<feature>: throttled (429)")` for the rest —
  routers translate to the existing throttle exception and the next
  tier in the chain runs.

There is **no silent fallback inside a wrapper** — selection is the
router's job, so a wrapper failure is always visible in App Insights
and on the admin panel.

## 6. Admin surfacing

`AdminAzureAiPanel.jsx` reads `/admin/azure/ai/health` (proxied by
`routes/admin_azure_ai.py` on the backend) and shows per-feature:

- Enabled toggle (POSTs to `/admin/azure/ai/toggle`).
- 15-minute throttle (429) count.
- p50 / p95 data-plane latency.
- Spend MTD against the per-feature budget.
- Failure mode (string drawn from §2 above).
- Recent anomalies surfaced by Anomaly Detector.

The panel is mounted in `AdminHealth` under the Infrastructure tab
alongside `AdminAwsInfraCard` and `AdminCronJobsCard`.

## 7. Cost visibility

Per-feature spend is pulled from the Azure Cost Management API by the
nightly billing cron (`services/cron-jobs/azure_billing_pull.py`),
joined with the existing unified billing view, and surfaced both on
the admin Azure AI panel and in `AdminMonetization`. Per-feature
budgets are defined in the panel config (not in Terraform — budgets
are tunable at runtime).

## 8. Admin toggles + feature flags

Every Azure AI surface is gated by a runtime flag so a misbehaving
service can be disabled without a deploy:

- Per-service `enabled` flag (read by the matching router).
- `rag.retriever` for the Pinecone vs Azure Search switch.
- `recs.next_quiz_provider` for the Personalizer A/B.
- `azure.speech.syra_voice` for the Custom Neural Voice opt-in.

Flags live in the existing feature-flag store; the panel writes
through `/admin/azure/ai/toggle` which validates and persists.

## 9. Rotation runbook

Endpoint URLs do not rotate (the Cognitive Services account name is
stable). If a service is recreated:

1. `terraform apply` re-creates the account and writes the new
   endpoint URL to Key Vault.
2. The resolver's per-process LRU cache picks up the new URL on next
   container boot. Force a roll by restarting the cron environment.

API keys are intentionally not used; rotation has no key-rotation
step.

## 10. Out of scope

- Hosting / cron / observability infra (Tasks #329 + #332).
- Replacing any existing provider — Azure is *additional* everywhere.
- Automated content-blocking decisions on Content Safety alone.
- Azure Blob Storage usage for any artefact persistence.
