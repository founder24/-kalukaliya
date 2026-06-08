---
name: JWT RS256→HS256 migration fallback
description: During algorithm migration, logout must try current alg then fall back to legacy alg for in-flight tokens
---

When JWT_ALGORITHM changes from RS256 to HS256 in production, tokens already issued with RS256 are still live (up to the access token TTL, typically 15 min).  The logout endpoint re-decodes the access token to get its expiry — this fails with `InvalidAlgorithmError` for old RS256 tokens after the switch.

**Why:** `_get_verification_key()` returns the current configured key/alg, so decoding an RS256 token with `algorithms=["HS256"]` raises an error even though the token is cryptographically valid.

**How to apply:**
- Use `_decode_token_with_fallback(token)` in logout (and any other non-auth-critical decode path) instead of raw `jwt.decode(token, key, algorithms=[algorithm])`
- The fallback: try primary alg, catch Exception, if primary is HS256 and JWT_PUBLIC_KEY is set → retry with RS256
- Remove the RS256 branch once all old tokens have expired (i.e., after 1 access-token TTL past the algorithm switch date)
- The standard `get_current_user` path intentionally does NOT use the fallback (it validates the incoming request token, which must already use the current algorithm by the time the rollover is complete)
