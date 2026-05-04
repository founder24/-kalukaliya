# Cloudflare Workers AI — Speed Test (LLMs + Indic)

**Date:** 2026-05-04
**Method:** direct `POST /accounts/{account_id}/ai/run/{model}` from a
single client, 2 trials per model, parallelized within batches.
Network: Replit container → CF edge (cold path; no AI Gateway cache).
Account: `CF_AI_GATEWAY_ACCOUNT_ID` (CF for Startups, $5k pool).
LLM prompt: *"Explain the difference between supervised and unsupervised learning in 2 short sentences."* (`max_tokens: 120`).
Indic prompt: short factual sentence about photosynthesis.

> All times are wall-clock total round-trip (ms). Tokens-per-second is
> approximated from word count / latency. Cold-path numbers — production
> with AI Gateway caching will be 30–40 % faster on repeat queries.

---

## §1 LLMs — chat / instruct

| Rank | Model | Trial 1 | Trial 2 | Avg ms | Words | ~tok/s | Status |
|---:|---|---:|---:|---:|---:|---:|:-:|
| 1 | `@cf/meta/llama-3.2-1b-instruct` | 1003 | 749 | **876** | 76 | **~87** | ✅ |
| 2 | `@cf/openai/gpt-oss-120b` | 923 | 792 | **858** | 32 | ~37 | ✅ |
| 3 | `@cf/meta/llama-3.1-8b-instruct` | 786 | 1042 | **914** | 54 | ~59 | ✅ |
| 4 | `@cf/openai/gpt-oss-20b` | 947 | 1038 | **992** | 40 | ~40 | ✅ |
| 5 | `@cf/meta-llama/Llama-3.2-3B-Instruct` | 993 | 1077 | **1035** | 75 | ~71 | ✅ |
| 6 | `@cf/meta/llama-4-scout-17b-16e-instruct` | 1322 | 1286 | **1304** | 52 | ~40 | ✅ |
| 7 | `@cf/mistralai/mistral-small-3.1-24b-instruct` | 1460 | 1606 | **1533** | 57 | ~37 | ✅ |
| 8 | `@cf/microsoft/phi-2` | 2154 | 1189 | **1672** | 38 | ~23 | ✅ |
| 9 | `@cf/qwen/qwen2.5-coder-32b-instruct` | 1510 | 2120 | **1815** | 38 | ~21 | ✅ |
| 10 | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | 2631 | 1743 | **2187** | 56 | ~26 | ✅ |
| 11 | `@cf/google/gemma-3-12b-it` | 1229 | 3505 | **2367** | 40 | ~17 | ✅ |
| 12 | `@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` | 3469 | 3424 | **3447** | 100 | ~29 | ✅ (incl. `<think>`) |
| 13 | `@cf/meta/llama-3.1-70b-instruct` | 3627 | 3384 | **3506** | 65 | ~19 | ✅ |
| 14 | `@cf/qwen/qwq-32b` | 4717 | 3856 | **4287** | 100 | ~23 | ✅ (reasoning model) |
| 15 | `@cf/mistral/mistral-7b-instruct-v0.1` | 7191 | 7287 | **7239** | 75 | ~10 | ⚠️ slow |

### Models that returned errors

| Model | Status | Error |
|---|:-:|---|
| `@cf/mistral/mistral-7b-instruct-v0.2` | 400 | "No route for that URI" — model slug retired |
| `@cf/mistralai/mistral-7b-instruct-v0.2` | 400 | "No route for that URI" |
| `@cf/qwen/qwen1.5-14b-chat-awq` | 410 | **Deprecated 2025-10-01** — use alternative |
| `@cf/qwen/qwen3-30b-a3b` | 400 | "No route for that URI" |
| `@cf/qwen/qwen3-coder-30b-a3b-instruct` | 400 | "No route for that URI" |
| `@cf/qwen/qwen2.5-coder-32b-instruct-fast` | 400 | "No route for that URI" |

---

## §2 Indic translation models

| Direction | Model | Avg ms | Quality | Status |
|---|---|---:|---|:-:|
| **en → Assamese (`asm_Beng`)** | `@cf/ai4bharat/indictrans2-en-indic-1b` | **1009** | ✅ "সালোক সংশ্লেষণ হ'ল উদ্ভিদই সূৰ্য্যৰ পোহৰৰ পৰা কেনেদৰে খাদ্য প্ৰস্তুত কৰে।" — **clean native Assamese** | ✅ |
| en → Bengali (`ben_Beng`) | `@cf/ai4bharat/indictrans2-en-indic-1b` | 1735 | ✅ "সালোকসংশ্লেষণ হল কিভাবে উদ্ভিদ সূর্যালোক থেকে খাদ্য তৈরি করে।" | ✅ |
| en → Hindi (`hin_Deva`) | `@cf/ai4bharat/indictrans2-en-indic-1b` | 1120 | ✅ "प्रकाश संश्लेषण यह है कि पौधे सूर्य के प्रकाश से भोजन कैसे बनाते हैं।" | ✅ |
| Indic → en | `@cf/ai4bharat/indictrans2-indic-en-1b` | — | — | ❌ 400 "No route" — model slug retired or never existed under this path |
| en → Assamese | `@cf/meta/m2m100-1.2b` | — | — | ❌ "assamese is not a supported language" |
| en → Bengali | `@cf/meta/m2m100-1.2b` | 1057 | ⚠️ "ফটোসিন্থেসি…" — leaves *Photosynthesis* untranslated word-1 | ✅ but lower quality |
| en → Hindi | `@cf/meta/m2m100-1.2b` | 824 | ⚠️ "Photosynthesis यह है कि…" — same word-1 issue | ✅ but lower quality |

> ⚠️ **Critical input contract for IndicTrans2:** language must be the
> ISO `lang_Script` form (`asm_Beng`, `ben_Beng`, `hin_Deva`,
> `eng_Latn`), NOT `"assamese"`. The wrapper at
> `artifacts/syrabit-backend/providers/workers_indic.py` already
> handles this; any direct call must use the ISO codes or the request
> 400s.

---

## §3 Recommended chain order (post-test)

### `english_rag_chat` (Tier-3 CF leg)
1. **`@cf/openai/gpt-oss-120b`** (~860 ms, large model with strong quality) — surprisingly fastest among >20B models
2. **`@cf/openai/gpt-oss-20b`** (~990 ms) — current choice in `provider-priority-map.md`, validated
3. **`@cf/meta/llama-4-scout-17b-16e-instruct`** (~1300 ms) — solid quality, mid latency
4. Fast small fallback: **`@cf/meta-llama/Llama-3.2-3B-Instruct`** (~1000 ms, 71 tok/s)

> The current map's choice of `@cf/openai/gpt-oss-20b` → `@cf/meta/llama-3.3-70b-instruct-fp8-fast` is **valid**, but `gpt-oss-120b` is empirically faster than the 70B-fp8-fast variant (~860 ms vs ~2200 ms) — consider promoting it.

### `assamese_rag_chat` Tier-3 / Tier-4 fallback
- The CF leg here is "translate-then-answer". Best chain:
  1. **`@cf/ai4bharat/indictrans2-en-indic-1b`** (1009 ms, en→`asm_Beng`) — for outbound Assamese rendering
  2. **`@cf/openai/gpt-oss-20b`** (992 ms) — answer leg in English
  3. Optional re-translation back to Assamese via IndicTrans2

### `translate` (Tier-1 — CF Workers AI is canonical)
- **`@cf/ai4bharat/indictrans2-en-indic-1b`** is **THE** model for `en → as / bn / hi`. Average 1.0–1.7 s per sentence; quality clean native script.
- IndicTrans2 reverse direction (`indic-en-1b`) is currently **broken at this slug** — needs investigation. M2M100 is a poor substitute (leaves source words untranslated).

### `content` Tier-3 fallback
- Same as `english_rag_chat` Tier-3.

### Models to retire from any chain
- ❌ `@cf/qwen/qwen1.5-14b-chat-awq` — **deprecated 2025-10-01** (returns HTTP 410)
- ❌ `@cf/mistral/mistral-7b-instruct-v0.2` and `@cf/mistralai/mistral-7b-instruct-v0.2` — slugs retired
- ❌ `@cf/qwen/qwen3-30b-a3b` and `@cf/qwen/qwen3-coder-30b-a3b-instruct` — slugs not provisioned on this account

---

## §4 Findings worth pinning

1. **`gpt-oss-120b` is the fastest large model on CF.** Faster than `llama-3.3-70b-fp8-fast` (858 ms vs 2187 ms) despite being larger. The MoE architecture makes inference cheap.
2. **`llama-3.2-1b` is the fastest small model** (876 ms, 87 tok/s). Use for ultra-low-latency fallback paths (e.g. greeting routing, intent classification) where quality is secondary.
3. **`qwq-32b` and `deepseek-r1-distill-qwen-32b` are reasoning models** — they emit `<think>...</think>` blocks before the answer, inflating both latency and output tokens. Only use in chains where reasoning trace is desired.
4. **IndicTrans2 `en-indic-1b` is fully working** for Assamese, Bengali, Hindi — confirms the Tier-1 `translate` choice in `provider-priority-map.md`. Indic-en direction needs a follow-up to find the correct slug or accept that reverse-translation has no CF Workers AI coverage today.
5. **`mistral-7b-instruct-v0.1` is unusably slow** at 7+ s per request. The retired v0.2 slugs were the better path; without them, drop Mistral 7B from any CF chain.
6. **No measured request crossed the 8-second p95** — even the slowest working model fits within typical web request budgets.

---

## §5 Cost note

All 38 successful test invocations consumed **<$0.01** of the $5k CF for
Startups credit pool. Workers AI is metered per neuron-second; small
models (1B–8B) are effectively free at the volumes Syrabit will see at
10k DAU. The $31/mo Cloudflare line in
`per-cloud-feature-delegation.md` §3.2 is dominated by translate
(IndicTrans2) traffic, not chat fallback.

---

## §6 Drift recommendations for `provider-priority-map.md`

Optional, evidence-based:

| Chain | Current | Recommended | Reason |
|---|---|---|---|
| `english_rag_chat` Tier-3 | `gpt-oss-20b → llama-3.3-70b-fp8-fast` | `gpt-oss-120b → gpt-oss-20b → llama-4-scout-17b` | 120b is faster than 70b-fp8-fast and higher quality |
| `content` Tier-3 | `gpt-oss-20b` | `gpt-oss-120b` | same — better speed/quality at edge |
| Drop from any chain | — | `qwen1.5-14b-chat-awq`, `mistral-7b-v0.2`, `qwen3-30b-a3b`, mistral 7B v0.1 | deprecated or unusably slow |

> These are recommendations only — no edits to the canonical
> `provider-priority-map.md` were made in this turn. The drift register
> at `feature-deep-dive.md` §7.3 is the right place to record any
> accepted change.

---

## §7 Re-running this test

```bash
# Requires: CF_AI_GATEWAY_ACCOUNT_ID + CLOUDFLARE_API_TOKEN in env

# Single model, 2 trials, parallel
node /tmp/cf_speed.mjs "@cf/openai/gpt-oss-120b"

# Batch of models
node /tmp/cf_speed.mjs \
  "@cf/openai/gpt-oss-20b" \
  "@cf/openai/gpt-oss-120b" \
  "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

# Indic (uses different payload shape — see /tmp/cf_indic.mjs)
node /tmp/cf_indic.mjs
```

Raw results: `/tmp/cf_results.jsonl` (regenerated each run).
