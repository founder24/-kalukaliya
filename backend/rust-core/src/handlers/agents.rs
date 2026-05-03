//! Agent management endpoint handlers

use axum::{
    extract::{State, Path},
    http::StatusCode,
    Json,
};
use serde::{Deserialize, Serialize};
use crate::AppState;

#[derive(Debug, Serialize)]
pub struct AgentInfo {
    pub agent_id: String,
    pub name: String,
    pub state: String,
    pub current_task: Option<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

#[allow(dead_code)] // `parameters` is parsed by serde but not yet consumed (handler stub)
#[derive(Debug, Deserialize)]
pub struct ExecuteAgentRequest {
    pub action: String,
    pub parameters: Option<std::collections::HashMap<String, String>>,
}

/// List all active agents
pub async fn list_agents(
    State(_state): State<AppState>,
) -> Result<Json<Vec<AgentInfo>>, StatusCode> {
    // TODO: Query database for active agents
    let agents = vec![
        AgentInfo {
            agent_id: "agent-001".to_string(),
            name: "Content Curator".to_string(),
            state: "idle".to_string(),
            current_task: None,
            created_at: chrono::Utc::now(),
        },
        AgentInfo {
            agent_id: "agent-002".to_string(),
            name: "SEO Optimizer".to_string(),
            state: "running".to_string(),
            current_task: Some("Analyzing keywords".to_string()),
            created_at: chrono::Utc::now(),
        },
    ];

    Ok(Json(agents))
}

/// Execute a specific agent command
pub async fn execute_agent(
    State(_state): State<AppState>,
    Path(agent_id): Path<String>,
    Json(payload): Json<ExecuteAgentRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    tracing::info!("Executing agent {} with action {}", agent_id, payload.action);

    // TODO: Implement actual agent execution logic
    match payload.action.as_str() {
        "execute" => {
            Ok(Json(serde_json::json!({
                "success": true,
                "agent_id": agent_id,
                "message": "Agent execution started"
            })))
        }
        "pause" => {
            Ok(Json(serde_json::json!({
                "success": true,
                "agent_id": agent_id,
                "message": "Agent paused"
            })))
        }
        "resume" => {
            Ok(Json(serde_json::json!({
                "success": true,
                "agent_id": agent_id,
                "message": "Agent resumed"
            })))
        }
        "terminate" => {
            Ok(Json(serde_json::json!({
                "success": true,
                "agent_id": agent_id,
                "message": "Agent terminated"
            })))
        }
        _ => Err(StatusCode::BAD_REQUEST),
    }
}
