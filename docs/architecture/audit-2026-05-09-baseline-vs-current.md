# Audit: baseline (Task #5) → current (Task #15) — 2026-05-09

Scope: every project task that ran since the 2026 architecture lock
was first written (commit `7b9c811`, Task #5). That is **13 tasks**
(`#1, #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15`) and
**~19 commits**, totalling **408 files / +11 745 / −12 997 lines**.
The "20 task upgrades" framing in the prompt is the rolling
total once proposed follow-ups (#23–#29) are counted; this audit
covers only the 13 that actually merged.

The single most important finding sits in §3 below: **the
architecture matrix was not flipped to reflect the work that
shipped**. Code-on-disk and matrix-on-disk disagree in 16 rows,
all in the direction of "matrix is more pessimistic than reality".

## 1 · What ran (chronological)

| # | Task | Outcome | Commits | Net churn |
|---|------|---------|---------|-----------|
| 1 | Trim `replit.md`, extract decisions | MERGED | 1 | small |
| 4 | Claim AWS Explore credits + cleanup | MERGED | 2 | +278 / −1 |
| 5 | Architecture audit & lock (2026 blueprint) | MERGED | 2 | created lock + matrix |
| 6 | Backend dead-code & provider purge | MERGED | 4 | net −large |
| 7 | Frontend dead-code & package purge | MERGED | 2 | net negative |
| 8 | Repo trim & `replit.md` rewrite | MERGED | 1 | shaped the README we use today |
| 9 | Bot management — unblock search & AI crawlers | MERGED | 15 | +large; introduced verified-bot KV split + `gen_bot_regex.py` + `tests/test_bot_unblock.py` |
| 10 | Semantic cache fingerprint + deterministic render | MERGED | 4 | added `cache_fingerprint.py` + `templates/deterministic/*` |
| 11 | Programmatic SEO/GEO/AEO engine | MERGED | 7 | added `routes/seo_pages.py` + `templates/seo/chapter.html.j2` + IndexNow extension |
| 12 | AEO answer-card & FAQ materialization | MERGED | 2 | +1 472 lines; added `aca_jobs/materialize_chapter_faqs.py` + FAQ JSON-LD endpoint |
| 13 | Prewarming + dynamic TTL engine | MERGED | 6 | +2 094 lines; added `aca_jobs/prewarm_seo_routes.py` + cache-calendar wiring + admin tile |
| 14 | Fix failing workflows & make app functional | MERGED | 3 | rewrote workflow contract → 3-service artifact-managed model |
| 15 | E2E validation & ranking baseline | MERGED (offline slice) | 1 | +730 lines; `scripts/seo_baseline.py` + Playwright SEO journey + ranking playbook |

## 2 · What ships on disk today (verified)

| Promised by | Artifact | Status |
|-------------|----------|--------|
| #9  | `scripts/gen_bot_regex.py` | EXISTS |
| #9  | `tests/test_bot_unblock.py` | EXISTS (521 lines) |
| #9  | Verified-bot split in `workers/edge-proxy/src/index.ts` | EXISTS (+370 net) |
| #10 | `artifacts/syrabit-backend/cache_fingerprint.py` | EXISTS |
| #11 | `artifacts/syrabit-backend/routes/seo_pages.py` | EXISTS |
| #11 | `artifacts/syrabit-backend/templates/seo/chapter.html.j2` | EXISTS (incl. `data-aeo-block` markers) |
| #12 | `artifacts/syrabit-backend/aca_jobs/materialize_chapter_faqs.py` | EXISTS |
| #13 | `artifacts/syrabit-backend/aca_jobs/prewarm_seo_routes.py` | EXISTS |
| #15 | `scripts/seo_baseline.py` | EXISTS (408 lines) |
| #15 | `docs/architecture/ranking-playbook.md` | EXISTS |

Every file the upstream tasks promised is present on disk. So the
**code half** of the audit is clean — every IN-SCOPE deliverable
landed.

## 3 · Drift: matrix vs. reality (the headline finding)

`infra/architecture-matrix.json` was last edited at commit `a44a019`
(Task #6). **Tasks #7 → #15 did not touch it.** That is why row
counts are *identical* before and after the 13-task chain:

| Status | Baseline (#5) | Current | Δ |
|--------|---------------|---------|---|
| IMPLEMENTED | 87 | 87 | 0 |
| PARTIAL | 14 | 14 | 0 |
| MISSING | 2 | 2 | 0 |
| RETIRED | 2 | 1 | −1 |
| FILE_DELETED | 0 | 1 | +1 |

The 16 rows below say **"PARTIAL — Task #X will fix"** for a Task
that *already merged*. They should each be flipped to IMPLEMENTED
(or at least the note should be rewritten in past tense) in a
single matrix-cleanup PR. Until that happens, every consumer of
the matrix (the `check_architecture_lock.py` guard, the
`replit.md` summary, the §15.7 "zero MISSING in-scope rows"
deliverable) is reading a stale picture.

| Section | Item | Status today | Already shipped by |
|---------|------|--------------|--------------------|
| §2 Core Principles | SEO-driven distribution | PARTIAL | #11 |
| §4.1 Cloudflare | WAF + Bot Management | PARTIAL | #9 |
| §4.1 Cloudflare | Cron Triggers — prewarming | PARTIAL | #13 |
| §4.2 Azure | Azure Blob — temporary OCR/media | RETIRED | #46 (row retired; R2 is canonical OCR scratch — docs updated in #48) |
| §7 AI Pipeline | Stage 8 — Deterministic materialization | PARTIAL | #12 + #13 |
| §9 Adv. cache | #572 Semantic query fingerprinting | **MISSING** | #10 |
| §9 Adv. cache | #573 Deterministic educational rendering | PARTIAL | #10 |
| §9 Adv. cache | #574 Prewarming engine | **MISSING** | #13 |
| §10 SEO | Generated content types (notes/MCQ/flashcards/PYQ) | PARTIAL | #11 |
| §10 SEO | IndexNow submission | PARTIAL | #11 |
| §10 SEO | AEO answer cards + FAQ JSON-LD | PARTIAL | #12 |
| §13 Security | Authentication — Supabase sole IdP | PARTIAL | (still PARTIAL — legacy email/password endpoints not yet retired) |
| §13 Security | Edge security — verified-bot KV fast path | PARTIAL | #9 |
| §15 Cost gov. | Heavy-free user flow — prewarm leg | PARTIAL | #13 |
| §17 Build phases | Phase 2 Scale (prewarm) | PARTIAL | #13 |
| §… Strategic moats | Assamese educational corpus | PARTIAL | (correct — corpus growth ongoing) |

**15 of 16 rows are stale.** Only "Assamese corpus" remains
genuinely PARTIAL on the merits — the "Azure Blob" row was
retired by Task #46 (R2 is now the canonical OCR scratch; docs
updated in #48).

## 4 · Drift: documented founder-locks

| Lock | Source | State | Verdict |
|------|--------|-------|---------|
| Monthly USD ≤ $100 | `cost_caps.py::_DEFAULT_MONTHLY_TOTAL_USD_CAP` | unchanged | OK |
| Voice paywall on `/voice/{tts,stt,voice}` | enforced in routes | unchanged | OK |
| 60/80/95 % degradation ladder strictly increasing | `cost_caps.py` | unchanged | OK |
| Sarvam = sole Assamese head | canonical chain | unchanged | OK |
| Pinecone dim = 1024 | quarantine guard | unchanged | OK |
| Supabase = sole auth | code path live, **legacy email/password endpoints still present** | drift unchanged from baseline | needs Task #X cutover |
| No silent fallbacks (V4 §12) | scattered | reinforced by #15 fail-loud edits | OK |
| Token budget overrides require `# COST-CAP-OVERRIDE:` marker | `tests/test_cost_caps.py` | unchanged | OK |
| AWS budget mirror = $100 + 60/80/95 % | added by #4 | NEW | OK |

## 5 · Drift: CI / guard scripts referenced from `replit.md`

| Referenced as | Real path | State |
|----------------|-----------|-------|
| `scripts/check_architecture_lock.py` | EXISTS | OK |
| `scripts/check_budget_ceiling.py` | **DOES NOT EXIST** | covered by follow-up #26 |
| `scripts/check_canonical_delegation.py` | only at `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` | repo-root shim covered by follow-up #25 |

The `replit.md` "Founder locks (always win)" section name-checks
`scripts/check_budget_ceiling.py` as if it ran on every PR. It
does not exist yet. That is a documentation lie of omission — fix
in #26 by either adding the script or removing the reference.

## 6 · Drift: workflow contract

Task #14 deliberately replaced the original "5-workflow contract"
described in older runbooks with a 3-service artifact-managed model
driven by `.replit-artifact/artifact.toml`:

- `artifacts/syrabit: api` (port 8080)
- `artifacts/syrabit: web` (port 25144)
- `artifacts/mockup-sandbox: Component Preview Server` (port 8081)

Plus on-demand `dev_health` and `tf-apply`. This is documented in
`docs/dev/task-14-workflow-triage-2026-05-09.md` and is the
canonical contract today. **Drift here is intentional and
documented**, not a finding.

## 7 · Open / proposed follow-ups parked against this chain

| Ref | Title | Category | Gates on |
|-----|-------|----------|----------|
| #25 | Canonical-delegation umbrella shim at repo root | tech_debt | nothing — pickable today |
| #26 | `scripts/check_budget_ceiling.py` umbrella green | tech_debt | nothing — pickable today |
| #27 | Cohere-via-Bedrock embed route for Indic queries | next_steps | Bedrock provisioning |
| #28 | Weekly EventBridge → Lambda for `seo_baseline.py` + admin tile | incomplete_scope | needs §3 matrix flips for #10/#11 to be honest |
| #29 | 14-day post-#10/#12 KV hit-ratio confirmation + matrix flips | incomplete_scope | needs 14 d of prod traffic |
| #23, #24 | Task #14 workflow follow-ups | tech_debt / test_gaps | nothing |

## 8 · Recommended next move

**Land a single "matrix-cleanup PR"** that does only one thing: walk
the 14 stale rows in §3, flip them to IMPLEMENTED with a
`source_paths` reference to the file that already exists on disk,
and rewrite each `note` from future-tense ("Task #X adds…") to
past-tense ("Task #X added…, see <path>"). This:

- Unblocks Task #15's §7 deliverable ("zero MISSING in-scope rows").
- Makes the `replit.md` summary table honest again.
- Gives the next agent a true picture before they reach for #28 or #29.

After that, pick up #25 + #26 (both are small CI shims with no
dependencies) so the umbrella CI guard the founder-locks claim
actually exists. Only then does it make sense to schedule the
weekly baseline (#28) — running it against an inconsistent matrix
would just bake the inconsistency into a weekly report.
