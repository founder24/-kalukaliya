-- ROI audit history always filters by this action/target tuple and reads the
-- newest records by the same timestamp/ID ordering used by its cursor.
-- Keep cal_expires_idx separate because the hourly TTL cleanup uses expires_at.
CREATE INDEX IF NOT EXISTS cal_roi_download_history_idx
  ON content_audit_log(action, target_type, target_id, created_at, id);