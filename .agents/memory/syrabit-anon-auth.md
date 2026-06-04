---
name: Syrabit anon user auth
description: IP-primary anonymous identity system — what was broken, what was fixed, how it works now
---

## Rule
Anonymous user identity is now IP-primary. `core/anon.py::resolve_anon_id(request)` is the single source of truth for all anon endpoints. Call it; do not read `x-anon-id` headers directly.

**Why:** 6 separate bugs caused anon users to get 401s, see wrong credits, and have their chats stored under `None`/`"anonymous"` (a shared bucket). IP-as-auth was the stated design intent, not implemented correctly.

**How to apply:** Any new endpoint that serves anon users must:
1. Use `get_current_user_optional` (not `get_current_user`)
2. Call `resolve_anon_id(request)` to get the `ip_*` or `anon_*` key
3. Never fall back to literal string `"anonymous"` as a user_id

## Bugs fixed (all in one session)

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `users.py:/credits` | Used `get_current_user` (strict) → 401 for anon | Changed to `get_current_user_optional` + Redis lookup |
| 2 | `chat.py:chat()` | `user_id` defaulted to `"anonymous"` string | `resolve_anon_id(http_request)` |
| 3 | `chat.py:chat_stream()` | Same `"anonymous"` literal fallback | `resolve_anon_id(http_request)` |
| 4 | `chat.py:save_chat()` | `user_id=user_id if user else None` | `user_id=user_id` (always passes resolved id) |
| 5 | `chat.py:get_chat_history()` | `_ANON_ID_PATTERN = r"^anon_[a-f0-9]{32}$"` — rejected ip_* keys | Replaced with `ANON_ID_PATTERN` from `core.anon` |
| 6 | `conversations.py` anon endpoints | Read `x-anon-id` header directly | `_resolve_request_anon_id(request)` via `resolve_anon_id()` |

## Identity resolution order (core/anon.py)
1. `X-Real-IP` header (reverse proxy / edge)
2. `X-Forwarded-For` first hop
3. `request.client.host` (direct TCP)
4. `x-anon-id` header (browser localStorage — legacy fallback only)
5. `"anon_unknown"` (last resort)

## Key format
`ip_` prefix + regex `[^a-z0-9]` → `_` + max 55 chars
Examples: `127.0.0.1` → `ip_127_0_0_1`, `::1` → `ip___1`

## ANON_ID_PATTERN (in core/anon.py)
```python
re.compile(r"^(?:ip_[a-z0-9_]{6,62}|anon_[a-f0-9]{32}|anon_unknown)$")
```
All anon endpoints use this pattern. IP-based keys and legacy anon_* keys both match.

## Rate limit key
`rate:{user_id}:{YYYY-MM}` — same key for anon and authed users.
Legacy `rate_anon:{ip}:{month}` key is dead; new key is `rate:ip_127_0_0_1:2026-06`.

## /user/credits response for anon
```json
{ "credits_remaining": 28, "credits_used": 2, "monthly_limit": 30, "tier": "anonymous", "anon_id": "ip_127_0_0_1" }
```
Frontend reads `credits_used` + `monthly_limit` — both present.
