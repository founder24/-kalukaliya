# Local Chat RAG benchmark

This benchmark separates answer quality from source-card quality. The scorer reads local JSON files only: it does not call the chat API, a model provider, Wrangler bindings, or production services.

The repository starts with empty case and observation files. There is no approved curriculum gold set in the local fixtures, so the initial report is **not scored**. Do not fill the set with guessed source IDs or the existing three keyword-smoke questions. Each gold case needs curriculum and language review before it can count.

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
    "language_verified": true
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