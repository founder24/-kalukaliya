# Task #15 — what landed and what is deferred (2026-05-09)

Task #15 is the *closing baseline* for the #5–#13 chain. It depends on
those upstream tasks being merged AND on 14 days of post-merge
production traffic to compute the cache-hit-ratio target. At the time
this task closed, most upstream tasks were still PENDING. Per the
user's explicit choice (chat 2026-05-09), the agent shipped every
deliverable that does NOT need real prod signal and explicitly deferred
the rest. This file records that split so the next agent picking this
up does not redo the offline parts.

## Landed

| Deliverable | Path | Notes |
| --- | --- | --- |
| SEO baseline driver | [`scripts/seo_baseline.py`](../../scripts/seo_baseline.py) | Lighthouse + JSON-LD validator + Google Rich Results sampler. Fails loud on missing tools / 429. CLI-runnable today; the first meaningful run waits on #10 + #11. |
| Baseline JSON skeleton | [`docs/seo/baseline-2026-Q2.json`](../seo/baseline-2026-Q2.json) | Empty placeholder with `deferred_until` annotation; first overwrite happens after #10 + #11 merge. |
| Playwright SEO/AEO journey | [`artifacts/syrabit/tests/seo-journey.spec.ts`](../../artifacts/syrabit/tests/seo-journey.spec.ts) | Googlebot leg asserts JSON-LD + Quick-Answer + LCP ≤ 2.5 s; PerplexityBot leg asserts 200 + edge-cache HIT on warm fetch. Drift from task brief: lives under the existing `artifacts/syrabit/tests/` Playwright suite, not a new top-level `tests/e2e/` (the only configured `playwright.config.ts` is the workspace one). |
| Ranking playbook | [`docs/architecture/ranking-playbook.md`](ranking-playbook.md) | Levers × impact × lead-time matrix; explicit "won't pull" list; weekly measurement loop. Impact column is qualitative until the first 14-day post-merge baseline replaces it with observed deltas. |

## Deferred — and the gate that unblocks each

| Deferred deliverable | Blocked on | Why it cannot land now |
| --- | --- | --- |
| Weekly EventBridge → Lambda wiring of `seo_baseline.py` + admin-observability tile | #28 (proposed) | Wiring a weekly job whose first 4 reports would baseline an *unimproved* state would pollute the trend line. Land the job AFTER #10 + #11 merge so the first published report is meaningful. |
| `scripts/check_budget_ceiling.py` umbrella green | #26 (proposed in Task #4) | The script doesn't exist yet — the umbrella CI guard cannot run until #26 ships it. |
| `scripts/check_canonical_delegation.py` repo-root umbrella shim | #25 (proposed in Task #4) | The canonical-delegation guard exists at `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py` but the repo-root shim documented in the task brief / runbook has not been created. |
| ≥ 70 % KV hit-ratio confirmation for materialization-eligible content_types | #10, #12, AND 14 d of post-merge traffic | Cannot be measured without the cache shape #10/#12 introduce AND two weeks of crawler hits. |
| `infra/architecture-matrix.json` shows zero MISSING rows in scope | #5 → #13 merged | Today the matrix has 2 MISSING rows (#572 semantic fingerprint, #574 prewarming engine) and ~14 PARTIAL rows tied to the #5–#13 chain. Marking them IMPLEMENTED before the work merges would be a lie; flipping happens in each task's own PR. |

## Reading order for the next agent

1. Read this file.
2. Confirm the upstream tasks (#5 → #13) are MERGED.
3. Run `python scripts/seo_baseline.py` against production with
   `GOOGLE_RR_API_KEY` set; archive the previous baseline JSON to
   `docs/seo/history/baseline-YYYY-MM-DD.json` first.
4. Run `pnpm --filter @workspace/syrabit test:e2e --grep "SEO/AEO ranking-page contract"` against production; expect the PerplexityBot warm-fetch HIT assertion to start passing once #12 lands.
5. Pick up Task #28 (weekly Lambda wiring + admin tile) once both pass cleanly two weeks running.
6. Walk `infra/architecture-matrix.json`; flip every row whose source files now exist to IMPLEMENTED, in the same PR as the upstream task.
