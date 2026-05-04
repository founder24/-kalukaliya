//! Phase 5 — Observability rewire (Task #333).
//!
//! Rust core entrypoint for DO App Platform. Wires:
//!
//!   * OpenTelemetry tracing with two parallel exporters
//!     (Application Insights + Axiom, both via OTLP/HTTP). Either
//!     sink may be missing without breaking the other; the SDK
//!     stays idle when neither is configured.
//!   * Axum HTTP server on `HTTP_PORT` (default 3000) exposing
//!     `GET /health` for DO App Platform's HEALTHCHECK.
//!   * Tonic gRPC server on `GRPC_PORT` (default 50051) exposing
//!     the standard `grpc.health.v1.Health/Check` so the in-VPC
//!     Python caller's failover-aware health watch can pin a
//!     specific replica's state.
//!
//! The two servers share the same Tokio runtime and exit together
//! on SIGTERM (DO sends SIGTERM with a 30s grace period before
//! SIGKILL on rolling deploys).

use std::env;
use std::net::SocketAddr;
use std::time::{Duration, Instant};

use anyhow::Result;
use axum::{routing::get, Json, Router};
use opentelemetry::{trace::TracerProvider as _, KeyValue};
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::{trace as sdktrace, trace::Config as SdkTraceConfig, Resource};
use serde_json::json;
use tonic::transport::Server;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

/// Process boot time, used to report uptime in `/health`.
static BOOT: once_cell::sync::Lazy<Instant> = once_cell::sync::Lazy::new(Instant::now);

/// Wire OTel tracing via a single OTLP/HTTP exporter pointed at the
/// in-cluster Azure Monitor OpenTelemetry Collector (DO App Platform
/// runs the collector as a separate component in the same App spec —
/// see `infra/do/app-otel-collector.yaml`). The collector then fans
/// out to App Insights + Axiom in parallel.
///
/// Rationale: the Rust ecosystem does not have a native App Insights
/// exporter, and the App Insights "OTLP-direct" path is still in
/// preview as of `azure-monitor-opentelemetry-exporter@1.0.0b30` and
/// only works for the Python/Node SDKs. The collector pattern keeps
/// the binary's exporter wiring simple, mirrors what the Lambda
/// fleet does (see `infra/aws/lambda-otel.tf`), and means a sink
/// rotation (App Insights → Axiom or vice-versa) is a collector
/// config change, not a Rust rebuild.
///
/// Returns `Ok(())` on success or when no exporter is configured.
fn init_tracing() -> Result<()> {
    let resource = Resource::new(vec![
        KeyValue::new("service.name", "syrabit-rust-core-do"),
        KeyValue::new("service.namespace", "syrabit"),
        KeyValue::new("deployment.environment", env::var("DEPLOYMENT_ENV").unwrap_or_else(|_| "production".into())),
        KeyValue::new("cloud.provider", "digitalocean"),
        KeyValue::new("cloud.platform", "digitalocean_app_platform"),
        KeyValue::new("cloud.region", env::var("DO_REGION").unwrap_or_else(|_| "blr1".into())),
        KeyValue::new("service.version", env::var("GIT_SHA").unwrap_or_else(|_| "dev".into())),
    ]);

    // opentelemetry_sdk 0.24 exposes the Resource via Config rather
    // than a builder method on TracerProvider directly.
    let mut tp_builder = sdktrace::TracerProvider::builder()
        .with_config(SdkTraceConfig::default().with_resource(resource));
    let mut wired_any = false;

    // Single OTLP/HTTP exporter. Endpoint is the in-cluster Azure
    // Monitor OTel Collector (default: the canonical localhost:4318
    // when running alongside the collector in the same App spec).
    if let Ok(endpoint) = env::var("OTEL_EXPORTER_OTLP_ENDPOINT") {
        let exp = opentelemetry_otlp::new_exporter()
            .http()
            .with_endpoint(format!("{}/v1/traces", endpoint.trim_end_matches('/')))
            .build_span_exporter()?;
        tp_builder = tp_builder.with_batch_exporter(exp, opentelemetry_sdk::runtime::Tokio);
        wired_any = true;
        eprintln!("[rust-core::otel] OTLP exporter wired endpoint={endpoint}");
    }

    let provider = tp_builder.build();
    let tracer = provider.tracer("rust-core");
    opentelemetry::global::set_tracer_provider(provider);

    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .with(tracing_subscriber::fmt::layer().json())
        .with(tracing_opentelemetry::layer().with_tracer(tracer))
        .init();

    if !wired_any {
        tracing::warn!(
            "OTEL_EXPORTER_OTLP_ENDPOINT not set — spans will be dropped. \
             Point this at the in-cluster collector (default http://localhost:4318)."
        );
    }
    Ok(())
}

/// Liveness probe for DO App Platform's HEALTHCHECK + the Dockerfile
/// `wget --spider` health check.
async fn http_health() -> Json<serde_json::Value> {
    Json(json!({
        "ok":      true,
        "service": "syrabit-rust-core-do",
        "uptime_s": BOOT.elapsed().as_secs(),
        "git_sha": option_env!("GIT_SHA").unwrap_or("dev"),
    }))
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = *BOOT;

    init_tracing()?;

    let http_port: u16 = env::var("HTTP_PORT").ok().and_then(|s| s.parse().ok()).unwrap_or(3000);
    let grpc_port: u16 = env::var("GRPC_PORT").ok().and_then(|s| s.parse().ok()).unwrap_or(50051);

    // ── HTTP server ──────────────────────────────────────────────
    // `/health` is the canonical path; `/healthz` is an alias kept
    // for compatibility with the existing
    // `.github/workflows/do-deploy-rust-core.yml` post-deploy
    // verification step that curls `/healthz`. Both return the
    // same payload.
    let app = Router::new()
        .route("/health", get(http_health))
        .route("/healthz", get(http_health));
    let http_addr: SocketAddr = ([0, 0, 0, 0], http_port).into();
    let http_listener = tokio::net::TcpListener::bind(http_addr).await?;
    tracing::info!(addr = %http_addr, "HTTP server listening");
    // Graceful-shutdown future: fires on either SIGTERM (DO App
    // Platform's rolling-deploy signal) or SIGINT (Ctrl-C in dev).
    // Both servers below dial into the same broadcast so they exit
    // together and OTel gets a chance to flush.
    let (shutdown_tx, _) = tokio::sync::broadcast::channel::<()>(1);
    let mut http_shutdown_rx = shutdown_tx.subscribe();
    let mut grpc_shutdown_rx = shutdown_tx.subscribe();

    let http_handle = tokio::spawn(async move {
        let res = axum::serve(http_listener, app)
            .with_graceful_shutdown(async move {
                let _ = http_shutdown_rx.recv().await;
            })
            .await;
        if let Err(e) = res {
            tracing::error!(error = %e, "HTTP server crashed");
        }
    });

    // ── gRPC server (health-only scaffold) ───────────────────────
    // `tonic_health` ships the canonical
    // `grpc.health.v1.Health/Check` implementation. The in-VPC
    // Python caller pins a replica's status via this RPC during
    // rolling deploys.
    let (mut health_reporter, health_service) = tonic_health::server::health_reporter();
    health_reporter
        .set_service_status("", tonic_health::ServingStatus::Serving)
        .await;

    let grpc_addr: SocketAddr = ([0, 0, 0, 0], grpc_port).into();
    tracing::info!(addr = %grpc_addr, "gRPC server listening");
    let grpc_handle = tokio::spawn(async move {
        if let Err(e) = Server::builder()
            .timeout(Duration::from_secs(30))
            .add_service(health_service)
            .serve_with_shutdown(grpc_addr, async move {
                let _ = grpc_shutdown_rx.recv().await;
            })
            .await
        {
            tracing::error!(error = %e, "gRPC server crashed");
        }
    });

    // Wait for SIGTERM (DO/k8s rolling-deploy signal) OR SIGINT
    // (Ctrl-C in dev). Whichever lands first triggers the broadcast
    // shutdown so HTTP + gRPC both stop cleanly, then OTel flushes.
    use tokio::signal::unix::{signal, SignalKind};
    let mut sigterm = signal(SignalKind::terminate())?;
    tokio::select! {
        _ = tokio::signal::ctrl_c()  => tracing::info!("SIGINT received"),
        _ = sigterm.recv()           => tracing::info!("SIGTERM received"),
    }
    let _ = shutdown_tx.send(());
    tracing::info!("shutdown signal received; flushing OTel and stopping");
    opentelemetry::global::shutdown_tracer_provider();
    let _ = http_handle.await;
    let _ = grpc_handle.await;
    Ok(())
}
