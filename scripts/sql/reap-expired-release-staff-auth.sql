DELETE FROM refresh_token_claims
  WHERE user_id IN (
    SELECT fixture_id FROM release_staff_auth_leases
    WHERE expires_at <= unixepoch()
  );
DELETE FROM content_audit_log
  WHERE user_id IN (
      SELECT fixture_id FROM release_staff_auth_leases
      WHERE expires_at <= unixepoch()
    )
    OR target_id IN (
      SELECT fixture_id FROM release_staff_auth_leases
      WHERE expires_at <= unixepoch()
    );
DELETE FROM analytics_events
  WHERE id IN (
      SELECT telemetry_id FROM release_staff_auth_leases
      WHERE expires_at <= unixepoch()
    )
    OR route_path IN (
      SELECT telemetry_route FROM release_staff_auth_leases
      WHERE expires_at <= unixepoch()
    );
DELETE FROM users
  WHERE id IN (
      SELECT fixture_id FROM release_staff_auth_leases
      WHERE expires_at <= unixepoch()
    );
DELETE FROM release_staff_auth_leases WHERE expires_at <= unixepoch();