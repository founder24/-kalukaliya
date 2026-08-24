-- Binds an opt-in post-deploy reset link to the exact request that issued it.
ALTER TABLE password_reset_tokens ADD COLUMN cutover_nonce TEXT;