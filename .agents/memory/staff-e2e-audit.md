---
name: Staff panel E2E audit
description: Results of the full staff→student end-to-end audit; bugs found and fixed; test credentials
---

## Verified working (July 2026)
- Staff login: `staff-test@syrabit.ai` / `StaffTest123!` (role=staff, in `syrabit_prod` DB)
- Staff panel at `/staff` — Board→Class→Stream→Subject cascade, chapter list, editor modal
- Notes/Questions/PYQ Content + RAG tabs all save correctly
- Public API serves `notes_en` (fallback `content_en`) as `"content"` to students — pipeline confirmed

## Bugs found and fixed
1. **notes_en → public API disconnect** (critical): `public_content.py` `chapter-by-slug` was serving `content_en` as `"content"`; fixed to prefer `notes_en || content_en` (same for AS).
2. **has_assamese flag**: library bundle only checked `content_as`; fixed to `notes_as or content_as`.
3. **word_count**: staff save computed from `content_en`; fixed to `notes_en or content_en`.
4. **notes_generated / library bundle counts**: didn't count `notes_en` chapters; fixed in all 3 bundle endpoints.
5. **faq_entries alias**: chapter API now returns both `faq_jsonld` and `faq_entries` (alias expected by ChapterPage preload).
6. **ChapterPage.jsx JSX crash**: orphaned duplicate PYQ block from rebase conflict (lines ~1397-1422); removed.
7. **AiHealthWidget.jsx adminToken**: used `adminToken` as free variable → `p.adminToken`.
8. **AdminContentEditor subjects/boards/classes/streams**: admin API returns `{subjects:[...], total:N}` but frontend did `sub.data || []`; fixed to `sub.data?.subjects || sub.data || []`.

## DB notes
- Production DB name is `syrabit_prod` (not `syrabit`). Direct Motor queries must use `client['syrabit_prod']`.
- User model uses `hashed_password` field (not `password_hash`). `_bcrypt_safe` uses `.digest()` (binary), not `.hexdigest()`.
- Test staff user was upserted via `update_one(..., upsert=True)` — avoids the `id:null` unique index error on raw `insert_one`.
