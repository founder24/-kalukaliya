# Features Roadmap — Task #362

> **Scope:** Layer four feature improvements onto the #360 baseline:
> (1) deep recall via long-term-summary embedding gated by a
> recall-intent detector, (2) mixed-language English↔Assamese chat,
> (3) per-session multi-model fallback for stuck Mistral sessions,
> (4) friendlier moderation UX with `safe`/`default`/`challenge`
> modes.
>
> **Companion docs:**
> - `infra/per-cloud-feature-delegation.md` — v3 spec.
> - `infra/provider-priority-map.md` — per-feature provider table.
> - `infra/credit-burn-runbook.md` — flag mechanics (extended in §F
>   for the new feature flags).
> - `infra/perf-roadmap-361.md` — perf tier (cache + A/B + p99).
> - `infra/capacity-roadmap-363.md` — capacity tier.
>
> **Provider removals tracked in #347. v3 spec locked in #359. v3
> dispatch implementation tracked in #360. Perf tier in #361.
> Capacity tier in #363.**

**Status:** locked spec — 2026-05-04
**Owner:** founder@syrabit.ai

---

## §1 — Deep recall (long-term-summary embedding)

### §1.1 — Storage

The off-critical-path summarizer from #360 Step 1 already produces
`conversations.summary` after every N new turns. Extend it to also
emit a **summary embedding** stored in Pinecone:

```
Pinecone index:    syrabit-summaries
namespace:         user_id  (one namespace per user)
vector_id:         {session_id}:summary:{summary_version}
metadata:          {session_id, summary_version, summary_text,
                    last_updated_iso, source_turn_count}
embed model:       @cf/baai/bge-m3   (canonical embed_hotpath)
TTL / lifecycle:   evict summaries with no session activity > 90 days
                   (background sweeper, not on the live path)
```

For **short conversations** (< 8 turns total at the time of the
recall query), skip Pinecone and use **Redis** instead:

```
key:    summary:short:{session_id}
value:  msgpack({summary_text, last_updated_iso})
TTL:    24 h
```

This avoids the Pinecone round-trip when the entire history is
already cheap to scan.

### §1.2 — Recall-intent detector (two-tier)

The extra Pinecone query is **gated** so most turns pay zero extra
latency. Only recall-intent turns pay the ~30–60 ms summary-vector
lookup.

#### Tier 1 — regex/keyword fast-path (sub-millisecond, every turn)

Match against a **curated phrase list** loaded from Redis key
`recall_intent:tier1_phrases` (a JSON-encoded list, hot-reloadable
without a deploy):

```json
[
  "earlier you said",
  "earlier you mentioned",
  "you mentioned",
  "you told me",
  "go back to",
  "what did i ask",
  "what did i say",
  "previously",
  "last time",
  "remember when",
  "remember the",
  "the thing about",
  "the part about",
  "as you said",
  "you said earlier"
]
```

Plus an explicit **`@recall` user prefix** that always triggers
Tier 1 regardless of phrase match.

Tier 1 expected fire rate: **< 2% of turns** (it's targeted at
unambiguous recall phrasing).

#### Tier 2 — fast-mode LLM classifier fallback (~30–50 ms)

Fires **only when**:
- Tier 1 missed AND
- The prompt contains at least one **temporal/anaphoric token**
  from `recall_intent:tier2_tokens` (Redis list, hot-reloadable):

```json
["it", "that", "those", "then", "the same",
 "the one", "again", "still", "before"]
```

When both triggers fire, run a 1-token "yes/no" classification
against `@cf/meta-llama/Llama-3.2-3B-Instruct`:

```
System: Classify whether the user is asking the assistant to
recall something said earlier in this conversation. Reply with
exactly "yes" or "no". One token only.

User: <user_message>
```

Tier 2 expected fire rate: **5–10% of turns**. False-positive cost
is ~50 ms (one wasted Pinecone query); false-negative cost is a
missed recall (UX bug). Bias toward firing.

### §1.3 — Calibration loop

Sample **200 turns/week** uniformly at random from production chat
turns, hand-label them as recall-intent yes/no, and run
`scripts/i18n/recall_intent_classifier_eval.py` (see §1.5) against
the labeled set to compute Tier 1 + Tier 2 precision/recall.

**Targets:**
- ≥ 85% recall on labeled positives (combined Tier 1 + Tier 2).
- ≤ 15% false-positive rate (combined).

Weekly review writes any phrase-list / token-list edits directly to
the Redis hot keys via `redis-cli SET`. No code deploy is needed for
phrase tuning — that's the whole point of the Redis-backed lists.

### §1.4 — Live-path integration

Per-turn order (extends the v3 per-turn order from #360 Step 1):

```
1. Mongo history load + bge-m3 embed in parallel (existing)
2. Pinecone RAG retrieve over syllabus index (existing)
3. Recall-intent gate:
     if Tier 1 hit OR (Tier 2 trigger AND Tier 2 classifier == "yes"):
         Pinecone query over syrabit-summaries (namespace=user_id),
         top-k=3, append matched summary_text to prompt context
     else:
         skip
4. Input moderation (existing)
5. LLM dispatch (existing)
6. Output moderation (streaming-compatible per Latency Rule 11)
7. Mongo write (fire-and-forget per #360 Step 1)
```

The Tier 1 gate runs **before** any extra Pinecone work, so the
~99.5% of turns that don't match pay zero extra latency. The Tier 2
classifier and the summary-vector query both run **off** the
critical-path embed/retrieve already in flight when possible — but
the summary lookup result must be in hand before the LLM dispatch
in step 5.

### §1.5 — Helper script

`scripts/i18n/recall_intent_classifier_eval.py` reads a labeled
JSONL set (`label: "yes"|"no"`, `prompt: "..."`) plus the current
phrase + token lists, runs Tier 1 + the Tier 2 trigger logic
(without actually calling the LLM — operator can layer that on
separately), and prints precision/recall/F1 per tier and combined.
See script header for I/O contract.

---

## §2 — Mixed-language English↔Assamese chat

### §2.1 — Routing

The existing Indic chain in `provider-priority-map.md`
(`@cf/ai4bharat/indictrans2-en-indic-1b` primary, Sarvam-M secondary,
`@cf/meta/llama-3.1-8b-instruct` rollback) handles translation. This
task adds a new **`language_pair` routing rule**:

| `user_profile.language_pair` | Behavior |
|---|---|
| `{input: "en", output: "en"}` | default chain (no translation) |
| `{input: "as", output: "as"}` | default chain (Assamese in/out) |
| `{input: "en", output: "as"}` | Indic chain: answer in English, then `indictrans2-en-indic-1b` translates to Assamese |
| `{input: "as", output: "en"}` | Indic chain: `indictrans2-en-indic-1b` reverse-translates question to English, answer in English |

The `language_pair` field already exists on `user_profile` per #359
schema. No schema migration needed.

### §2.2 — UX measurement (three signals, all to App Insights)

**BLEU is the floor, not the ceiling.** Translation BLEU only proves
the surface text matches a reference; it doesn't prove the answer is
useful in the target language. Three independent signals are emitted
per mixed-language turn:

#### Signal 1 — Round-trip semantic preservation

```
1. User asks question in English (input).
2. Assistant answers in Assamese (output_as).
3. Round-trip translate output_as back to English (output_en_rt)
   via the same Indic chain.
4. Embed both:
     - the expected-answer-scope vector for the input question
       (precomputed in tests/i18n/mixed_language_eval.jsonl, see §2.3)
     - the round-tripped English answer (output_en_rt)
   using @cf/baai/bge-m3.
5. Compute cosine similarity.
```

**Targets:**
- p50 cosine ≥ **0.80** across the labeled validation set.
- Below **0.70** → route is broken, **fail the smoke**.

#### Signal 2 — Script-purity rate

Fraction of the assistant response in the *intended* output script:
- Assamese / Bengali script (Unicode block `U+0980–U+09FF`) for
  Assamese answers.
- Latin script (`U+0000–U+007F` ASCII letters) for English answers.

Computed via Unicode-block ratio (see
`scripts/i18n/script_purity_check.py`).

**Target:** ≥ **95%**. Anything lower means the model is leaking
the wrong script and the user sees garbled output.

#### Signal 3 — User-visible quality signal

Same per-turn 1–5 rating + follow-up-turn-within-60s rate from
#361 §3, sliced by `language_pair = (input_lang, output_lang)`.

**Target:** mixed-language slices must not regress more than:
- 0.3 rating points vs single-language baseline, OR
- 5 percentage points engagement vs single-language baseline.

### §2.3 — Validation set

Hand-labeled validation set lives at
`tests/i18n/mixed_language_eval.jsonl`. Each row:

```json
{
  "id": "mlx-0001",
  "input_lang": "en",
  "output_lang": "as",
  "input_text": "What is photosynthesis?",
  "expected_answer_scope_en": "Photosynthesis is the process by which plants convert light energy into chemical energy stored in glucose, using carbon dioxide and water and releasing oxygen.",
  "notes": "biology / class 11 / standard textbook coverage"
}
```

**Sizing:** target **200 rows** at maturity (10 subjects × 10 topics
× 2 directions). Initial seed of **15 rows** ships with this task
covering biology, physics, chemistry, history, and civics — enough
to exercise the harness; expansion is a calibration follow-on.

CI runs the eval harness (`scripts/i18n/mixed_language_eval.py`) on
every change to:
- `infra/provider-priority-map.md` (Indic chain rows)
- the mixed-language router code in `_dispatch_llm_for_feature`
- the `tests/i18n/mixed_language_eval.jsonl` fixture itself

### §2.4 — Helper scripts

- `scripts/i18n/script_purity_check.py` — compute Unicode-block
  ratio for an input text against an intended script. Offline,
  no network. CI smoke + can be run on a single answer string for
  ad-hoc debugging.
- `scripts/i18n/mixed_language_eval.py` — orchestrator: reads the
  validation set, calls a pluggable backend (translator + embedder
  endpoints) provided via env vars, computes Signals 1–3, prints a
  per-row + aggregate report, exits non-zero on threshold breach.
  CI executes it in **dry-run mode by default** (no network — uses
  pre-baked golden translations from the fixture); operators run
  the full network mode manually before promoting an Indic-chain
  config change.

---

## §3 — Per-session multi-model fallback

### §3.1 — Distinct from global `chat:fallback`

The global `chat:fallback` Redis hot-flag from #360 Step 7 swaps
**all sessions** to Azure GPT-4.1-mini. This task adds a **per-session
sticky swap** that affects **only one session** without touching the
global flag.

The dispatcher reads `session:fallback:{session_id}` **before**
consulting the global `chat:fallback`. A per-session swap wins; the
global flag remains the broader-degradation signal.

### §3.2 — Trip thresholds (defaults; tunable via runbook)

```
K           = 3 consecutive turns
TTFB_MULT   = 3.0   (multiplied against #360's p50 envelope of 600–900 ms)
TTFB_TRIP   = 2400 ms  (3.0 × 800 ms midpoint)
WINDOW_TTL  = 24 h     (per-session TTFB rolling state)
SWAP_TTL    = 2 h      (default session lifetime; sticky for that window)
```

Per-session state:

```
key:    session:ttfb:{session_id}     (Redis hash)
fields: last_3_ttfb_ms (capped list of 3 most-recent TTFB values),
        swapped_at_iso (set when trip fires, else missing)
TTL:    24 h after last write

key:    session:fallback:{session_id} (Redis string)
value:  "azure_gpt41mini"
TTL:    SWAP_TTL (default 2 h)
```

### §3.3 — Recovery rule

**Sticky for the session.** No auto-revert mid-session — flapping
between models within one session is worse UX than a stable
fallback. The swap clears naturally when the session ends (TTL
expiry) or when the user starts a new session.

If the operator needs to force-revert (e.g., after upstream Mistral
recovers and they want to test a long-running session): manual
`redis-cli DEL session:fallback:{session_id}`.

### §3.4 — Anti-thundering-herd guard

If **> 5% of active sessions** trip the per-session swap within a
**5-minute window**, this is *not* a per-session problem — it's
broad upstream degradation, and per-session fallback would just
shift load onto Azure GPT-4.1-mini without addressing the root
cause.

When the guard fires:
- Post a high-priority alert to the on-call channel.
- Set Redis flag `session:fallback:disabled = 1` (TTL 30 min,
  read by the dispatcher every turn) — **stops accepting new
  per-session swaps** until on-call clears it.
- Existing per-session swaps stay in place (don't yank the rug
  out from under sessions already mid-conversation).
- On-call decides whether to flip the global `chat:fallback=1`
  instead.

The 5%/5min check runs as a background job (1-minute tick) reading
the count of `session:fallback:*` keys created in the last 5 minutes
against a rolling estimate of active sessions (from `session:ttfb:*`
key count with non-empty `last_3_ttfb_ms`).

### §3.5 — Helper script

`scripts/perf/session_fallback_thundering_herd_check.py` — operator
runbook tool. Counts current per-session swap rate, prints the
ratio, exits 1 if over the 5%/5min threshold (so it's wireable to
a periodic alert if the in-process background job is unavailable).

---

## §4 — Friendlier moderation UX + `safe`/`default`/`challenge` modes

### §4.1 — Per-user setting

`user_profile.moderation_mode: "safe" | "default" | "challenge"`
(field already exists per #359 schema, see
`infra/per-cloud-feature-delegation.md` `user_profile` schema
section). Defaults to `default`.

Reads on every turn (cached on the FastAPI request scope, **not**
per-turn Redis — the value is already in the `user_profile`
document loaded by step 1 of the v3 per-turn order).

Mode changes are logged with `{timestamp, user_id, previous, new}`
to the audit collection. **Audit log retention: 90 days** for
compliance.

### §4.2 — Threshold mapping

Llama Guard returns binary `safe`/`unsafe`. Azure AI Content Safety
returns severity 0–7 across `hate`, `sexual`, `self_harm`, `violence`.
Mode adjusts the **block** threshold per category; the categories
themselves come from the providers and `exam_model_paper`-only
categories from #360 Step 4 are **non-tunable** and stay fail-closed
regardless of mode.

| Category | `safe` | `default` | `challenge` |
|---|---|---|---|
| Llama Guard `unsafe` (binary) | block | block | block |
| Azure CS `hate` | block at sev ≥ **2** | block at sev ≥ **4** | block at sev ≥ **6** |
| Azure CS `sexual` | block at sev ≥ **2** | block at sev ≥ **4** | block at sev ≥ **6** |
| Azure CS `violence` | block at sev ≥ **2** | block at sev ≥ **4** | block at sev ≥ **6** |
| Azure CS `self_harm` | block at sev ≥ **2** | block at sev ≥ **4** | **block at sev ≥ 2** *(non-negotiable safety floor)* |
| `exam_model_paper` categories | fail-closed | fail-closed | fail-closed |

**Mode descriptions:**
- **`safe`** — stricter than default. Suitable for school accounts,
  parent-managed profiles, default for new accounts under 16 if age
  is collected.
- **`default`** — current #360 behavior, unchanged. Documented so
  changing the mode is a documented config flip, not a stealth
  behavior change.
- **`challenge`** — looser, opt-in only. Intended for adult
  researchers / advanced exam prep where stricter modes over-block
  legitimate medical, legal, or historical content. `self_harm`
  stays at the strictest threshold regardless.

### §4.3 — Hard floors (non-negotiable, codified as constants)

These block in **every** mode, including `challenge`:

```python
# scripts/i18n/moderation_floors.py (illustrative; actual constants
# live in the moderation module of the FastAPI backend)
HARD_FLOORS = {
    "csam":                       True,  # any signal blocks
    "self_harm_with_intent":      True,  # any credible signal blocks
    "exam_paper_leakage":         True,  # any signal blocks
    "self_harm_severity_floor":   2,     # Azure CS sev ≥ 2 blocks
                                         # in challenge mode too
}
```

A **unit test** asserts that flipping `moderation_mode` to
`challenge` does **not** unblock the test fixtures in those
categories. The test fixture file lives at
`tests/moderation/hard_floor_fixtures.jsonl` (out of scope to ship
the fixture content here — operator-provided, but the test harness
+ assertion contract is part of this spec).

### §4.4 — Friendlier veto messages

When a turn is blocked, the response surfaced to the user includes:
1. A friendly reason in plain language (one sentence).
2. The triggered category (when the moderation provider returned
   one — Azure CS does, Llama Guard's binary `unsafe` doesn't).
3. A **rephrase hint** — a short suggestion of how to get a useful
   answer for legitimate intents in the same category.

Example:

```
Original block message (current):
  "This answer was blocked for safety reasons."

New (mode = default, category = violence):
  "I can't answer that one — the topic touched on violence in a
  way I'm not able to help with directly. If you're studying this
  for history or current affairs, try asking about the historical
  context, the timeline of events, or the policy response instead."
```

Rephrase hints are **template-driven**, not LLM-generated, to keep
the block path latency negligible. Templates live in a static dict
keyed by `(category, mode)`; new templates can be added without a
code deploy by editing the Redis key `moderation:rephrase_hints` (a
JSON object). Default templates ship with the spec.

---

## §5 — Smoke matrix

10 rows. Each independently runnable.

| # | Scenario | Expected |
|---|---|---|
| 1 | State a unique fact in turn 1; ask 50 unrelated questions; ask "what did I tell you about X earlier?" in turn 52 | Tier 1 hits → summary Pinecone query returns the original fact → assistant cites the value from turn 1 |
| 2 | Turn 4 contains "go back to that thing" | Tier 1 hits on "go back to" → summary lookup runs |
| 3 | Turn 4 contains "tell me about it again" | Tier 1 misses; Tier 2 triggers on "it"+"again"; classifier returns yes → summary lookup runs |
| 4 | Plain question with no recall phrasing | Tier 1 misses; Tier 2 trigger missing → no summary lookup; turn latency identical to baseline |
| 5 | English-Q-Assamese-A turn from `tests/i18n/mixed_language_eval.jsonl` | Round-trip cosine ≥ 0.80; script-purity ≥ 95% |
| 6 | Synthetic load injects 3 consecutive 3.0s TTFB turns into one session | After turn 3 of the burst: `session:fallback:{id}` set; turn 4 routes to Azure GPT-4.1-mini; global `chat:fallback` unchanged |
| 7 | Same session as row 6, after SWAP_TTL expiry | `session:fallback:{id}` cleared by Redis TTL; subsequent turns route to default chain again |
| 8 | Synthetic load: trip per-session swap on > 5% of active sessions within 5 min | `session:fallback:disabled = 1` set; new swaps refused; high-priority alert posted; existing swaps remain |
| 9 | User on `safe` mode requests content that scores Azure CS sev = 3 on `hate` | Blocked. User on `default` mode same query: served. User on `challenge` mode same query: served |
| 10 | User on `challenge` mode requests content that scores Azure CS sev = 4 on `self_harm` | **Blocked** (the non-negotiable floor). Unit test in §4.3 asserts. |

---

## §6 — Cross-references to companion docs

### §A — `infra/provider-priority-map.md`

Add a new feature key row: `recall_summary_vector_query`:

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | pinecone | (n/a, vector store) | aws-us-east-1 | gated by recall-intent detector; namespace = user_id |
| primary | redis | (n/a, kv) | upstash-eu-west-1 | short-conversation fast path; < 8 turns |

Indic chain rows already exist; no changes for §2.

### §B — `infra/per-cloud-feature-delegation.md`

New **Latency Rule 14** appended to the §16 hot-path rules block:

> **14. Recall-intent gate is mandatory before any summary-vector
> Pinecone query.** The summary-embedding lookup adds ~30–60 ms;
> running it on every turn would add that to the p50 envelope. The
> two-tier detector keeps the unconditional-cost addition at zero
> on the ~90% of turns that don't pass either tier, ~50 ms on the
> ~5–10% of turns that hit Tier 2's classifier (without then
> querying Pinecone), and the full ~30–60 ms only on turns where
> recall-intent is actually detected. Per-turn synchronous
> summary-vector queries on every chat turn are forbidden.

### §C — `infra/credit-burn-runbook.md`

Add seven flag rows to the per-flag operations table (see §F below
for the exact rows; the runbook is the authoritative inline
reference so on-call doesn't need to cross-read this doc).

---

## §F — Per-flag operations rows (inlined into runbook)

| flag | read_path | write_path | default | who | propagation | rollback |
|---|---|---|---|---|---|---|
| `recall_intent:tier1_phrases` | Redis JSON list, read at process start + on every turn | on-call (manual edit via `redis-cli SET`) | seed list per §1.2 | on-call | < 5 ms | restore previous JSON via `redis-cli SET recall_intent:tier1_phrases '<previous-json>'` |
| `recall_intent:tier2_tokens` | Redis JSON list, read at process start + on every turn | on-call | seed list per §1.2 | on-call | < 5 ms | restore previous JSON |
| `session:fallback:{id}` | Redis string, read every turn (per-session) | dispatcher (auto on K-turn TTFB trip) | (unset; absence = use global chain) | dispatcher / on-call | < 5 ms | `redis-cli DEL session:fallback:{id}` |
| `session:fallback:disabled` | Redis string, read every turn | anti-thundering-herd job (auto on > 5%/5min) | `0` | background job / on-call | < 5 ms | `redis-cli DEL session:fallback:disabled` |
| `session:ttfb:{id}` | Redis hash, read+write every turn (per-session) | dispatcher | (unset on session start) | dispatcher | < 5 ms | `redis-cli DEL session:ttfb:{id}` |
| `moderation:rephrase_hints` | Redis JSON object, read at process start (refreshed every 5 min) | on-call | seeded per §4.4 | on-call | up to 5 min (acceptable — non-safety-critical) | restore previous JSON |
| `moderation:hard_floors_test_mode` | env var read at process start | infra owner | unset (= disabled) | infra owner | next deploy | redeploy without the env var |

---

## §F.2 — Decision log

| date | decision | rationale |
|---|---|---|
| 2026-05-04 | Two-tier recall-intent detector (regex + LLM classifier) instead of single-tier | Tier 1 alone misses anaphoric phrasing; LLM-only is too expensive on every turn (~50 ms × 100% of turns). Two-tier amortizes cost. |
| 2026-05-04 | Per-session sticky fallback (no auto-revert) | Flapping mid-session is worse UX than a stable fallback. Operator can force-revert via `redis-cli DEL`. |
| 2026-05-04 | 5%/5min anti-thundering-herd cutoff | Above this rate, the failure is global (Mistral degraded broadly), not per-session. Per-session swaps would just shift load onto Azure GPT-4.1-mini's quota. The global `chat:fallback` is the right tool above this threshold. |
| 2026-05-04 | `self_harm` stays at sev ≥ 2 even in `challenge` mode | Hard safety floor. Codified as constant, not config. Unit-tested. |
| 2026-05-04 | Rephrase hints are templates, not LLM-generated | Block-path latency must stay negligible. Templates cover ~95% of legitimate-rephrase cases; the long tail is acceptable to handle as a `default` block-message. |

---

End of `infra/features-roadmap-362.md`.
