//! Database models

use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use chrono::{DateTime, Utc};

/// Educational Board (e.g., CBSE, ICSE, State Boards)
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct Board {
    pub id: String,
    pub name: String,
    pub code: String,
    pub description: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Class/Grade level
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct Class {
    pub id: String,
    pub name: String,
    pub board_id: String,
    pub grade_level: i32,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Subject within a class and board
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct Subject {
    pub id: String,
    pub name: String,
    pub board_id: String,
    pub class_id: String,
    pub description: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Subject Page (lesson/content)
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct SubjectPage {
    pub id: String,
    pub subject_id: String,
    pub title: String,
    pub content: String,
    pub page_order: i32,
    pub metadata: Option<serde_json::Value>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Staff user with phone authentication
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct StaffUser {
    pub id: String,
    pub phone: String,
    pub name: Option<String>,
    pub role: String, // "staff", "admin"
    pub is_active: bool,
    pub last_login: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
}

/// OTP record for phone authentication
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct OtpRecord {
    pub id: String,
    pub phone: String,
    pub otp_hash: String,
    pub expires_at: DateTime<Utc>,
    pub is_used: bool,
    pub created_at: DateTime<Utc>,
}

/// Document for RAG search
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct Document {
    pub id: String,
    pub title: String,
    pub content: String,
    pub embedding: Option<Vec<f32>>, // Vector embedding
    pub metadata: Option<serde_json::Value>,
    pub document_type: String,
    pub source_id: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// Graph edge for knowledge graph traversal
#[derive(Debug, Clone, FromRow, Serialize, Deserialize)]
pub struct GraphEdge {
    pub id: String,
    pub source_id: String,
    pub target_id: String,
    pub edge_type: String,
    pub weight: f32,
    pub metadata: Option<serde_json::Value>,
    pub created_at: DateTime<Utc>,
}
