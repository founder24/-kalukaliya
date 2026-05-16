"""User account deletion and data export routes for GDPR/DPDP compliance.

This module provides endpoints for:
- User-initiated account deletion requests (GDPR Art. 17 / DPDP Act 2023)
- Data export requests (GDPR Art. 15 - Right of Access)
- Deletion status checking
- Grace period cancellation

All endpoints require authenticated users and implement proper authorization.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
import logging

from auth_deps import get_current_user
from data_retention import (
    process_deletion_request,
    get_user_data_export,
    process_grace_period_deletions,
    enforce_data_retention,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/account/delete-request")
async def request_account_deletion(
    user: dict = Depends(get_current_user),
    grace_period_days: int = 30
) -> Dict[str, Any]:
    """
    Initiate account deletion with a grace period.
    
    This endpoint implements GDPR Art. 17 (Right to Erasure) and 
    DPDP Act 2023 Section 9 (Data Retention and Deletion).
    
    Process:
    1. Marks account for deletion after grace period (default 30 days)
    2. Immediately anonymizes PII to protect privacy
    3. Disables account access
    4. Schedules final deletion job
    
    During the grace period, users can cancel the deletion request.
    
    Args:
        user: Authenticated user from JWT token
        grace_period_days: Days before final deletion (default 30)
    
    Returns:
        Deletion request confirmation with grace period details
    
    Raises:
        HTTPException: If user not found or request fails
    """
    try:
        result = await process_deletion_request(
            user_id=user["id"],
            email=user["email"],
            grace_period_days=grace_period_days
        )
        
        logger.info(
            f"Deletion request initiated for user {user['id']} "
            f"(grace period: {grace_period_days} days)"
        )
        
        return {
            "status": "success",
            "message": "Account deletion scheduled",
            "details": {
                "grace_period_days": grace_period_days,
                "grace_period_ends": result["grace_period_ends"],
                "immediate_actions": result["immediate_actions"],
                "can_cancel_until": result["grace_period_ends"],
                "reference_id": user["id"]
            },
            "compliance": {
                "gdpr_art_17": True,
                "dpdp_section_9": True,
                "data_export_available": True
            }
        }
    
    except ValueError as e:
        logger.warning(f"Invalid deletion request: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to process deletion request for {user['id']}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process deletion request. Please contact support."
        )


@router.get("/account/deletion-status")
async def get_deletion_status(
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Check the current deletion status of the user's account.
    
    Returns information about:
    - Whether a deletion request is pending
    - Grace period end date
    - Whether cancellation is still possible
    
    Args:
        user: Authenticated user from JWT token
    
    Returns:
        Current deletion status and timeline
    """
    from deps import db
    
    try:
        deletion_request = await db.deletion_requests.find_one({
            "user_id": user["id"],
            "status": {"$in": ["pending", "scheduled"]}
        })
        
        if not deletion_request:
            return {
                "status": "no_pending_request",
                "account_active": True
            }
        
        grace_period_ends = deletion_request.get("grace_period_ends")
        now = datetime.now(timezone.utc)
        
        can_cancel = False
        if grace_period_ends:
            if isinstance(grace_period_ends, datetime):
                can_cancel = grace_period_ends > now
            elif isinstance(grace_period_ends, str):
                can_cancel = datetime.fromisoformat(grace_period_ends.replace('Z', '+00:00')) > now
        
        return {
            "status": "pending_deletion",
            "requested_at": deletion_request.get("requested_at"),
            "grace_period_ends": grace_period_ends,
            "can_cancel": can_cancel,
            "days_remaining": (
                (grace_period_ends - now).days if can_cancel and grace_period_ends else 0
            ),
            "account_active": True,
            "pii_anonymized": True
        }
    
    except Exception as e:
        logger.error(f"Error checking deletion status for {user['id']}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve deletion status"
        )


@router.post("/account/cancel-deletion")
async def cancel_deletion_request(
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Cancel a pending account deletion request.
    
    Users can cancel their deletion request anytime before the grace period expires.
    This will:
    - Restore full account access
    - Remove the pending deletion flag
    - Keep anonymized PII (for security, previously anonymized data is not restored)
    
    Args:
        user: Authenticated user from JWT token
    
    Returns:
        Confirmation of cancellation
    
    Raises:
        HTTPException: If no pending request or grace period expired
    """
    from deps import db
    from data_retention import supa_update_user
    
    try:
        # Check for pending deletion
        deletion_request = await db.deletion_requests.find_one({
            "user_id": user["id"],
            "status": "pending"
        })
        
        if not deletion_request:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No pending deletion request found"
            )
        
        # Check if grace period has expired
        grace_period_ends = deletion_request.get("grace_period_ends")
        now = datetime.now(timezone.utc)
        
        if grace_period_ends:
            if isinstance(grace_period_ends, datetime):
                if grace_period_ends <= now:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Grace period has expired. Deletion cannot be cancelled."
                    )
            elif isinstance(grace_period_ends, str):
                ends_dt = datetime.fromisoformat(grace_period_ends.replace('Z', '+00:00'))
                if ends_dt <= now:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Grace period has expired. Deletion cannot be cancelled."
                    )
        
        # Update deletion request status
        await db.deletion_requests.update_one(
            {"user_id": user["id"]},
            {"$set": {"status": "cancelled", "cancelled_at": now}}
        )
        
        # Restore account status in Supabase
        await supa_update_user(user["id"], {
            "deletion_requested_at": None,
            "deletion_hard_at": None,
            "account_status": "active"
        })
        
        logger.info(f"Deletion request cancelled for user {user['id']}")
        
        return {
            "status": "success",
            "message": "Account deletion cancelled",
            "account_status": "active",
            "note": "Some data was anonymized during the deletion request and cannot be restored"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel deletion for {user['id']}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel deletion request"
        )


@router.get("/account/data-export")
async def request_data_export(
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Export all user data (GDPR Art. 15 - Right of Access).
    
    This endpoint provides a complete export of all personal data
    associated with the user's account.
    
    Args:
        user: Authenticated user from JWT token
    
    Returns:
        Complete user data export in structured format
    
    Compliance Notes:
        - GDPR Art. 15: Right of access by data subject
        - DPDP Act 2023: Right to access and correct data
        - CCPA: Right to know what personal information is collected
    """
    try:
        export_data = await get_user_data_export(user["id"])
        
        logger.info(f"Data export requested for user {user['id']}")
        
        return {
            "status": "success",
            "export_date": export_data["metadata"]["export_date"],
            "user_id": user["id"],
            "data": export_data,
            "format": "json",
            "compliance": {
                "gdpr_art_15": True,
                "ccpa_right_to_know": True,
                "dpdp_right_to_access": True
            }
        }
    
    except Exception as e:
        logger.error(f"Failed to export data for {user['id']}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export user data"
        )


@router.post("/admin/cron/enforce-retention")
async def cron_enforce_retention(
    admin_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Admin-only cron endpoint to enforce data retention policies.
    
    This should be called daily by a cron scheduler to:
    1. Delete data past its retention period
    2. Process grace period deletions for users who requested deletion
    
    Security:
        - Requires authenticated admin user
        - Should be called via internal service account
    
    Args:
        admin_user: Authenticated admin user
    
    Returns:
        Summary of retention enforcement actions
    """
    # Verify admin privileges
    if not admin_user.get("is_admin") and not admin_user.get("role") == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    
    try:
        # Enforce automatic data retention
        retention_results = await enforce_data_retention()
        
        # Process grace period deletions
        deletion_results = await process_grace_period_deletions()
        
        logger.info(
            f"Retention enforcement completed: {retention_results}, "
            f"Deletions processed: {deletion_results}"
        )
        
        return {
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "retention_enforcement": retention_results,
            "deletion_processing": deletion_results,
            "summary": {
                "tables_cleaned": len([k for k in retention_results.keys() if '_error' not in k]),
                "users_deleted": deletion_results.get("successful", 0),
                "deletion_failures": deletion_results.get("failed", 0)
            }
        }
    
    except Exception as e:
        logger.error(f"Retention enforcement cron failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retention enforcement failed: {str(e)}"
        )
