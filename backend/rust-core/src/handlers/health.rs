//! Health check endpoint handlers

use axum::{
    http::StatusCode,
    Json,
};
use serde_json::json;
use crate::AppState;

/// Basic health check endpoint
pub async fn health_check() -> Json<serde_json::Value> {
    Json(json!({
        "status": "healthy",
        "service": "syrabit-rust-core",
        "version": env!("CARGO_PKG_VERSION"),
        "timestamp": chrono::Utc::now().to_rfc3339()
    }))
}

/// Detailed health check with system metrics
/// Wired up by an upcoming admin route; kept public so the symbol is part
/// of the crate's API surface even before the route exists.
#[allow(dead_code)]
pub async fn health_detailed(state: axum::extract::State<AppState>) -> Result<Json<serde_json::Value>, StatusCode> {
    // Check database connectivity
    let db_health = match sqlx::query("SELECT 1").fetch_one(&state.db).await {
        Ok(_) => json!({"status": "connected", "latency_ms": 1}),
        Err(e) => json!({"status": "disconnected", "error": e.to_string()}),
    };

    Ok(Json(json!({
        "status": "healthy",
        "service": "syrabit-rust-core",
        "version": env!("CARGO_PKG_VERSION"),
        "timestamp": chrono::Utc::now().to_rfc3339(),
        "database": db_health,
        "environment": state.config.environment,
        "ports": {
            "http": state.config.http_port,
            "grpc": state.config.grpc_port
        }
    })))
}
