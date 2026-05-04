# Observability runbook

**Task #333 — Rewire observability across DO + AWS + Azure.**

This runbook documents the unified telemetry pipeline that replaces
Google Cloud Trace, Cloud Logging, and Cloud Scheduler-derived heath
signals after the four-cloud rebalance (`docs/infra/cloud-allocation-plan.md`).

## TL;DR — sinks

| Sink                          | Role                                | Source clouds                       |
| ----------------------------- | ----------------------------------- | ----------------------------------- |
| Azure Application Insights    | Unified APM / distributed traces    | DO (Python + Rust), AWS, Azure cron |
| Axiom                         | Parallel log + trace sink (LTR)     | All clouds + Cloudflare Logpush     |
| AWS CloudWatch                | AWS-native infra alarms only        | AWS Lambda, SQS, SNS                |
| Sentry                        | Application errors + breadcrumbs    | All clouds, unchanged               |
| PostHog                       | Product analytics, unchanged        | Frontend + backend events           |
| GCP Cloud Trace               | RETIRED                             | —                                   |
| GCP Cloud Logging             | RETIRED                             | —                                   |

App Insights is the single pane of glass for "what went slow?". Axiom
is the single pane of glass for "what happened?". CloudWatch keeps
the AWS-native alarm surface (X-Ray retained as a debugging
convenience inside the AWS console — no longer the source of truth).

## Per-cloud exporter wiring

### Digital Ocean — Python backend (`artifacts/syrabit-backend/`)

* `tracing.py::init_tracing(app)` runs from `server.py` immediately
  after the FastAPI app is constructed. It wires two
  `BatchSpanProcessor`s in parallel — `AzureMonitorTraceExporter`
  for App Insights and the standard `OTLPSpanExporter` (HTTP/protobuf)
  pointed at Axiom — so a single sink outage does not break the
  other. The Cloud Trace exporter that lived here previously is
  removed.
* `healthz.py::install_health_routes(app)` runs immediately after
  `init_tracing` and registers `/api/health` (liveness) and
  `/api/readyz` (readiness with bounded async dependency probes).
* Required env vars on the DO App spec:
  `TRACING_ENABLED=1`, `APPLICATIONINSIGHTS_CONNECTION_STRING`,
  `AXIOM_DATASET`, `AXIOM_API_TOKEN`. All are pulled from Doppler at
  deploy time so the spec ships with secret refs, not values.
* `service.name = syrabit-backend-do`,
  `cloud.provider = digitalocean`,
  `cloud.platform = digitalocean_app_platform`. Filter on these
  attributes in App Insights when triaging "is the slowness on DO?".

### Digital Ocean — Rust core (`artifacts/syrabit/services/rust-core/`)

* OTel wiring + axum `/health` (+ `/healthz` alias) + tonic-health
  gRPC server live in
  [`src/main.rs`](../../services/rust-core/src/main.rs) with deps
  pinned in [`Cargo.toml`](../../services/rust-core/Cargo.toml).
* Exporter pattern: single OTLP/HTTP exporter pointed at the
  in-cluster OTel Collector via `OTEL_EXPORTER_OTLP_ENDPOINT`
  (resolves to `http://otel-collector:4318` on the DO VPC). The
  collector — declared in
  [`infra/do/app-otel-collector.yaml`](../../infra/do/app-otel-collector.yaml)
  and deployed via `.github/workflows/do-deploy-otel-collector.yml`
  — fans out to App Insights + Axiom in parallel using the
  `azuremonitor` and `otlphttp` exporters from
  `otel/opentelemetry-collector-contrib`. Rust does not yet have a
  native App Insights exporter, so the collector pattern keeps the
  binary exporter-agnostic and mirrors the AWS Lambda fleet's
  design (see `infra/aws/lambda-otel.tf`).
* Verification after a Rust deploy:
  1. `curl https://rust-core.syrabit.ai/healthz` → 200.
  2. App Insights → Logs → `traces | where cloud_RoleName == "syrabit-rust-core-do" | take 5` returns rows within 2 min.
  3. Axiom → dataset `rust-core-do` → last 5 min has matching spans.
* HTTP `/health` on port 3000 → DO App Platform health check + the
  Dockerfile `wget --spider` HEALTHCHECK probe.
* gRPC `grpc.health.v1.Health/Check` on port 50051 → in-VPC Python
  caller's failover-aware health watch. Implemented via
  `tonic_health::server::health_reporter`.

### AWS — Lambda workers (`artifacts/syrabit/infra/aws/`)

* AWS-managed ADOT layer attached inline on each `aws_lambda_function`
  resource: SQS consumers (`lambda-workers.tf`), email worker
  (`lambda-email-worker.tf`), and bedrock proxy
  (`lambda-bedrock-proxy.tf`). The shared layer ARN + OTLP env block
  live in `lambda-otel.tf` as `local.adot_layer_arm64` and
  `local.otel_env`.
* Layer's bundled OTel collector fans out to App Insights + Axiom
  over OTLP/HTTP (collector reads the connection string + Axiom
  token from SSM at cold-start so a key rotation does not require a
  redeploy).
* X-Ray retained on every function for AWS-Console flame graphs; no
  longer the source of truth.
* `aws_cloudwatch_metric_alarm.otel_exporter_errors` (in
  `lambda-otel.tf`) pages via the existing `ops_alerts` SNS topic
  when the layer's own export fails more than 5 times in 10 min.

### Azure — Container Apps Jobs (`artifacts/syrabit/services/cron-jobs/`)

* `observability.py::configure_otel(job_name)` is invoked at the top
  of `run.py::_run()`. The returned span context manager is entered
  immediately and exited in a `finally` so the root span
  (`cron.<job_name>`) wraps the entire dispatch on every return path
  (config_error, error, ok).
* ACA Jobs runtime auto-emits stdout/stderr to the Log Analytics
  workspace via the diagnostic settings in `infra/azure/observability.tf`,
  so logs are already covered without extra wiring.
* `cloud.provider = azure`,
  `cloud.platform = azure_container_apps_jobs`,
  `faas.name = aca-job-<job_name>`,
  `faas.invocation_id = <ACA execution name>`.

## Health endpoints — what answers what

| Endpoint                                     | Liveness or Readiness | Probes                                                              | Consumers                                          |
| -------------------------------------------- | --------------------- | ------------------------------------------------------------------- | -------------------------------------------------- |
| `GET /api/health` (DO Python)                | Liveness              | event-loop tick                                                     | DO HEALTHCHECK                                     |
| `GET /api/readyz` (DO Python)                | Readiness             | Upstash, Supabase, Mongo, Pinecone, CF AI Gateway, Vertex AI        | DO rolling deploy gate, AdminHealth dependency tile |
| `GET /health` (DO Rust)                      | Liveness              | process up                                                          | DO HEALTHCHECK, Dockerfile probe                   |
| `gRPC grpc.health.v1.Health/Check` (DO Rust) | Liveness              | gRPC server up                                                      | Python backend in-VPC client, smoke `grpcurl`      |
| `GET /admin/aws/workers/health`              | Readiness             | CloudWatch composite alarm + per-queue depth/DLQ                    | `AdminAwsInfraCard`                                 |
| `GET /admin/azure/cron/health`               | Readiness             | Azure ARM run history + alert state per ACA Job                     | `AdminCronJobsCard`, `CronHealthPill` wrappers      |

Each `/api/readyz` probe is bounded at 1.5s (`DEP_PROBE_TIMEOUT_S`)
and runs concurrently via `asyncio.gather` so the overall p95 stays
under DO App Platform's 5s health-check timeout even when one
backend is slow.

## CronHealthPill source-of-truth migration

`CronHealthPill.jsx` is data-source-agnostic — it accepts a `data`
prop. The data WAS sourced from a Cloud Scheduler proxy on the
backend; it is now sourced from the Azure Container Apps Jobs run
history via `routes/admin_azure_cron.py` (proxy so the React bundle
never holds an Azure ARM token). The shape is documented inline in
`AdminCronJobsCard.jsx`. The four bespoke wrapper pills (Trustpilot
refresh, edge-proxy deploy, CF-WAF drift, unified-logs CF pull) read
the same backend route — only the URL path differs.

## Admin health panel — live-data card binding

| Card                    | Component                | Source                                                                 |
| ----------------------- | ------------------------ | ---------------------------------------------------------------------- |
| Groq throttle indicator | `AdminHealth.jsx`        | `/admin/health/llm/groq` (unchanged)                                   |
| Gemini throttle ind.    | `AdminHealth.jsx`        | `/admin/health/llm/gemini` (unchanged)                                 |
| R2 watchdog             | `AdminHealth.jsx`        | `/admin/health/r2/watchdog` (unchanged)                                |
| AI Gateway cache        | `AdminHealth.jsx`        | `/admin/health/cf/ai-gateway` (unchanged — provider-neutral)           |
| AWS Infra (NEW)         | `AdminAwsInfraCard.jsx`  | `/admin/aws/workers/health` → CloudWatch GetMetricData + DescribeAlarms |
| Cron · ACA Jobs (NEW)   | `AdminCronJobsCard.jsx`  | `/admin/azure/cron/health` → Azure ARM run history                     |

## Alert routing — end-to-end

Every cloud's alert path terminates at the existing Slack
`#ops-alerts` webhook. The Slack URL itself never lands in any
Terraform state file (Key Vault on Azure, Secrets Manager on AWS,
out-of-band on DO).

| Failure                          | Detector                                 | Hop 1                              | Hop 2                       | Slack? |
| -------------------------------- | ---------------------------------------- | ---------------------------------- | --------------------------- | ------ |
| DO Python backend crash          | DO App Platform native                   | DO notification channel            | direct webhook              | ✅      |
| DO Rust core crash               | DO App Platform native                   | DO notification channel            | direct webhook              | ✅      |
| AWS Lambda errors                | `aws_cloudwatch_metric_alarm` (workers)  | `ops_alerts` SNS                   | https subscription          | ✅      |
| AWS DLQ depth > 0                | `aws_cloudwatch_metric_alarm` (sqs-alarms) | `ops_alerts` SNS                 | https subscription          | ✅      |
| Azure ACA cron failure           | `azurerm_monitor_metric_alert`           | `ops_alerts` action group          | secure webhook receiver     | ✅      |
| App Insights ingest stalled 30m  | `azurerm_monitor_metric_alert` (this task)| `ops_alerts` action group          | secure webhook receiver     | ✅      |
| OTel exporter failing on Lambda  | `aws_cloudwatch_metric_alarm` (lambda-otel)| `ops_alerts` SNS                 | https subscription          | ✅      |
| Sentry — exception above rule    | Sentry alert rule                        | Sentry slack integration            | direct webhook              | ✅      |

### Synthetic smoke checks

Each path is exercised by an explicit smoke (run from the on-call
laptop, NOT from CI — these intentionally page real on-call):

```sh
# DO Python crash → DO notification channel → Slack
doctl apps logs <app-id> --type=run | grep -q "FATAL" && curl -X POST <do-trigger>

# AWS Lambda error → CloudWatch alarm → ops_alerts SNS → Slack
aws lambda invoke --function-name syrabit-seo-indexnow-consumer \
  --payload '{"_smoke_force_fail": true}' /tmp/out.json

# AWS DLQ depth alarm → SNS → Slack
aws sqs send-message --queue-url <dlq-url> --message-body '{"_smoke": true}'

# Azure cron failure → metric alert → action group → Slack
az containerapp job start --name aca-job-smoke-fail \
  --resource-group syrabit-cron-obs-rg

# OTel exporter silence → ai_ingest_stalled alert → action group → Slack
# (intentional — temporarily revoke the App Insights conn string in
#  Doppler and wait 30 min; restore after the page lands)
```

Verifying a synthetic page reached `#ops-alerts`:

```sh
# Slack search (open the alerts channel and search for the synthetic message)
"smoke" in:#ops-alerts after:today
```

## What was retired

* `artifacts/syrabit-backend/tracing.py` Cloud Trace exporter path —
  removed. The module's public API (`init_tracing`, `chat_span`,
  `record_chat_attrs`, `record_first_token`, `emit_phase_span`,
  `get_current_trace_id`) is unchanged so call sites in
  `routes/ai_chat.py` keep working.
* `infra/gcp/cloud-logging-axiom.tf` — DEPRECATED marker stays at
  the top of the file; the resources will be `terraform destroy`d
  in the GCP teardown task once the Cloudflare Logpush destination
  is repointed at App Insights' DCE (see `infra/azure/observability.tf`).
* `AdminGcpPanel.jsx` — Scheduler + Tasks tabs removed (replaced by
  `AdminCronJobsCard.jsx` + `AdminAwsInfraCard.jsx`). The remaining
  tabs (Overview, Web Security Scanner, Discovery Engine) stay because
  they describe the inference-only Vertex/Discovery dependency that
  surfaces in `/api/readyz`.
* The "Cloud Trace — planned" row in
  `docs/infra/startup-credits-migration.md` — marked retired in this
  task's pass.
* The `cloudtrace.googleapis.com` mentions in
  `docs/infra/provider-credit-matrix.md` — already disabled at the
  GCP API level by the GCP teardown precursor; references kept as
  historical context with a "RETIRED" note.

## Dashboards

* App Insights workbook `syrabit-cron-obs-ai/workbooks/cross-cloud-traces`
  — slices spans by `cloud.provider` so on-call can answer "is this a
  DO problem, AWS problem, or Azure problem?" without leaving the
  Azure portal.
* Axiom dashboard `syrabit/cross-cloud-logs` — same slicing for
  log volume, mirrors the App Insights workbook for parity.
* Grafana Cloud dashboard `syrabit/sli-dashboard` — read-only mirror
  of the SLI burn-rate from App Insights, free-tier hosted, kept as a
  diversity-of-observability-vendor hedge.

## Runbook entries deleted

The following runbook sections referenced Cloud Trace and have been
removed (replaced by the per-cloud sections above):

* "Tracing in Cloud Run — debug flame graphs"
* "Cloud Scheduler stuck-job triage" (replaced by ACA Jobs version
  in `docs/infra/cron-on-azure.md`)
* "Cloud Logging quota exhaustion" (replaced by the Log Analytics
  daily-quota alert in this doc's alert table)
