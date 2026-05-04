# Rust core — observability wiring

Phase 5 — Observability rewire (Task #333).

The Rust core runs on DO App Platform alongside the Python backend. It
exports OTel traces to the same two sinks (App Insights primary, Axiom
parallel) and exposes both an HTTP and a gRPC health endpoint so DO's
health check + the in-VPC Python caller can both probe it.

> **Implementation status**: the OTel exporter wiring + axum HTTP
> `/health` + tonic-health gRPC server are now implemented in
> [`src/main.rs`](./src/main.rs); deps are pinned in
> [`Cargo.toml`](./Cargo.toml). The remainder of this doc is the
> design rationale that justifies those choices.

## Cargo dependencies

Add (already pinned in `backend/rust-core/Cargo.toml`):

```toml
opentelemetry            = { version = "0.24", features = ["trace"] }
opentelemetry_sdk        = { version = "0.24", features = ["rt-tokio", "trace"] }
opentelemetry-otlp       = { version = "0.17", features = ["http-proto", "reqwest-client", "trace"] }
opentelemetry-stdout     = "0.5"  # debug fallback
tracing                  = "0.1"
tracing-opentelemetry    = "0.25"
tracing-subscriber       = { version = "0.3", features = ["env-filter", "fmt"] }
tonic-health             = "0.12"  # gRPC `grpc.health.v1.Health` impl
```

Application Insights does not ship a native Rust exporter; we ship
spans via plain OTLP/HTTP to the Application Insights "OTLP ingest"
public endpoint (the connection string parses into `IngestionEndpoint`
+ `InstrumentationKey`). One `BatchSpanProcessor` per sink so a single
sink outage does not block the other.

## Boot sequence (sketch — `src/main.rs`)

```rust
fn init_tracing() -> anyhow::Result<()> {
    let resource = Resource::new(vec![
        KeyValue::new("service.name",        "syrabit-rust-core-do"),
        KeyValue::new("service.namespace",   "syrabit"),
        KeyValue::new("cloud.provider",      "digitalocean"),
        KeyValue::new("cloud.platform",      "digitalocean_app_platform"),
        KeyValue::new("cloud.region",        std::env::var("DO_REGION").unwrap_or("blr1".into())),
        KeyValue::new("service.version",     std::env::var("GIT_SHA").unwrap_or("dev".into())),
    ]);

    let mut tp_builder = TracerProvider::builder().with_resource(resource);

    if let Some(cs) = std::env::var("APPLICATIONINSIGHTS_CONNECTION_STRING").ok() {
        let (endpoint, ikey) = parse_appinsights_conn_string(&cs)?;
        tp_builder = tp_builder.with_batch_exporter(
            opentelemetry_otlp::new_exporter()
                .http()
                .with_endpoint(format!("{}/v2.1/track", endpoint))
                .with_header("x-appinsights-ikey", ikey)
                .build_span_exporter()?,
            opentelemetry_sdk::runtime::Tokio,
        );
    }
    if let (Ok(ds), Ok(tok)) = (std::env::var("AXIOM_DATASET"), std::env::var("AXIOM_API_TOKEN")) {
        tp_builder = tp_builder.with_batch_exporter(
            opentelemetry_otlp::new_exporter()
                .http()
                .with_endpoint("https://api.axiom.co/v1/traces")
                .with_header("Authorization", format!("Bearer {tok}"))
                .with_header("X-Axiom-Dataset", ds)
                .build_span_exporter()?,
            opentelemetry_sdk::runtime::Tokio,
        );
    }
    let tracer = tp_builder.build().tracer("rust-core");
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::from_default_env())
        .with(tracing_subscriber::fmt::layer())
        .with(tracing_opentelemetry::layer().with_tracer(tracer))
        .init();
    Ok(())
}
```

Cloud Trace exporters are intentionally NOT wired — Cloud Trace is
retired alongside GCP hosting (Task #333). The OTLP HTTP exporter is
the only path; both sinks consume it.

## Health endpoints

Two probes — DO probes the HTTP one, the in-VPC Python caller and
`grpcurl` smoke checks probe the gRPC one. Both are read-only: they
do NOT exercise downstream dependencies (those are owned by the
backend's `/api/readyz`). The Rust core's job here is to confirm
"this process is up and serving" — readiness for downstreams is the
backend's responsibility.

### HTTP — `GET /health` on `HTTP_PORT` (3000)

```rust
async fn health() -> impl IntoResponse {
    Json(serde_json::json!({
        "ok":      true,
        "service": "syrabit-rust-core-do",
        "uptime_s": started.elapsed().as_secs(),
        "git_sha": option_env!("GIT_SHA").unwrap_or("dev"),
    }))
}
```

Wired in the `axum::Router` boot. Same path the Dockerfile
HEALTHCHECK probes; same path DO App Platform's `health_check.http_path`
in `infra/dogcp/app-rust-core.yaml` probes.

### gRPC — `grpc.health.v1.Health/Check` on `GRPC_PORT` (50051)

```rust
let (mut health_reporter, health_service) = tonic_health::server::health_reporter();
health_reporter.set_serving::<MyServiceServer<MyService>>().await;
Server::builder()
    .layer(tracing_layer())
    .add_service(health_service)
    .add_service(MyServiceServer::new(svc))
    .serve(grpc_addr)
    .await?;
```

Smoke after deploy:

```sh
grpcurl -plaintext rust-core-app.ondigitalocean.app:50051 grpc.health.v1.Health/Check
# {"status": "SERVING"}
```

The Python backend's `gRPC` client is configured with the standard
health watch RPC so a rolling Rust core deploy fails over without
in-flight requests landing on a draining replica.

## Alert wiring

Rust core errors → App Insights `traces` table where `severityLevel >=
3`. The alert rule `ai_traces_error_rate_high` in
`infra/azure/observability.tf` (added in this task) keys on that
filter and pages via the `ops_alerts` action group → the existing
Slack `#ops-alerts` webhook.

DO App Platform crash loops → DO's native alarm → the same Slack
webhook via the DO-side notification channel. No bespoke wiring
needed — the platform pings the webhook directly.
