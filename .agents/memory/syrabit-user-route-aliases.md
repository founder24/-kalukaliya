---
name: Syrabit user route aliases
description: Frontend ProfilePage calls /user/profile, /user/account, /user/memories — these are different from the backend /users/me routes.
---

## The Rule
The users router is mounted at BOTH `/api/v1/users` AND `/api/v1/user` in main.py.
But the frontend calls **different path suffixes** than the backend defines:

| Frontend call | Backend route (correct) |
|---|---|
| GET/PATCH `/user/profile` | `/users/me` (GET) + `/users/me` (PUT) |
| DELETE `/user/account` | `/users/me` (DELETE) |
| POST `/user/account/cancel-delete` | (was missing) |
| GET/DELETE `/user/memories` | (was missing — memory_brain collection) |
| DELETE `/user/memories/{id}` | (was missing) |

## Fix Applied
Added alias routes to `apps/backend/app/api/v1/users.py`:
- `GET /profile` → same as `/me`
- `PATCH /profile` → expanded PatchProfileRequest (name, preferred_language, ads_opt_out, board_id/name, class_id/name, stream_id/name, phone)
- `DELETE /account` → same as `/me` DELETE
- `POST /account/cancel-delete` → clears deletion_scheduled_at
- `GET /memories` → paginated query on memory_brain collection
- `DELETE /memories` → bulk delete from memory_brain
- `DELETE /memories/{memory_id}` → scoped single delete

**Why:** Frontend was written expecting singular `/user/` prefix with different endpoint names than the backend's `/users/me` convention. Rather than changing the frontend, alias routes were added to bridge the gap.

**How to apply:** Any new profile-related feature should add BOTH the canonical `/me` route AND a `/profile` alias if the frontend expects it.
