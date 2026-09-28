CREATE TABLE `auth_rate_limits` (
  `bucket_key` text PRIMARY KEY NOT NULL,
  `request_count` integer NOT NULL DEFAULT 1,
  `expires_at` integer NOT NULL,
  `updated_at` integer NOT NULL
);

CREATE INDEX `auth_rate_limits_expiry_idx` ON `auth_rate_limits` (`expires_at`);