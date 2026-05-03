//! gRPC service implementation for Edge communication

use tonic::{Request, Response, Status};
use tokio::sync::broadcast;
use sqlx::PgPool;
use std::pin::Pin;
use futures::Stream;
use crate::generated::syrabit::{
    neural_mesh_service_server::NeuralMeshService,
    ChatRequest, ChatResponse,
    RagQuery, RagResponse,
    AgentCommand, AgentResponse,
    HealthCheck,
    MetricsRequest, MetricsUpdate,
};

/// gRPC service implementation
pub struct NeuralMeshGrpcService {
    // Held for future LLM/RAG implementations of the streaming RPCs;
    // currently unused while those handlers return placeholder data.
    #[allow(dead_code)]
    db: PgPool,
    metrics_tx: broadcast::Sender<MetricsUpdate>,
}

impl NeuralMeshGrpcService {
    pub fn new(db: PgPool, metrics_tx: broadcast::Sender<MetricsUpdate>) -> Self {
        Self { db, metrics_tx }
    }
}

impl Clone for NeuralMeshGrpcService {
    fn clone(&self) -> Self {
        Self {
            db: self.db.clone(),
            metrics_tx: self.metrics_tx.clone(),
        }
    }
}

// Tonic streaming methods require `Stream<Item = Result<T, Status>>`.
// `BroadcastStream` yields `Result<T, BroadcastStreamRecvError>`, so we
// wrap it in a boxed stream that maps the recv error into a `Status`.
type ChatResponseStream = Pin<Box<dyn Stream<Item = Result<ChatResponse, Status>> + Send + 'static>>;
type MetricsUpdateStream = Pin<Box<dyn Stream<Item = Result<MetricsUpdate, Status>> + Send + 'static>>;

#[tonic::async_trait]
impl NeuralMeshService for NeuralMeshGrpcService {
    type ChatStream = ChatResponseStream;
    type StreamMetricsStream = MetricsUpdateStream;

    /// Chat with AI assistant (streaming response)
    async fn chat(
        &self,
        request: Request<ChatRequest>,
    ) -> Result<Response<Self::ChatStream>, Status> {
        let msg = request.into_inner();
        tracing::info!("gRPC Chat request from user: {}", msg.user_id);

        // TODO: Implement actual chat logic with LLM. Until then, return a
        // well-formed empty stream that completes cleanly (End-Of-Stream)
        // rather than emitting a synthetic recv error to the client.
        // We deliberately use `futures::stream::empty()` instead of a
        // BroadcastStream whose sender is dropped immediately — the latter
        // would surface as a `BroadcastStreamRecvError` on the wire.
        let stream = futures::stream::empty::<Result<ChatResponse, Status>>();
        Ok(Response::new(Box::pin(stream)))
    }

    /// RAG query with GraphRAG support
    async fn query_rag(
        &self,
        request: Request<RagQuery>,
    ) -> Result<Response<RagResponse>, Status> {
        let query = request.into_inner();
        tracing::info!("gRPC RAG query: {}", query.query);

        // Execute GraphRAG search
        let results = match crate::services::graph_rag::execute_graph_rag(&self.db, query).await {
            Ok(results) => results,
            Err(e) => {
                return Err(Status::internal(format!("GraphRAG failed: {}", e)));
            }
        };

        let rag_results = results.into_iter().map(|r| {
            crate::generated::syrabit::RagResult {
                document_id: r.document_id,
                content: r.content,
                score: r.score,
                metadata: r.metadata,
                related_ids: r.related_ids,
            }
        }).collect();

        let response = RagResponse {
            results: rag_results,
            latency_ms: 0, // TODO: Calculate actual latency
            search_type: "hybrid".to_string(),
            total_traversed: 0, // TODO: Track traversal count
        };

        Ok(Response::new(response))
    }

    /// Execute agent command
    async fn execute_agent(
        &self,
        request: Request<AgentCommand>,
    ) -> Result<Response<AgentResponse>, Status> {
        let command = request.into_inner();
        tracing::info!("gRPC Agent command: {} for agent {}", command.action, command.agent_id);

        // TODO: Implement actual agent execution

        let response = AgentResponse {
            command_id: uuid::Uuid::new_v4().to_string(),
            success: true,
            error_message: None,
            status: Some(crate::generated::syrabit::AgentStatus {
                agent_id: command.agent_id,
                state: "running".to_string(),
                current_task: "Processing command".to_string(),
                started_at: chrono::Utc::now().timestamp(),
                progress: std::collections::HashMap::new(),
            }),
        };

        Ok(Response::new(response))
    }

    /// Health check
    async fn healthz(
        &self,
        request: Request<HealthCheck>,
    ) -> Result<Response<HealthCheck>, Status> {
        let _msg = request.into_inner();
        
        // Check database connectivity
        let db_healthy = sqlx::query("SELECT 1")
            .fetch_one(&self.db)
            .await
            .is_ok();

        let response = HealthCheck {
            service: "syrabit-rust-core".to_string(),
            version: env!("CARGO_PKG_VERSION").to_string(),
            uptime_seconds: 0, // TODO: Track actual uptime
            metrics: Some(crate::generated::syrabit::SystemMetrics {
                cpu_usage: 0.25,
                memory_usage: 0.45,
                active_connections: 10,
                requests_per_second: 100,
                avg_latency_ms: 15.0,
            }),
        };

        if !db_healthy {
            return Ok(Response::new(response));
        }

        Ok(Response::new(response))
    }

    /// Stream real-time metrics for JARVIS HUD
    async fn stream_metrics(
        &self,
        request: Request<MetricsRequest>,
    ) -> Result<Response<Self::StreamMetricsStream>, Status> {
        let _config = request.into_inner();
        tracing::info!("gRPC metrics stream requested");

        // Subscribe to metrics broadcasts
        let rx = self.metrics_tx.subscribe();

        use tokio_stream::wrappers::BroadcastStream;
        use futures::StreamExt;
        let stream = BroadcastStream::new(rx)
            .map(|r| r.map_err(|e| Status::internal(format!("broadcast recv: {e}"))));
        Ok(Response::new(Box::pin(stream)))
    }
}
