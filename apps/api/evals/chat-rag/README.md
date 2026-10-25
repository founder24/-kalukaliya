# Local Chat RAG benchmark

This benchmark separates answer quality from source-card quality. The scorer reads local JSON files only: it does not call the chat API, a model provider, Wrangler bindings, or production services.

The repository starts with empty case and observation files. There is no approved curriculum gold set in the local fixtures, so the initial report is **not scored**. Do not fill the set with guessed source IDs or the existing three keyword-smoke questions. Each gold case needs curriculum and language review before it can count.

## Approval gate and curriculum inventory

`manifest.json` is a dated production-curriculum inventory prepared for approval, not an approved evaluation scope. It contains published chapter metadata and canonical IDs only; the inventory query did not read chat or conversation records. It excludes chapters whose chapter, subject, or hierarchy parent is unpublished. Canonical IDs use `chapter:<chapters.id>`; included RAG material is limited to chapter notes, Q&A, and published topic-card text that the current indexer can retrieve.

The read-only D1 refresh on 2026-10-25 confirms 35 board/class/stream/subject strata and 213 published chapters (114 AHSEC, 99 Degree). The product supports English (`en`) and Assamese (`as`) responses. Chapter notes/RAG fields are present in 204 chapters for English and 47 for Assamese; Q&A appears in 16/17 chapters respectively, and published topic-card text in 101/14. These source-family counts overlap and describe available material, not reviewer approval or gold-set coverage. File-only PYQ material is excluded because the current RAG indexer does not index it.

The 100+ case corpus remains blocked while `manifest.json` has `manifest_status: "pending_approval"` and reviewer assignments are empty. Keep `cases.json` and `observations.json` empty until the exact scope, source materials, and named reviewers have been approved.

Before populating either file:

1. Approve the exact curriculum materials, canonical source identifiers, board/class/subject scope, and evaluation languages in the manifest. Do not infer approval from material merely being present in the product.
2. Record named curriculum reviewers, English and Assamese language reviewers, and an independent output reviewer; then record the approval and the local or staging review environment.
3. Do not use production traffic or private student conversations.

## Add a reviewed case

Append an object to `cases.json`:

```json
{
  "id": "unique-case-id",
  "question": "Student's authored evaluation question",
  "language": "en",
  "expected_action": "answer",
  "expected_source_ids": ["chapter:<verified-id>"],
  "required_facts": ["A reviewer-verified answer point"],
  "scope": {
    "board": "AHSEC",
    "class": "11",
    "subject": "Physics"
  },
  "gold_review": {
    "curriculum_verified": true,
    "language_verified": true,
    "curriculum_reviewer_id": "assigned-reviewer",
    "language_reviewer_id": "assigned-reviewer"
  }
}
```

Use `expected_action: "clarify"` or `"abstain"` and empty `expected_source_ids`/`required_facts` when evidence is intentionally insufficient or the curriculum scope is ambiguous. For answerable cases, include every source needed to support the expected answer. Source IDs must match the exact published D1/Vectorize evidence identifiers.

## Record a local run and review it

Append one observation per case to `observations.json`. `retrieved_source_ids` must preserve retrieval rank; the scorer uses its first five IDs. `source_card_source_ids` are the sources actually shown to the student.

```json
{
  "case_id": "unique-case-id",
  "answer": "The answer generated in the local evaluation run.",
  "retrieved_source_ids": ["chapter:<verified-id>"],
  "source_card_source_ids": ["chapter:<verified-id>"],
  "review": {
    "reviewer_id": "independent-output-reviewer",
    "response": {
      "grounding": 5,
      "relevance": 5,
      "completeness": 5,
      "curriculum_scope": 5,
      "language_clarity": 5,
      "behavior": 5,
      "unsupported_major_claims": 0,
      "wrong_curriculum_scope": false
    },
    "source_card": {
      "passage_fidelity": 5,
      "hierarchy_accuracy": 5,
      "confidence_honesty": 5,
      "navigation": 5,
      "fabricated_source": false
    }
  }
}
```

Human ratings use **0–5**: 0 = failed or misleading, 3 = acceptable with a material issue, 5 = fully correct. `behavior` rates whether the model correctly answered, clarified, or abstained for that case. Mark major unsupported claims, wrong curriculum scope, and fabricated sources explicitly; any of these blocks 10/10 certification.

Use authored evaluation questions and local/staging outputs only. Do not copy private student conversations into this benchmark.

## Run and interpret

```sh
pnpm --filter syrabit-api eval:chat-rag
pnpm --filter syrabit-api test:chat-rag-eval
```

The scorer reports response quality, source-card quality, Recall@5, source-card source precision/recall, review coverage, and critical-failure case IDs. A score is provisional until **100 or more** reviewed cases and matching reviewed observations are present.

The 10/10 target is met only when the complete 100+ case set has response and source-card scores of 10.00, Recall@5 and source precision/recall are 1.00, and there are no critical failures. A smaller set may show pilot scores but can never certify the target.

The scorer reads the manifest by default, reports exact case denominators and the dated curriculum snapshot, and blocks certification while scope or reviewer approvals are incomplete. After scope approval, every expected source ID must exist in that manifest.

The current inventory and blocked run are recorded in `manifest.json` and `report-2026-10-25.json`. Re-run the scorer with `--out` after each reviewed local or staging evaluation to produce a fresh dated report.