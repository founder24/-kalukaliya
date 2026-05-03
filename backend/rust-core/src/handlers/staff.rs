//! Staff management handlers with phone authentication and permission guards

use axum::{
    extract::{State, Path},
    http::StatusCode,
    Json,
};
use serde::{Deserialize, Serialize};
use crate::AppState;

#[derive(Debug, Deserialize)]
pub struct SendOtpRequest {
    pub phone: String,
}

#[derive(Debug, Deserialize)]
pub struct VerifyOtpRequest {
    pub phone: String,
    pub otp: String,
}

#[derive(Debug, Serialize)]
pub struct AuthResponse {
    pub success: bool,
    pub token: Option<String>,
    pub role: String,
    pub message: String,
}

// Request payload structs deserialized from JSON. Fields are read by
// serde during deserialization but not yet consumed by handler bodies
// (handlers are stubs pending DB writes — see follow-up #302), so we
// explicitly silence the dead-code lint.
#[allow(dead_code)]
#[derive(Debug, Deserialize)]
pub struct CreateSubjectRequest {
    pub name: String,
    pub board_id: String,
    pub class_id: String,
    pub description: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
pub struct UpdateSubjectRequest {
    pub name: Option<String>,
    pub description: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
pub struct CreatePageRequest {
    pub subject_id: String,
    pub title: String,
    pub content: String,
    pub page_order: Option<i32>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize)]
pub struct UpdatePageRequest {
    pub title: Option<String>,
    pub content: Option<String>,
    pub page_order: Option<i32>,
}

/// Send OTP to phone number for staff login
pub async fn send_otp(
    State(_state): State<AppState>,
    Json(payload): Json<SendOtpRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // Validate phone number format
    if !is_valid_phone(&payload.phone) {
        return Ok(Json(serde_json::json!({
            "success": false,
            "message": "Invalid phone number format"
        })));
    }

    // TODO: Generate and send OTP via SMS provider (Twilio/Firebase)
    // For now, generate a mock OTP
    let otp = generate_mock_otp();
    tracing::info!("Mock OTP for {}: {}", payload.phone, otp);

    // Store OTP in cache/database with expiration (5 minutes)
    // TODO: Implement actual OTP storage

    Ok(Json(serde_json::json!({
        "success": true,
        "message": "OTP sent successfully",
        "debug_otp": otp // Remove in production
    })))
}

/// Verify OTP and return JWT token with staff role
pub async fn verify_otp(
    State(state): State<AppState>,
    Json(payload): Json<VerifyOtpRequest>,
) -> Result<Json<AuthResponse>, StatusCode> {
    // TODO: Verify OTP from database/cache
    let is_valid = verify_mock_otp(&payload.otp);

    if !is_valid {
        return Ok(Json(AuthResponse {
            success: false,
            token: None,
            role: "".to_string(),
            message: "Invalid OTP".to_string(),
        }));
    }

    // Generate JWT token with staff role
    let token = generate_staff_jwt(&payload.phone, &state.config.jwt_secret);

    Ok(Json(AuthResponse {
        success: true,
        token: Some(token),
        role: "staff".to_string(),
        message: "Authentication successful".to_string(),
    }))
}

/// Get content hub data for staff (read-only for boards/classes)
pub async fn get_content_hub(
    State(_state): State<AppState>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // TODO: Query database for content hierarchy
    Ok(Json(serde_json::json!({
        "boards": [
            {"id": "board-1", "name": "CBSE", "can_edit": false, "can_delete": false}
        ],
        "classes": [
            {"id": "class-10", "name": "Class 10", "can_edit": false, "can_delete": false}
        ],
        "subjects": [
            {"id": "subj-math", "name": "Mathematics", "can_edit": true, "can_delete": false}
        ],
        "permissions": {
            "can_edit_subjects": true,
            "can_delete_subjects": false,
            "can_edit_classes": false,
            "can_delete_classes": false,
            "can_edit_boards": false,
            "can_delete_boards": false,
            "can_create_pages": true,
            "can_edit_pages": true,
            "can_delete_pages": true
        }
    })))
}

/// Create a new subject (staff can create if board/class exists)
pub async fn create_subject(
    State(_state): State<AppState>,
    Json(_payload): Json<CreateSubjectRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // TODO: Validate board_id and class_id exist
    // TODO: Create subject in database
    
    Ok(Json(serde_json::json!({
        "success": true,
        "message": "Subject created successfully"
    })))
}

/// Update subject (staff can only edit name, not delete)
pub async fn update_subject(
    State(_state): State<AppState>,
    Path(subject_id): Path<String>,
    Json(_payload): Json<UpdateSubjectRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // TODO: Update subject in database
    
    Ok(Json(serde_json::json!({
        "success": true,
        "subject_id": subject_id,
        "message": "Subject updated successfully"
    })))
}

/// Create a new subject page (lesson/content)
pub async fn create_page(
    State(_state): State<AppState>,
    Json(_payload): Json<CreatePageRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // TODO: Create page in database
    
    Ok(Json(serde_json::json!({
        "success": true,
        "message": "Page created successfully"
    })))
}

/// Update subject page
pub async fn update_page(
    State(_state): State<AppState>,
    Path(page_id): Path<String>,
    Json(_payload): Json<UpdatePageRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // TODO: Update page in database
    
    Ok(Json(serde_json::json!({
        "success": true,
        "page_id": page_id,
        "message": "Page updated successfully"
    })))
}

/// Delete subject page (staff CAN delete pages)
pub async fn delete_page(
    State(_state): State<AppState>,
    Path(page_id): Path<String>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    // TODO: Delete page from database
    
    Ok(Json(serde_json::json!({
        "success": true,
        "page_id": page_id,
        "message": "Page deleted successfully"
    })))
}

// Helper functions

fn is_valid_phone(phone: &str) -> bool {
    // Basic validation: 10-15 digits, may start with +
    let cleaned = phone.replace(|c: char| !c.is_numeric(), "");
    cleaned.len() >= 10 && cleaned.len() <= 15
}

fn generate_mock_otp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis();
    format!("{:06}", (timestamp % 1000000) as u32)
}

fn verify_mock_otp(_otp: &str) -> bool {
    // In development, accept any 6-digit OTP
    // In production, verify against stored OTP
    true
}

fn generate_staff_jwt(phone: &str, secret: &str) -> String {
    // Simplified JWT generation - use jsonwebtoken crate in production
    format!("mock_jwt_token_for_{}_{}", phone, secret)
}
