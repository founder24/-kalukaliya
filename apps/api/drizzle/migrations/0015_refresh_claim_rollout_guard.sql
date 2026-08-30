-- Release evidence for the refresh-token KV bridge safety window.
--
-- The first row is written only after a production API Worker deployment
-- succeeds. Keeping the evidence in D1 makes it durable across GitHub runner
-- lifetimes and prevents a cleanup release from relying on a local timestamp.
CREATE TABLE IF NOT EXISTS refresh_claim_rollout (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  first_deployed_at INTEGER NOT NULL,
  first_version TEXT NOT NULL,
  last_deployed_at INTEGER NOT NULL,
  last_version TEXT NOT NULL,
  successful_deployments INTEGER NOT NULL DEFAULT 1 CHECK (successful_deployments > 0)
);