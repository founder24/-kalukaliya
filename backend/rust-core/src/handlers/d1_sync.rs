//! D1 Sync Handler - UUID to TEXT conversion layer
//! 
//! This handler provides endpoints for syncing PostgreSQL data (UUID-based)
//! to Cloudflare D1 (TEXT-based IDs). It handles the schema conversion
//! automatically to ensure seamless edge caching.

use axum::{
    extract::{State, Query},
    http::StatusCode,
    Json,
};
use serde::{Deserialize, Serialize};
use crate::AppState;

/// Query parameters for D1 sync endpoint
#[allow(dead_code)] // `table`/`since` parsed by serde, not yet used by stub handler
#[derive(Debug, Deserialize)]
pub struct D1SyncParams {
    #[serde(default = "default_limit")]
    pub limit: i64,
    pub table: Option<String>,
    pub since: Option<i64>, // Unix timestamp in milliseconds
}

fn default_limit() -> i64 {
    1000
}

/// D1-compatible record with TEXT IDs
#[derive(Debug, Serialize)]
pub struct D1Board {
    pub id: String,           // UUID converted to TEXT
    pub name: String,
    pub code: String,
    pub description: Option<String>,
    pub is_active: bool,
    pub created_at: i64,      // Unix timestamp ms
    pub updated_at: i64,
}

#[derive(Debug, Serialize)]
pub struct D1Class {
    pub id: String,
    pub name: String,
    pub code: String,
    pub board_id: String,     // Foreign key as TEXT
    pub sort_order: i32,
    pub is_active: bool,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Serialize)]
pub struct D1Subject {
    pub id: String,
    pub name: String,
    pub code: Option<String>,
    pub description: Option<String>,
    pub board_id: String,
    pub class_id: String,
    pub is_active: bool,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Serialize)]
pub struct D1Chapter {
    pub id: String,
    pub subject_id: String,
    pub title: String,
    pub chapter_number: Option<i32>,
    pub description: Option<String>,
    pub sort_order: i32,
    pub is_active: bool,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Serialize)]
pub struct D1Page {
    pub id: String,
    pub chapter_id: String,
    pub title: String,
    pub content: String,
    pub page_order: i32,
    pub is_active: bool,
    pub created_at: i64,
    pub updated_at: i64,
}

/// Response wrapper for D1 sync
#[derive(Debug, Serialize)]
pub struct D1SyncResponse<T> {
    pub success: bool,
    pub table: String,
    pub count: usize,
    pub has_more: bool,
    pub data: Vec<T>,
    pub synced_at: i64, // Unix timestamp ms
}

#[derive(Debug, Serialize)]
pub struct D1SyncStatus {
    pub success: bool,
    pub tables_synced: Vec<String>,
    pub total_records: usize,
    pub last_sync_at: Option<i64>,
    pub message: String,
}

/// GET /api/edge/d1-sync/boards
/// Fetch boards in D1-compatible format (TEXT IDs)
pub async fn sync_boards(
    State(state): State<AppState>,
    Query(params): Query<D1SyncParams>,
) -> Result<Json<D1SyncResponse<D1Board>>, StatusCode> {
    let query = sqlx::query_as::<_, (uuid::Uuid, String, String, Option<String>, bool, chrono::DateTime<chrono::Utc>, chrono::DateTime<chrono::Utc>)>(
        r#"
        SELECT id, name, code, description, is_active, created_at, updated_at
        FROM boards
        WHERE is_active = true
        ORDER BY created_at DESC
        LIMIT $1
        "#,
    )
    .bind(params.limit + 1); // Fetch one extra to check has_more

    let rows = query.fetch_all(&state.db).await.map_err(|e| {
        tracing::error!("Failed to fetch boards for D1 sync: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let has_more = rows.len() as i64 > params.limit;
    let rows: Vec<_> = rows.into_iter().take(params.limit as usize).collect();

    let data: Vec<D1Board> = rows.into_iter().map(|(id, name, code, desc, is_active, created_at, updated_at)| {
        D1Board {
            id: id.to_string(), // UUID → TEXT conversion
            name,
            code,
            description: desc,
            is_active,
            created_at: created_at.timestamp_millis(),
            updated_at: updated_at.timestamp_millis(),
        }
    }).collect();

    Ok(Json(D1SyncResponse {
        success: true,
        table: "boards".to_string(),
        count: data.len(),
        has_more,
        data,
        synced_at: chrono::Utc::now().timestamp_millis(),
    }))
}

/// GET /api/edge/d1-sync/classes
pub async fn sync_classes(
    State(state): State<AppState>,
    Query(params): Query<D1SyncParams>,
) -> Result<Json<D1SyncResponse<D1Class>>, StatusCode> {
    let query = sqlx::query_as::<_, (uuid::Uuid, String, String, uuid::Uuid, i32, bool, chrono::DateTime<chrono::Utc>, chrono::DateTime<chrono::Utc>)>(
        r#"
        SELECT id, name, code, board_id, sort_order, is_active, created_at, updated_at
        FROM classes
        WHERE is_active = true
        ORDER BY sort_order ASC
        LIMIT $1
        "#,
    )
    .bind(params.limit + 1);

    let rows = query.fetch_all(&state.db).await.map_err(|e| {
        tracing::error!("Failed to fetch classes for D1 sync: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let has_more = rows.len() as i64 > params.limit;
    let rows: Vec<_> = rows.into_iter().take(params.limit as usize).collect();

    let data: Vec<D1Class> = rows.into_iter().map(|(id, name, code, board_id, sort_order, is_active, created_at, updated_at)| {
        D1Class {
            id: id.to_string(),
            name,
            code,
            board_id: board_id.to_string(),
            sort_order,
            is_active,
            created_at: created_at.timestamp_millis(),
            updated_at: updated_at.timestamp_millis(),
        }
    }).collect();

    Ok(Json(D1SyncResponse {
        success: true,
        table: "classes".to_string(),
        count: data.len(),
        has_more,
        data,
        synced_at: chrono::Utc::now().timestamp_millis(),
    }))
}

/// GET /api/edge/d1-sync/subjects
pub async fn sync_subjects(
    State(state): State<AppState>,
    Query(params): Query<D1SyncParams>,
) -> Result<Json<D1SyncResponse<D1Subject>>, StatusCode> {
    let query = sqlx::query_as::<_, (uuid::Uuid, String, Option<String>, Option<String>, uuid::Uuid, uuid::Uuid, bool, chrono::DateTime<chrono::Utc>, chrono::DateTime<chrono::Utc>)>(
        r#"
        SELECT id, name, code, description, board_id, class_id, is_active, created_at, updated_at
        FROM subjects
        WHERE is_active = true
        ORDER BY name ASC
        LIMIT $1
        "#,
    )
    .bind(params.limit + 1);

    let rows = query.fetch_all(&state.db).await.map_err(|e| {
        tracing::error!("Failed to fetch subjects for D1 sync: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let has_more = rows.len() as i64 > params.limit;
    let rows: Vec<_> = rows.into_iter().take(params.limit as usize).collect();

    let data: Vec<D1Subject> = rows.into_iter().map(|(id, name, code, desc, board_id, class_id, is_active, created_at, updated_at)| {
        D1Subject {
            id: id.to_string(),
            name,
            code,
            description: desc,
            board_id: board_id.to_string(),
            class_id: class_id.to_string(),
            is_active,
            created_at: created_at.timestamp_millis(),
            updated_at: updated_at.timestamp_millis(),
        }
    }).collect();

    Ok(Json(D1SyncResponse {
        success: true,
        table: "subjects".to_string(),
        count: data.len(),
        has_more,
        data,
        synced_at: chrono::Utc::now().timestamp_millis(),
    }))
}

/// GET /api/edge/d1-sync/chapters
pub async fn sync_chapters(
    State(state): State<AppState>,
    Query(params): Query<D1SyncParams>,
) -> Result<Json<D1SyncResponse<D1Chapter>>, StatusCode> {
    let query = sqlx::query_as::<_, (uuid::Uuid, uuid::Uuid, String, Option<i32>, Option<String>, i32, bool, chrono::DateTime<chrono::Utc>, chrono::DateTime<chrono::Utc>)>(
        r#"
        SELECT id, subject_id, title, chapter_number, description, sort_order, is_active, created_at, updated_at
        FROM chapters
        WHERE is_active = true
        ORDER BY sort_order ASC
        LIMIT $1
        "#,
    )
    .bind(params.limit + 1);

    let rows = query.fetch_all(&state.db).await.map_err(|e| {
        tracing::error!("Failed to fetch chapters for D1 sync: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let has_more = rows.len() as i64 > params.limit;
    let rows: Vec<_> = rows.into_iter().take(params.limit as usize).collect();

    let data: Vec<D1Chapter> = rows.into_iter().map(|(id, subject_id, title, chapter_num, desc, sort_order, is_active, created_at, updated_at)| {
        D1Chapter {
            id: id.to_string(),
            subject_id: subject_id.to_string(),
            title,
            chapter_number: chapter_num,
            description: desc,
            sort_order,
            is_active,
            created_at: created_at.timestamp_millis(),
            updated_at: updated_at.timestamp_millis(),
        }
    }).collect();

    Ok(Json(D1SyncResponse {
        success: true,
        table: "chapters".to_string(),
        count: data.len(),
        has_more,
        data,
        synced_at: chrono::Utc::now().timestamp_millis(),
    }))
}

/// GET /api/edge/d1-sync/pages
pub async fn sync_pages(
    State(state): State<AppState>,
    Query(params): Query<D1SyncParams>,
) -> Result<Json<D1SyncResponse<D1Page>>, StatusCode> {
    let query = sqlx::query_as::<_, (uuid::Uuid, uuid::Uuid, String, String, i32, bool, chrono::DateTime<chrono::Utc>, chrono::DateTime<chrono::Utc>)>(
        r#"
        SELECT id, chapter_id, title, content, page_order, is_active, created_at, updated_at
        FROM pages
        WHERE is_active = true
        ORDER BY page_order ASC
        LIMIT $1
        "#,
    )
    .bind(params.limit + 1);

    let rows = query.fetch_all(&state.db).await.map_err(|e| {
        tracing::error!("Failed to fetch pages for D1 sync: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let has_more = rows.len() as i64 > params.limit;
    let rows: Vec<_> = rows.into_iter().take(params.limit as usize).collect();

    let data: Vec<D1Page> = rows.into_iter().map(|(id, chapter_id, title, content, page_order, is_active, created_at, updated_at)| {
        D1Page {
            id: id.to_string(),
            chapter_id: chapter_id.to_string(),
            title,
            content,
            page_order,
            is_active,
            created_at: created_at.timestamp_millis(),
            updated_at: updated_at.timestamp_millis(),
        }
    }).collect();

    Ok(Json(D1SyncResponse {
        success: true,
        table: "pages".to_string(),
        count: data.len(),
        has_more,
        data,
        synced_at: chrono::Utc::now().timestamp_millis(),
    }))
}

/// GET /api/edge/d1-status
/// Get sync status across all tables
pub async fn d1_sync_status(
    State(state): State<AppState>,
) -> Result<Json<D1SyncStatus>, StatusCode> {
    let mut total_records = 0;
    let mut tables_synced = Vec::new();

    // Count records in each table
    let tables = ["boards", "classes", "subjects", "chapters", "pages"];
    
    for table in &tables {
        let count: (i64,) = sqlx::query_as(&format!(
            "SELECT COUNT(*) FROM {}", table
        ))
        .fetch_one(&state.db)
        .await
        .map_err(|e| {
            tracing::error!("Failed to count {}: {}", table, e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

        if count.0 > 0 {
            tables_synced.push(table.to_string());
            total_records += count.0 as usize;
        }
    }

    let synced_count = tables_synced.len();
    Ok(Json(D1SyncStatus {
        success: true,
        tables_synced,
        total_records,
        last_sync_at: Some(chrono::Utc::now().timestamp_millis()),
        message: format!("{} tables ready for D1 sync with {} total records", synced_count, total_records),
    }))
}
