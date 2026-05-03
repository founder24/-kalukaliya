//! RAG (Retrieval-Augmented Generation) endpoint handlers
//! Implements GraphRAG with hybrid search and 5-hop traversal

use axum::{
    extract::State,
    http::StatusCode,
    Json,
};
use serde::{Deserialize, Serialize};
use crate::AppState;
use crate::generated::syrabit::RagQuery;

#[derive(Debug, Deserialize)]
pub struct RagQueryRequest {
    pub query: String,
    #[serde(default = "default_top_k")]
    pub top_k: i32,
    pub filters: Option<Vec<String>>,
    #[serde(default)]
    pub hybrid_search: bool,
    #[serde(default = "default_hop_count")]
    pub hop_count: i32,
}

fn default_top_k() -> i32 { 10 }
fn default_hop_count() -> i32 { 5 }

#[derive(Debug, Serialize)]
pub struct RagQueryResponse {
    pub results: Vec<RagResultItem>,
    pub latency_ms: i64,
    pub search_type: String,
    pub total_traversed: i32,
}

#[derive(Debug, Serialize)]
pub struct RagResultItem {
    pub document_id: String,
    pub content: String,
    pub score: f32,
    pub metadata: std::collections::HashMap<String, String>,
    pub related_ids: Vec<String>,
}

/// Query RAG with GraphRAG support
pub async fn query_rag(
    State(state): State<AppState>,
    Json(payload): Json<RagQueryRequest>,
) -> Result<Json<RagQueryResponse>, StatusCode> {
    let start_time = std::time::Instant::now();

    // Build the gRPC RAG query
    let rag_query = RagQuery {
        query: payload.query.clone(),
        top_k: payload.top_k,
        score_threshold: None,
        filters: payload.filters.clone().unwrap_or_default(),
        hybrid_search: payload.hybrid_search,
        hop_count: payload.hop_count,
    };

    // Execute GraphRAG search via services
    let results = crate::services::graph_rag::execute_graph_rag(
        &state.db,
        rag_query,
    ).await
    .map_err(|e| {
        tracing::error!("GraphRAG query failed: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let latency_ms = start_time.elapsed().as_millis() as i64;
    let search_type = if payload.hybrid_search { "hybrid" } else { "vector" }.to_string();
    let total_traversed = results.iter().map(|r| r.related_ids.len() as i32).sum();

    let response = RagQueryResponse {
        results: results.into_iter().map(|r| RagResultItem {
            document_id: r.document_id,
            content: r.content,
            score: r.score,
            metadata: r.metadata,
            related_ids: r.related_ids,
        }).collect(),
        latency_ms,
        search_type,
        total_traversed,
    };

    Ok(Json(response))
}

/// Hybrid search combining vector and keyword search
pub async fn hybrid_search(
    State(state): State<AppState>,
    Json(payload): Json<RagQueryRequest>,
) -> Result<Json<RagQueryResponse>, StatusCode> {
    // Force hybrid search mode
    let mut hybrid_payload = payload;
    hybrid_payload.hybrid_search = true;

    query_rag(State(state), Json(hybrid_payload)).await
}
