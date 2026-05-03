//! Syrabit.ai Neural Mesh Core
//! 
//! High-performance Rust/gRPC backend with:
//! - Axum HTTP server for REST API
//! - Tonic gRPC server for Edge communication
//! - GraphRAG with hybrid search and 5-hop traversal
//! - WebSocket support for real-time JARVIS HUD metrics

mod generated;
mod handlers;
mod services;
mod models;
mod db;
mod grpc;

// WebSocket handler lives in handlers/websocket.rs; alias for ergonomics.
use crate::handlers::websocket;

use axum::{
    routing::{get, post, put, delete},
    Router,
};
use crate::generated::syrabit::neural_mesh_service_server::NeuralMeshServiceServer;
use tower_http::{
    cors::{Any, CorsLayer},
    trace::TraceLayer,
};
use tokio::sync::broadcast;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};
use std::net::SocketAddr;

use crate::grpc::NeuralMeshGrpcService;
use crate::handlers::websocket::MetricsBroadcaster;

/// Application state shared across handlers
#[derive(Clone)]
pub struct AppState {
    /// Database connection pool (SQLx)
    pub db: sqlx::PgPool,
    /// Broadcast channel for WebSocket metrics streaming
    pub metrics_tx: broadcast::Sender<generated::syrabit::MetricsUpdate>,
    /// Configuration
    pub config: AppConfig,
}

/// Application configuration
#[derive(Clone)]
pub struct AppConfig {
    pub http_port: u16,
    pub grpc_port: u16,
    pub database_url: String,
    pub jwt_secret: String,
    pub environment: String,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            http_port: std::env::var("HTTP_PORT")
                .unwrap_or_else(|_| "3000".to_string())
                .parse()
                .unwrap_or(3000),
            grpc_port: std::env::var("GRPC_PORT")
                .unwrap_or_else(|_| "50051".to_string())
                .parse()
                .unwrap_or(50051),
            database_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/syrabit".to_string()),
            jwt_secret: std::env::var("JWT_SECRET")
                .unwrap_or_else(|_| "dev-secret-key".to_string()),
            environment: std::env::var("ENVIRONMENT")
                .unwrap_or_else(|_| "development".to_string()),
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize logging
    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer())
        .with(tracing_subscriber::EnvFilter::new(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "info".into()),
        ))
        .init();

    tracing::info!("🚀 Starting Syrabit Neural Mesh Core...");

    // Load configuration
    let config = AppConfig::default();
    
    // Initialize database connection pool
    let db_pool = sqlx::PgPool::connect(&config.database_url).await?;
    tracing::info!("✅ Database connection established");

    // Run database migrations
    sqlx::migrate!("./migrations").run(&db_pool).await?;
    tracing::info!("✅ Database migrations completed");

    // Create metrics broadcast channel for WebSocket streaming
    let (metrics_tx, _metrics_rx) = broadcast::channel::<generated::syrabit::MetricsUpdate>(100);
    let metrics_broadcaster = MetricsBroadcaster::new(metrics_tx.clone());

    // Clone for gRPC server
    let grpc_db = db_pool.clone();
    let grpc_metrics_tx = metrics_tx.clone();

    // Create application state
    let state = AppState {
        db: db_pool,
        metrics_tx,
        config: config.clone(),
    };

    // Configure CORS
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any)
        .expose_headers([axum::http::header::CONTENT_TYPE]);

    // Build HTTP router with Axum
    let app = Router::new()
        // Health check endpoint
        .route("/health", get(handlers::health::health_check))
        // D1 Sync endpoints for Edge caching (UUID → TEXT conversion)
        .route("/api/edge/d1-sync/boards", get(handlers::d1_sync::sync_boards))
        .route("/api/edge/d1-sync/classes", get(handlers::d1_sync::sync_classes))
        .route("/api/edge/d1-sync/subjects", get(handlers::d1_sync::sync_subjects))
        .route("/api/edge/d1-sync/chapters", get(handlers::d1_sync::sync_chapters))
        .route("/api/edge/d1-sync/pages", get(handlers::d1_sync::sync_pages))
        .route("/api/edge/d1-status", get(handlers::d1_sync::d1_sync_status))
        // RAG endpoints (migrated from Python)
        .route("/api/rag/query", post(handlers::rag::query_rag))
        .route("/api/rag/search", post(handlers::rag::hybrid_search))
        // Agent endpoints
        .route("/api/agents", get(handlers::agents::list_agents))
        // WebSocket endpoint for JARVIS HUD
        .route("/ws/metrics", get(websocket::metrics_handler))
        // Staff management endpoints (Phone auth + CMS)
        .route("/api/staff/login", post(handlers::staff::send_otp))
        .route("/api/staff/verify", post(handlers::staff::verify_otp))
        .route("/api/staff/content-hub", get(handlers::staff::get_content_hub))
        .route("/api/staff/subjects", post(handlers::staff::create_subject))
        // Axum 0.8: path params changed from `:id` to `{id}` (matchit 0.8).
        .route("/api/staff/subjects/{id}", put(handlers::staff::update_subject))
        .route("/api/staff/subject-pages", post(handlers::staff::create_page))
        .route("/api/staff/subject-pages/{id}", put(handlers::staff::update_page))
        .route("/api/staff/subject-pages/{id}", delete(handlers::staff::delete_page))
        // Agent execute path also affected by the 0.7 → 0.8 router syntax change.
        .route("/api/agents/{id}/execute", post(handlers::agents::execute_agent))
        // Layer middleware
        .layer(cors)
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    // Spawn gRPC server in background with tonic-web support for Cloudflare Workers
    let grpc_addr = SocketAddr::from(([0, 0, 0, 0], config.grpc_port));
    tracing::info!("📡 gRPC server listening on {} (HTTP/2 + gRPC-Web)", grpc_addr);

    let grpc_service = NeuralMeshGrpcService::new(grpc_db, grpc_metrics_tx);

    // Enable gRPC-Web support for Cloudflare Workers compatibility.
    // tonic 0.12: services are constructed via the generated `*Server::new`
    // wrapper; the previous `into_service` shim no longer exists.
    // gRPC server reflection (Task #192 — required for grpcurl/probe
    // tooling and for the Cloudflare gRPC-Web edge proxy to discover
    // service definitions at runtime). The descriptor set is generated
    // by build.rs and embedded at compile time.
    const FILE_DESCRIPTOR_SET: &[u8] =
        include_bytes!(concat!(env!("OUT_DIR"), "/syrabit_descriptor.bin"));

    let grpc_handle = tokio::spawn(async move {
        let server = NeuralMeshServiceServer::new(grpc_service);
        let reflection = tonic_reflection::server::Builder::configure()
            .register_encoded_file_descriptor_set(FILE_DESCRIPTOR_SET)
            .build_v1()
            .expect("Failed to build gRPC reflection service");
        tonic::transport::Server::builder()
            .accept_http1(true)  // Required for gRPC-Web
            .add_service(tonic_web::enable(server))
            .add_service(reflection)
            .serve(grpc_addr)
            .await
            .expect("Failed to start gRPC server");
    });

    // Start metrics broadcaster task
    let metrics_handle = tokio::spawn(async move {
        metrics_broadcaster.run().await;
    });

    // Start HTTP server
    let http_addr = SocketAddr::from(([0, 0, 0, 0], config.http_port));
    tracing::info!("🌐 HTTP server listening on {}", http_addr);

    let listener = tokio::net::TcpListener::bind(http_addr).await?;
    
    // Run all servers concurrently
    tokio::select! {
        result = axum::serve(listener, app) => {
            tracing::error!("HTTP server error: {:?}", result);
        }
        result = grpc_handle => {
            tracing::error!("gRPC server error: {:?}", result);
        }
        result = metrics_handle => {
            tracing::error!("Metrics broadcaster error: {:?}", result);
        }
    }

    Ok(())
}
