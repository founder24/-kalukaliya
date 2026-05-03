//! Database repository for CRUD operations

use sqlx::PgPool;
use crate::db::models::*;
use uuid::Uuid;
use chrono::Utc;

/// Repository for database operations.
///
/// The struct and most of its methods are not constructed yet — handlers
/// currently operate on mocked data. The Repository is part of the
/// crate's public surface and will be wired up in follow-up #302
/// (replace staff handler stubs). Suppress the dead-code lint until then.
#[allow(dead_code)]
pub struct Repository {
    pool: PgPool,
}

#[allow(dead_code)]
impl Repository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    // Board operations (read-only for staff)
    
    pub async fn list_boards(&self) -> Result<Vec<Board>, sqlx::Error> {
        sqlx::query_as("SELECT * FROM boards ORDER BY name")
            .fetch_all(&self.pool)
            .await
    }

    pub async fn get_board(&self, id: &str) -> Result<Option<Board>, sqlx::Error> {
        sqlx::query_as("SELECT * FROM boards WHERE id = $1")
            .bind(id)
            .fetch_optional(&self.pool)
            .await
    }

    // Class operations (read-only for staff)
    
    pub async fn list_classes(&self, board_id: Option<&str>) -> Result<Vec<Class>, sqlx::Error> {
        let query = match board_id {
            Some(_) => "SELECT * FROM classes WHERE board_id = $1 ORDER BY grade_level",
            None => "SELECT * FROM classes ORDER BY grade_level",
        };
        
        let mut q = sqlx::query_as(query);
        if let Some(bid) = board_id {
            q = q.bind(bid);
        }
        
        q.fetch_all(&self.pool).await
    }

    // Subject operations (staff can create and edit name)
    
    pub async fn list_subjects(
        &self,
        board_id: Option<&str>,
        class_id: Option<&str>,
    ) -> Result<Vec<Subject>, sqlx::Error> {
        let mut query = String::from("SELECT * FROM subjects WHERE 1=1");
        let mut params: Vec<&str> = Vec::new();
        
        if let Some(bid) = board_id {
            query.push_str(&format!(" AND board_id = ${}", params.len() + 1));
            params.push(bid);
        }
        
        if let Some(cid) = class_id {
            query.push_str(&format!(" AND class_id = ${}", params.len() + 1));
            params.push(cid);
        }
        
        query.push_str(" ORDER BY name");
        
        let mut q = sqlx::query_as(&query);
        for param in params {
            q = q.bind(param);
        }
        
        q.fetch_all(&self.pool).await
    }

    pub async fn create_subject(
        &self,
        name: &str,
        board_id: &str,
        class_id: &str,
        description: Option<&str>,
    ) -> Result<Subject, sqlx::Error> {
        let id = Uuid::new_v4().to_string();
        
        sqlx::query_as(
            "INSERT INTO subjects (id, name, board_id, class_id, description)
             VALUES ($1, $2, $3, $4, $5)
             RETURNING *"
        )
        .bind(&id)
        .bind(name)
        .bind(board_id)
        .bind(class_id)
        .bind(description)
        .fetch_one(&self.pool)
        .await
    }

    pub async fn update_subject_name(
        &self,
        id: &str,
        name: &str,
    ) -> Result<Subject, sqlx::Error> {
        sqlx::query_as(
            "UPDATE subjects SET name = $1, updated_at = NOW()
             WHERE id = $2
             RETURNING *"
        )
        .bind(name)
        .bind(id)
        .fetch_one(&self.pool)
        .await
    }

    // Subject Page operations (staff can full CRUD)
    
    pub async fn list_pages(&self, subject_id: &str) -> Result<Vec<SubjectPage>, sqlx::Error> {
        sqlx::query_as(
            "SELECT * FROM subject_pages
             WHERE subject_id = $1
             ORDER BY page_order"
        )
        .bind(subject_id)
        .fetch_all(&self.pool)
        .await
    }

    pub async fn create_page(
        &self,
        subject_id: &str,
        title: &str,
        content: &str,
        page_order: i32,
    ) -> Result<SubjectPage, sqlx::Error> {
        let id = Uuid::new_v4().to_string();
        
        sqlx::query_as(
            "INSERT INTO subject_pages (id, subject_id, title, content, page_order)
             VALUES ($1, $2, $3, $4, $5)
             RETURNING *"
        )
        .bind(&id)
        .bind(subject_id)
        .bind(title)
        .bind(content)
        .bind(page_order)
        .fetch_one(&self.pool)
        .await
    }

    pub async fn update_page(
        &self,
        id: &str,
        title: Option<&str>,
        content: Option<&str>,
        page_order: Option<i32>,
    ) -> Result<SubjectPage, sqlx::Error> {
        sqlx::query_as(
            "UPDATE subject_pages
             SET title = COALESCE($1, title),
                 content = COALESCE($2, content),
                 page_order = COALESCE($3, page_order),
                 updated_at = NOW()
             WHERE id = $4
             RETURNING *"
        )
        .bind(title)
        .bind(content)
        .bind(page_order)
        .bind(id)
        .fetch_one(&self.pool)
        .await
    }

    pub async fn delete_page(&self, id: &str) -> Result<bool, sqlx::Error> {
        let result = sqlx::query("DELETE FROM subject_pages WHERE id = $1")
            .bind(id)
            .execute(&self.pool)
            .await?;
        
        Ok(result.rows_affected() > 0)
    }

    // Staff user operations
    
    pub async fn get_staff_by_phone(&self, phone: &str) -> Result<Option<StaffUser>, sqlx::Error> {
        sqlx::query_as("SELECT * FROM staff_users WHERE phone = $1 AND is_active = true")
            .bind(phone)
            .fetch_optional(&self.pool)
            .await
    }

    pub async fn create_staff_user(
        &self,
        phone: &str,
        name: Option<&str>,
    ) -> Result<StaffUser, sqlx::Error> {
        let id = Uuid::new_v4().to_string();
        
        sqlx::query_as(
            "INSERT INTO staff_users (id, phone, name, role, is_active)
             VALUES ($1, $2, $3, 'staff', true)
             RETURNING *"
        )
        .bind(&id)
        .bind(phone)
        .bind(name)
        .fetch_one(&self.pool)
        .await
    }

    // OTP operations
    
    pub async fn store_otp(
        &self,
        phone: &str,
        otp_hash: &str,
        expires_at: chrono::DateTime<Utc>,
    ) -> Result<OtpRecord, sqlx::Error> {
        let id = Uuid::new_v4().to_string();
        
        sqlx::query_as(
            "INSERT INTO otp_records (id, phone, otp_hash, expires_at, is_used)
             VALUES ($1, $2, $3, $4, false)
             RETURNING *"
        )
        .bind(&id)
        .bind(phone)
        .bind(otp_hash)
        .bind(expires_at)
        .fetch_one(&self.pool)
        .await
    }

    pub async fn get_valid_otp(&self, phone: &str) -> Result<Option<OtpRecord>, sqlx::Error> {
        sqlx::query_as(
            "SELECT * FROM otp_records
             WHERE phone = $1 AND is_used = false AND expires_at > NOW()
             ORDER BY created_at DESC
             LIMIT 1"
        )
        .bind(phone)
        .fetch_optional(&self.pool)
        .await
    }

    pub async fn mark_otp_used(&self, id: &str) -> Result<bool, sqlx::Error> {
        let result = sqlx::query("UPDATE otp_records SET is_used = true WHERE id = $1")
            .bind(id)
            .execute(&self.pool)
            .await?;
        
        Ok(result.rows_affected() > 0)
    }
}
