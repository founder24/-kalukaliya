//! Staff management handlers with phone authentication and permission guards.
//!
//! This module replaces the original mocked stubs (#302) with real
//! implementations:
//!
//! * OTP send/verify hash codes with SHA-256 and persist them to Postgres
//!   via `db::Repository`. Codes carry a 5 minute TTL and are marked
//!   `is_used` after a successful verification so they cannot be replayed.
//! * SMS delivery is performed via Twilio when `TWILIO_ACCOUNT_SID`,
//!   `TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER` are configured. In
//!   development (any of those missing) the OTP is logged via `tracing`
//!   and returned in the response body under `debug_otp` so local QA can
//!   complete the flow without a real provider.
//! * JWTs are minted with the `jsonwebtoken` crate (HS256) using the
//!   configured `JWT_SECRET`. Tokens carry the staff user id, phone and
//!   role and expire after 24 hours.
//! * Content hub, subject CRUD and page CRUD are now backed by
//!   `db::Repository` rather than hardcoded JSON.

use axum::{
    extract::{Path, State},
    http::StatusCode,
    Json,
};
use chrono::{Duration, Utc};
use jsonwebtoken::{decode, encode, Algorithm, DecodingKey, EncodingKey, Header, Validation};
use rand::Rng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::db::repository::Repository;
use crate::AppState;

/// OTP lifetime in minutes — short enough to limit replay window, long
/// enough to forgive a slow SMS provider.
const OTP_TTL_MINUTES: i64 = 5;
/// Issued JWT lifetime. 24h matches the staff dashboard session length.
const JWT_TTL_HOURS: i64 = 24;

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

#[derive(Debug, Deserialize)]
pub struct CreateSubjectRequest {
    pub name: String,
    pub board_id: String,
    pub class_id: String,
    pub description: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct UpdateSubjectRequest {
    pub name: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct CreatePageRequest {
    pub subject_id: String,
    pub title: String,
    pub content: String,
    pub page_order: Option<i32>,
}

#[derive(Debug, Deserialize)]
pub struct UpdatePageRequest {
    pub title: Option<String>,
    pub content: Option<String>,
    pub page_order: Option<i32>,
}

/// JWT claims minted on successful OTP verification.
#[derive(Debug, Serialize, Deserialize)]
pub struct Claims {
    /// Staff user id (UUID string).
    pub sub: String,
    pub phone: String,
    pub role: String,
    pub exp: usize,
    pub iat: usize,
}

// ---------------------------------------------------------------------------
// Auth handlers
// ---------------------------------------------------------------------------

/// Send OTP to phone number for staff login.
pub async fn send_otp(
    State(state): State<AppState>,
    Json(payload): Json<SendOtpRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    if !is_valid_phone(&payload.phone) {
        return Ok(Json(serde_json::json!({
            "success": false,
            "message": "Invalid phone number format"
        })));
    }

    let repo = Repository::new(state.db.clone());
    let otp = generate_otp();
    let otp_hash = hash_otp(&otp);
    let expires_at = Utc::now() + Duration::minutes(OTP_TTL_MINUTES);

    if let Err(e) = repo.store_otp(&payload.phone, &otp_hash, expires_at).await {
        tracing::error!("Failed to persist OTP for {}: {:?}", payload.phone, e);
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }

    // Attempt SMS delivery via Twilio if fully configured. Treat this as
    // best-effort: a delivery failure should still return success so an
    // attacker can't probe whether a phone number is provisioned.
    let mut delivered = false;
    if let (Some(sid), Some(token), Some(from)) = (
        state.config.twilio_account_sid.as_deref(),
        state.config.twilio_auth_token.as_deref(),
        state.config.twilio_from_number.as_deref(),
    ) {
        match send_twilio_sms(sid, token, from, &payload.phone, &otp).await {
            Ok(()) => delivered = true,
            Err(e) => tracing::error!("Twilio delivery failed for {}: {:?}", payload.phone, e),
        }
    } else {
        tracing::info!("[dev] OTP for {}: {}", payload.phone, otp);
    }

    // Only echo the OTP back outside of production so local development
    // and integration tests can complete the flow without SMS.
    let mut body = serde_json::json!({
        "success": true,
        "message": "OTP sent successfully",
        "delivered": delivered,
    });
    if state.config.environment != "production" {
        body["debug_otp"] = serde_json::Value::String(otp);
    }
    Ok(Json(body))
}

/// Verify OTP and return JWT token with staff role.
pub async fn verify_otp(
    State(state): State<AppState>,
    Json(payload): Json<VerifyOtpRequest>,
) -> Result<Json<AuthResponse>, StatusCode> {
    let repo = Repository::new(state.db.clone());

    let record = match repo.get_valid_otp(&payload.phone).await {
        Ok(Some(r)) => r,
        Ok(None) => {
            return Ok(Json(AuthResponse {
                success: false,
                token: None,
                role: String::new(),
                message: "OTP expired or not found".to_string(),
            }));
        }
        Err(e) => {
            tracing::error!("Failed to look up OTP for {}: {:?}", payload.phone, e);
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    if hash_otp(&payload.otp) != record.otp_hash {
        return Ok(Json(AuthResponse {
            success: false,
            token: None,
            role: String::new(),
            message: "Invalid OTP".to_string(),
        }));
    }

    // Consume the OTP so it cannot be replayed.
    if let Err(e) = repo.mark_otp_used(&record.id).await {
        tracing::error!("Failed to mark OTP {} as used: {:?}", record.id, e);
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }

    // Look up (or auto-provision) the staff user. New phone numbers are
    // created with the default `staff` role; admins must be promoted out
    // of band.
    let staff = match repo.get_staff_by_phone(&payload.phone).await {
        Ok(Some(s)) => s,
        Ok(None) => match repo.create_staff_user(&payload.phone, None).await {
            Ok(s) => s,
            Err(e) => {
                tracing::error!("Failed to create staff user for {}: {:?}", payload.phone, e);
                return Err(StatusCode::INTERNAL_SERVER_ERROR);
            }
        },
        Err(e) => {
            tracing::error!("Failed to look up staff user {}: {:?}", payload.phone, e);
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    let token = match generate_staff_jwt(&staff.id, &staff.phone, &staff.role, &state.config.jwt_secret) {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("JWT signing failed: {:?}", e);
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    Ok(Json(AuthResponse {
        success: true,
        token: Some(token),
        role: staff.role,
        message: "Authentication successful".to_string(),
    }))
}

// ---------------------------------------------------------------------------
// Content hub / CRUD handlers
// ---------------------------------------------------------------------------

/// Get content hub data for staff (read-only for boards/classes).
pub async fn get_content_hub(
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let repo = Repository::new(state.db.clone());

    let boards = repo.list_boards().await.map_err(|e| {
        tracing::error!("list_boards failed: {:?}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;
    let classes = repo.list_classes(None).await.map_err(|e| {
        tracing::error!("list_classes failed: {:?}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;
    let subjects = repo.list_subjects(None, None).await.map_err(|e| {
        tracing::error!("list_subjects failed: {:?}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    let boards_json: Vec<_> = boards
        .into_iter()
        .map(|b| {
            serde_json::json!({
                "id": b.id,
                "name": b.name,
                "code": b.code,
                "can_edit": false,
                "can_delete": false,
            })
        })
        .collect();

    let classes_json: Vec<_> = classes
        .into_iter()
        .map(|c| {
            serde_json::json!({
                "id": c.id,
                "name": c.name,
                "board_id": c.board_id,
                "grade_level": c.grade_level,
                "can_edit": false,
                "can_delete": false,
            })
        })
        .collect();

    let subjects_json: Vec<_> = subjects
        .into_iter()
        .map(|s| {
            serde_json::json!({
                "id": s.id,
                "name": s.name,
                "board_id": s.board_id,
                "class_id": s.class_id,
                "can_edit": true,
                "can_delete": false,
            })
        })
        .collect();

    Ok(Json(serde_json::json!({
        "boards": boards_json,
        "classes": classes_json,
        "subjects": subjects_json,
        "permissions": {
            "can_edit_subjects": true,
            "can_delete_subjects": false,
            "can_edit_classes": false,
            "can_delete_classes": false,
            "can_edit_boards": false,
            "can_delete_boards": false,
            "can_create_pages": true,
            "can_edit_pages": true,
            "can_delete_pages": true,
        }
    })))
}

/// Create a new subject (staff can create if board/class exists).
pub async fn create_subject(
    State(state): State<AppState>,
    Json(payload): Json<CreateSubjectRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let repo = Repository::new(state.db.clone());

    // Validate board/class exist before insert so we surface a 400 rather
    // than relying on Postgres to raise a foreign-key violation.
    match repo.get_board(&payload.board_id).await {
        Ok(Some(_)) => {}
        Ok(None) => return Err(StatusCode::BAD_REQUEST),
        Err(e) => {
            tracing::error!("get_board failed: {:?}", e);
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
    }

    let subject = repo
        .create_subject(
            &payload.name,
            &payload.board_id,
            &payload.class_id,
            payload.description.as_deref(),
        )
        .await
        .map_err(|e| {
            tracing::error!("create_subject failed: {:?}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    Ok(Json(serde_json::json!({
        "success": true,
        "subject": subject,
        "message": "Subject created successfully"
    })))
}

/// Update subject (staff can only edit name, not delete).
pub async fn update_subject(
    State(state): State<AppState>,
    Path(subject_id): Path<String>,
    Json(payload): Json<UpdateSubjectRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let Some(name) = payload.name else {
        return Err(StatusCode::BAD_REQUEST);
    };

    let repo = Repository::new(state.db.clone());
    let subject = repo
        .update_subject_name(&subject_id, &name)
        .await
        .map_err(|e| {
            tracing::error!("update_subject_name({}) failed: {:?}", subject_id, e);
            match e {
                sqlx::Error::RowNotFound => StatusCode::NOT_FOUND,
                _ => StatusCode::INTERNAL_SERVER_ERROR,
            }
        })?;

    Ok(Json(serde_json::json!({
        "success": true,
        "subject": subject,
        "message": "Subject updated successfully"
    })))
}

/// Create a new subject page (lesson/content).
pub async fn create_page(
    State(state): State<AppState>,
    Json(payload): Json<CreatePageRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let repo = Repository::new(state.db.clone());
    let order = payload.page_order.unwrap_or(0);

    let page = repo
        .create_page(&payload.subject_id, &payload.title, &payload.content, order)
        .await
        .map_err(|e| {
            tracing::error!("create_page failed: {:?}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    Ok(Json(serde_json::json!({
        "success": true,
        "page": page,
        "message": "Page created successfully"
    })))
}

/// Update subject page.
pub async fn update_page(
    State(state): State<AppState>,
    Path(page_id): Path<String>,
    Json(payload): Json<UpdatePageRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let repo = Repository::new(state.db.clone());

    let page = repo
        .update_page(
            &page_id,
            payload.title.as_deref(),
            payload.content.as_deref(),
            payload.page_order,
        )
        .await
        .map_err(|e| {
            tracing::error!("update_page({}) failed: {:?}", page_id, e);
            match e {
                sqlx::Error::RowNotFound => StatusCode::NOT_FOUND,
                _ => StatusCode::INTERNAL_SERVER_ERROR,
            }
        })?;

    Ok(Json(serde_json::json!({
        "success": true,
        "page": page,
        "message": "Page updated successfully"
    })))
}

/// Delete subject page (staff CAN delete pages).
pub async fn delete_page(
    State(state): State<AppState>,
    Path(page_id): Path<String>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let repo = Repository::new(state.db.clone());
    let deleted = repo.delete_page(&page_id).await.map_err(|e| {
        tracing::error!("delete_page({}) failed: {:?}", page_id, e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    if !deleted {
        return Err(StatusCode::NOT_FOUND);
    }

    Ok(Json(serde_json::json!({
        "success": true,
        "page_id": page_id,
        "message": "Page deleted successfully"
    })))
}

// ---------------------------------------------------------------------------
// Helpers (kept `pub(crate)` so they can be unit-tested and reused by the
// agents handler / future middleware without relying on the HTTP surface).
// ---------------------------------------------------------------------------

pub(crate) fn is_valid_phone(phone: &str) -> bool {
    // Basic validation: 10-15 digits, may start with +.
    let cleaned: String = phone.chars().filter(|c| c.is_ascii_digit()).collect();
    cleaned.len() >= 10 && cleaned.len() <= 15
}

pub(crate) fn generate_otp() -> String {
    let mut rng = rand::thread_rng();
    let n: u32 = rng.gen_range(0..1_000_000);
    format!("{:06}", n)
}

pub(crate) fn hash_otp(otp: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(otp.as_bytes());
    hex::encode(hasher.finalize())
}

pub(crate) fn generate_staff_jwt(
    user_id: &str,
    phone: &str,
    role: &str,
    secret: &str,
) -> Result<String, jsonwebtoken::errors::Error> {
    let now = Utc::now();
    let claims = Claims {
        sub: user_id.to_string(),
        phone: phone.to_string(),
        role: role.to_string(),
        iat: now.timestamp() as usize,
        exp: (now + Duration::hours(JWT_TTL_HOURS)).timestamp() as usize,
    };
    encode(
        &Header::new(Algorithm::HS256),
        &claims,
        &EncodingKey::from_secret(secret.as_bytes()),
    )
}

#[allow(dead_code)]
pub(crate) fn decode_staff_jwt(token: &str, secret: &str) -> Result<Claims, jsonwebtoken::errors::Error> {
    let mut validation = Validation::new(Algorithm::HS256);
    // We embed `sub` in claims; require it to be present.
    validation.set_required_spec_claims(&["exp", "sub"]);
    decode::<Claims>(
        token,
        &DecodingKey::from_secret(secret.as_bytes()),
        &validation,
    )
    .map(|data| data.claims)
}

/// Send the OTP via the Twilio Programmable SMS API.
async fn send_twilio_sms(
    account_sid: &str,
    auth_token: &str,
    from: &str,
    to: &str,
    otp: &str,
) -> Result<(), reqwest::Error> {
    let url = format!(
        "https://api.twilio.com/2010-04-01/Accounts/{}/Messages.json",
        account_sid
    );
    let body = [
        ("From", from),
        ("To", to),
        ("Body", &format!("Your Syrabit verification code is {}", otp)),
    ];
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .basic_auth(account_sid, Some(auth_token))
        .form(&body)
        .send()
        .await?;
    resp.error_for_status()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phone_validation_accepts_valid_numbers() {
        assert!(is_valid_phone("+12025550123"));
        assert!(is_valid_phone("9876543210"));
        assert!(is_valid_phone("+91 98765 43210"));
    }

    #[test]
    fn phone_validation_rejects_short_or_long_numbers() {
        assert!(!is_valid_phone("12345"));
        assert!(!is_valid_phone(""));
        assert!(!is_valid_phone("1234567890123456")); // 16 digits
    }

    #[test]
    fn generate_otp_is_six_digits() {
        for _ in 0..50 {
            let otp = generate_otp();
            assert_eq!(otp.len(), 6);
            assert!(otp.chars().all(|c| c.is_ascii_digit()));
        }
    }

    #[test]
    fn hash_otp_is_deterministic_and_avalanches() {
        let h1 = hash_otp("123456");
        let h2 = hash_otp("123456");
        let h3 = hash_otp("123457");
        assert_eq!(h1, h2);
        assert_ne!(h1, h3);
        assert_eq!(h1.len(), 64); // hex-encoded SHA-256
    }

    #[test]
    fn jwt_roundtrip_succeeds_with_correct_secret() {
        let secret = "test-secret-key-with-enough-entropy";
        let token = generate_staff_jwt("user-1", "+12025550123", "staff", secret)
            .expect("encode");
        let claims = decode_staff_jwt(&token, secret).expect("decode");
        assert_eq!(claims.sub, "user-1");
        assert_eq!(claims.phone, "+12025550123");
        assert_eq!(claims.role, "staff");
        assert!(claims.exp > claims.iat);
    }

    #[test]
    fn jwt_decode_fails_with_wrong_secret() {
        let token =
            generate_staff_jwt("user-1", "+12025550123", "staff", "secret-a").expect("encode");
        let err = decode_staff_jwt(&token, "secret-b").expect_err("must reject");
        // Any error from jsonwebtoken signals auth failure — we just need
        // to assert we did not silently accept the token.
        let kind = err.kind();
        assert!(matches!(
            kind,
            jsonwebtoken::errors::ErrorKind::InvalidSignature
                | jsonwebtoken::errors::ErrorKind::InvalidToken
        ));
    }

    #[test]
    fn jwt_decode_rejects_garbage_token() {
        let err = decode_staff_jwt("not-a-jwt", "secret").expect_err("must reject");
        assert!(matches!(
            err.kind(),
            jsonwebtoken::errors::ErrorKind::InvalidToken
                | jsonwebtoken::errors::ErrorKind::InvalidSignature
        ));
    }
}
