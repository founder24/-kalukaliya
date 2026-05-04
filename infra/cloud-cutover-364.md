# Cloud cutover for the Task #347 provider removals — Task #364

> **Status:** runbook v1 — 2026-05-04
> **Owner:** infra@syrabit.ai
> **On-call channel:** `#syrabit-oncall` (Slack)
> **Companion docs:**
> - `artifacts/syrabit/docs/infra/providers-task-347-decommission.md` — what was
>   removed in #347 and why (replacement vendors, code-level changes).
> - `artifacts/syrabit/docs/infra/aca-cutover.md` — Azure Container Apps
>   day-one bootstrap + per-deploy revision swap (the canonical ACA runbook).
> - `infra/credit-burn-runbook.md` — flag mechanics + meters for the
>   replacement chain (SendGrid, Workers AI fallbacks, Razorpay).
> - `infra/per-cloud-feature-delegation.md` — chain priority + Latency Rules.
> - `infra/provider-priority-map.md` — provider tiers per feature.
>
> #347 removed the **code** for OpenAI / Anthropic / xAI-Grok / Bedrock /
> Stripe / Resend; this task removes the **cloud-side** state those
> imports referenced (secrets, KV entries, CF Workers, IAM bindings)
> and warms SendGrid up to 100 % production traffic.
>
> **Like every other infra-tier task on this project, this doc is the
> deliverable.** The actual `az` / `wrangler` / `gh secret` / `dig`
> invocations below run operator-side; this repo ships only the runbook
> + the three verification scripts under `scripts/infra/`.

---

## §1 — Pre-flight gates

Do **not** start the cutover until **all** of these are green. Each gate
has a one-line verification command — paste the output into the cutover
ticket.

| # | Gate | Verification | Expected |
|---|------|---|---|
| 1 | Task #347 merged to `main` | `git log --oneline main \| rg "Task #347"` | one or more matches |
| 2 | Lint guardian active | `python3 artifacts/syrabit-backend/scripts/check_dead_providers.py` | exit 0 on `main` |
| 3 | SendGrid v3 key minted with **Mail Send (Full Access)** scope only | SendGrid dashboard → Settings → API Keys | one key, scope = Mail Send only |
| 4 | SendGrid sending domain `em.syrabit.ai` shows **Verified** for the SPF + 3 DKIM CNAMEs SendGrid auto-issues | SendGrid → Settings → Sender Authentication | green check on all 4 rows |
| 5 | Razorpay live keys present in Azure Key Vault (`RAZORPAY-KEY-ID`, `RAZORPAY-KEY-SECRET`) | `az keyvault secret list --vault-name syrabit-prod-kv -o tsv \| rg RAZORPAY` | both rows present |
| 6 | DO origin still serves `api.syrabit.ai` traffic (rollback floor for the first 14 days, per `aca-cutover.md` §2) | `curl -sI https://api.syrabit.ai/api/health \| rg -i 'origin'` | DO origin tag |
| 7 | Two operators on call: one driver, one observer (Sentry + Axiom + Cloudflare Analytics) | Slack #infra-deploys | "cutover on" pinned |
| 8 | `check_email_dns_alignment.py` exits 0 (operator must supply the link-branding CNAME prefix shown in SendGrid → Sender Authentication for this account; without it the script WARNs but does not fail) | `python3 scripts/infra/check_email_dns_alignment.py --domain em.syrabit.ai --link-branding-cname em1234.em.syrabit.ai` (replace `em1234` with the actual prefix) | exit 0 + SPF/DKIM/DMARC/link-branding all OK |

If any gate fails, **stop**. Fix the gate, re-run all gates. Do not skip.

---

## §2 — Phase A: Azure Container Apps revision swap

The new image (post-#347) reads `SENDGRID_API_KEY` instead of
`RESEND_API_KEY`, removes the Stripe SDK init at boot, and removes the
`workers/bedrock-proxy` dependency. The image must be rolled into ACA
**before** the legacy secrets are deleted; otherwise the prior revision
crashes when its Key Vault `secretRef` resolves to nothing.

### A.1 Build + push

The CI workflow `azure-container-apps-deploy.yml` does this end-to-end.
From GitHub: **Actions → Azure Container Apps deploy (manual)**, set
`app=syrabit-backend`, `mode=deploy`, run.

The workflow:

1. Builds `artifacts/syrabit-backend/Dockerfile` into ACR
   (`syrabitacr.azurecr.io/syrabit/backend:<git-sha>`).
2. Calls `az containerapp update --image …` so ACA rolls in a new
   revision in **single-revision mode** — the new revision receives
   100 % of traffic only after its readiness probe passes.
3. Probes `https://api.syrabit.ai/health` until it returns 200.

### A.2 Add the SendGrid secret to Key Vault before the swap

Operator runs (one-time, before A.1 above if not already present):

```bash
# Add SendGrid key to Key Vault (operator pastes the value at the prompt)
read -rs -p "SendGrid v3 key (Mail Send Full Access): " SG_KEY
az keyvault secret set \
  --vault-name syrabit-prod-kv \
  --name SENDGRID-API-KEY \
  --value "$SG_KEY"
unset SG_KEY
```

The Container App's managed identity already has `Key Vault Secrets
User` per `aca-cutover.md` §0.5; no additional RBAC change needed.

The Bicep template (`infra/azure/aca-syrabit-backend.bicep`) must list
`SENDGRID-API-KEY` in the `secrets` block with a `keyVaultUrl`
reference and surface it to the container as the `SENDGRID_API_KEY`
env var. Confirm with:

```bash
az containerapp show -g syrabit-prod -n syrabit-backend \
  --query "properties.template.containers[0].env[?name=='SENDGRID_API_KEY']" -o json
```

Expected output: a single object whose `secretRef` is `sendgrid-api-key`.

### A.3 Warm-swap verification

Once A.1 reports success:

```bash
# Confirm the new revision is healthy and is taking 100 % of traffic
az containerapp revision list -g syrabit-prod -n syrabit-backend -o table

# The newest row's Active=True, TrafficWeight=100, Healthy=True.
# Probe the live origin:
curl -fsS https://api.syrabit.ai/api/health | jq .
# Expect: {"status":"ok", "git_sha":"<the new sha>", ...}

# Confirm the SendGrid integration boots without an error log
az containerapp logs show -g syrabit-prod -n syrabit-backend \
  --type system --follow false --tail 50 \
  | rg -i "sendgrid|SENDGRID_API_KEY"
# Expect: no "no_key" / "missing" lines; the Email/SendGrid logger should
# either be silent or emit "[Email/SendGrid] ready".
```

If the new revision is unhealthy, **roll back per `aca-cutover.md` §2**
before proceeding. **Do not** delete any legacy secrets while a
revision swap is in flight or rolled back.

---

## §3 — Phase B: SendGrid IP warmup (1 % → 10 % → 50 % → 100 %)

> **Why warm up at all?** Even though `em.syrabit.ai` was already SES-
> validated for the legacy SES path, this is SendGrid's first time
> sending under that domain. SendGrid's IP reputation system penalises
> any sudden volume spike from a new sending identity; a phased ramp
> keeps bounces below the **5 %** threshold and complaints below
> **0.1 %**, the two SendGrid auto-suspension floors.

> **Mechanism — use SendGrid's built-in IP Warmup, not a custom
> traffic-split.** The earlier draft of this runbook proposed a
> `SENDGRID_TRAFFIC_PCT` Worker var to split traffic between SendGrid
> and the SES fallback; that was rejected during review because it
> requires runtime code in `workers/email-worker/src/index.ts` (and a
> matching `email_templates.py` change) that doesn't exist on `main`
> and would have to be invented in this task. SendGrid ships an IP
> Warmup feature (Settings → IP Addresses → Warmup) that throttles
> outbound itself per the same 30-day RFC-aligned schedule we'd
> otherwise have to reimplement; it is the canonical mechanism and
> requires zero application-code changes. Use it.

### B.1 Enable SendGrid IP Warmup

Operator (one-time per dedicated IP):

1. SendGrid dashboard → **Settings → IP Addresses**.
2. For the dedicated IP assigned to the SendGrid account, toggle
   **Warmup → On**.
3. Confirm the warmup schedule shown is the standard 30-day ramp
   (day 1 cap = 50 messages; day 7 = 1 600; day 14 = 12 000; day 30 =
   unlimited). If a shorter schedule is offered, **decline** and
   stick with the 30-day default — the shorter schedules trade
   reputation risk for time and we have no business reason to.
4. SendGrid will return HTTP 421 ("try later") for any send that
   exceeds the day's cap; `email_templates.py` already retries 421
   into the SES tier via the existing `email-fallback` SQS queue, so
   no application change is needed.

### B.2 Per-day soak gates

Run the warmup monitor at each soak checkpoint to confirm the
SendGrid-served traffic is healthy. The script is read-only against
SendGrid; it never sends an email.

| Day | Min soak before checkpoint | Threshold gates |
|---|---|---|
| Day 1  | 24 h | bounce rate < 2 %, spam rate < 0.05 %, ≥ 25 messages sent, zero `permission_denied` from SendGrid |
| Day 3  | 48 h | same thresholds; ≥ 75 messages |
| Day 7  | 96 h | same thresholds; ≥ 200 messages |
| Day 14 | 168 h | same thresholds; ≥ 500 messages |
| Day 30 | 360 h | thresholds hold; SES fallback share < 1 %  |

Per-checkpoint command (the `--mode messages` path uses the SendGrid
Activity Feed API for true minute-precision filtering; the
`--mode stats` fallback queries `/v3/stats` for accounts without the
Email Activity History add-on, with day-level granularity):

```bash
SENDGRID_API_KEY=$(az keyvault secret show \
  --vault-name syrabit-prod-kv --name SENDGRID-API-KEY \
  --query value -o tsv) \
python3 scripts/infra/sendgrid_warmup_monitor.py \
  --mode messages \
  --window-minutes 1440 \
  --bounce-max-pct 2.0 \
  --spam-max-pct 0.05 \
  --min-messages 25
```

Exit codes: `0` = OK, advance to the next checkpoint; `1` = threshold
breach, **pause the warmup** (toggle **Settings → IP Addresses →
Warmup → Off** so SendGrid stops adding daily quota until the
breach is investigated); `2` = harness failure (network /
credentials / Activity History add-on missing — re-run with
`--mode stats`).

### B.3 Rollback within Phase B

Pausing the warmup (Settings → IP Addresses → Warmup → Off) freezes
the daily cap at the current day's number; new sends in excess of
that cap continue to 421 → SES, so deliverability stays whole. If a
hard break is needed, set the SendGrid account's **Sender Reputation
→ Pause Sending** flag — every subsequent SendGrid call returns 401
"account paused", which `email_templates.py` already retries into
SES via the existing 5xx-tier path (it treats 4xx auth failures as
retry-eligible into the SES fallback by virtue of the
`_SENDGRID_NO_KEY` branch when the worker proxies an upstream auth
failure back to the backend).

---

## §4 — Phase C: Legacy secret deletion (only after §3 finalizes)

> Pre-condition: §3 Day-30 checkpoint has held green (warmup
> complete; SES fallback share < 1 % over the prior 24 h) **and**
> `scripts/infra/check_email_dns_alignment.py` still exits 0.

The seven secrets to delete:

| Secret | Lives in | Why |
|---|---|---|
| `OPENAI_API_KEY` | CF Worker secrets, Azure KV, GitHub Actions | Replaced by `azure_openai` (CF AI Gateway BYOK → Azure OpenAI). |
| `XAI_API_KEY` | CF Worker secrets, Azure KV, GitHub Actions | Slug never enabled in any pool weight; replaced by Workers AI `mistral-7b`. |
| `ANTHROPIC_API_KEY` | CF Worker secrets, Azure KV, GitHub Actions | Slug never reached prod routing; replaced by `azure_openai` + `vertex`. |
| `BEDROCK_PROXY_AUTH_TOKEN` | CF Worker secrets (was bound to `syrabit-bedrock-proxy`) | Worker is being deleted in §5. |
| `RESEND_API_KEY` | CF Worker secrets, Azure KV, GitHub Actions | Replaced by SendGrid (§3); India deliverability fell to 43 % vs SendGrid's 71 %. |
| `STRIPE_SECRET_KEY` | Azure KV, GitHub Actions | Razorpay (INR) is the sole gateway; the `/payments/stripe/*` and `/webhooks/stripe` routes return 410 Gone. |
| `STRIPE_WEBHOOK_SECRET` | Azure KV, GitHub Actions | Same as above — webhook handler deleted. |

### C.1 Deletion commands

> Snapshot every secret value into 1Password vault
> `syrabit/cloud-cutover-2026-05` **before** deleting. The vault
> reference is the only restore path if a rollback is required.

```bash
# Cloudflare Worker secrets (per worker that holds each secret)
for w in syrabit-edge-proxy syrabit-email syrabit-bedrock-proxy; do
  for s in OPENAI_API_KEY XAI_API_KEY ANTHROPIC_API_KEY \
           BEDROCK_PROXY_AUTH_TOKEN RESEND_API_KEY; do
    wrangler secret delete "$s" --name "$w" --env production || true
  done
done

# Azure Key Vault — soft-delete, retention is set to 90 days at the
# vault level so a fat-finger is recoverable inside that window.
for s in OPENAI-API-KEY XAI-API-KEY ANTHROPIC-API-KEY \
         RESEND-API-KEY STRIPE-SECRET-KEY STRIPE-WEBHOOK-SECRET; do
  az keyvault secret delete --vault-name syrabit-prod-kv --name "$s"
done

# GitHub Actions — repo-level secrets
for s in OPENAI_API_KEY XAI_API_KEY ANTHROPIC_API_KEY \
         BEDROCK_PROXY_AUTH_TOKEN RESEND_API_KEY \
         STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET; do
  gh secret delete "$s" --repo syrabit-ai/syrabit
done

# GitHub Actions — environment-scoped secrets (production env)
for s in OPENAI_API_KEY XAI_API_KEY ANTHROPIC_API_KEY \
         BEDROCK_PROXY_AUTH_TOKEN RESEND_API_KEY \
         STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET; do
  gh secret delete "$s" --repo syrabit-ai/syrabit --env production
done
```

### C.2 Verify the deletion

```bash
python3 scripts/infra/check_legacy_secrets_purged.py \
  --cf-account-id "$CF_ACCOUNT_ID" \
  --cf-workers syrabit-edge-proxy,syrabit-email,syrabit-bedrock-proxy \
  --azure-vault syrabit-prod-kv \
  --gh-repo syrabit-ai/syrabit \
  --gh-envs production
```

Exit codes: `0` = all 7 secrets confirmed absent in every surface;
`1` = one or more still present (script prints the surface + secret
name); `2` = harness failure.

---

## §5 — Phase D: Cloudflare Worker `syrabit-bedrock-proxy` deletion

Pre-condition: §4 confirms `BEDROCK_PROXY_AUTH_TOKEN` is gone from the
Worker; the Worker now has no usable secret and would 401 on every
request anyway.

```bash
# Confirm zero traffic for the prior 24 h before deletion
wrangler tail syrabit-bedrock-proxy --env production --format json \
  --once 2>&1 | head -5
# Expect: no recent invocations.

# Cloudflare Workers Analytics → syrabit-bedrock-proxy → Last 24 h
# Expect: 0 requests.

# Delete the Worker (operator must confirm in dashboard).
wrangler delete --name syrabit-bedrock-proxy --env production
```

Verify:

```bash
curl -sI https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/workers/scripts/syrabit-bedrock-proxy \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -o /dev/null -w "%{http_code}\n"
# Expect: 404
```

The repo-side `workers/bedrock-proxy/` directory was already removed in
#347; this step removes the deployed artifact.

---

## §6 — Phase E: SES fallback verification

The SES tier (Lambda + `email-fallback` SQS queue) is **kept** as the
final-tier 5xx-only fallback per §3 of `providers-task-347-
decommission.md`. Confirm it still works after the SendGrid cutover
using **only controls that already exist** — no new backend or worker
code is shipped by this task.

The verification leverages the existing 5xx-fallback path:
`email_templates.py` retries to its SES tier when the CF Email Worker
returns any 5xx, and the worker returns 500 if `SENDGRID_API_KEY` is
unset (see `workers/email-worker/src/index.ts` line ~165). So:

```bash
# 1. Snapshot the current SendGrid key (so we can put it back exactly).
#    Read it from Azure Key Vault, NOT from `wrangler secret get`
#    (which doesn't exist; CF only allows put/delete on secrets).
SG_KEY=$(az keyvault secret show \
  --vault-name syrabit-prod-kv --name SENDGRID-API-KEY \
  --query value -o tsv)
test -n "$SG_KEY" || { echo "FAIL: empty key from KV"; exit 1; }

# 2. Delete the worker's SENDGRID_API_KEY. The worker will now throw
#    "SENDGRID_API_KEY secret not set" on every send, which surfaces
#    as a 500 to the backend.
wrangler secret delete SENDGRID_API_KEY \
  --name syrabit-email --env production

# 3. Send one test transactional email through the normal API path.
#    Backend sees the worker 500 and falls through to its SES tier.
curl -fsS -X POST https://api.syrabit.ai/api/admin/email/test \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -d '{"to":"infra-test@syrabit.ai","template":"smoke_ses"}' | jq .
# Expect: {"status":"queued", "transport":"ses_fallback", ...}

# 4. Confirm Lambda + SES actually delivered.
aws logs tail /aws/lambda/syrabit-email-worker --since 5m \
  | rg -i "ses_send_ok"

# 5. Restore the worker's SendGrid key IMMEDIATELY. Do not leave the
#    worker keyless; user-visible transactional email will fail-open
#    to SES for as long as the key is missing, which is fine for the
#    ~60 s test window but not as a steady state.
printf '%s' "$SG_KEY" | wrangler secret put SENDGRID_API_KEY \
  --name syrabit-email --env production
unset SG_KEY

# 6. Confirm normal SendGrid path is back.
curl -fsS -X POST https://api.syrabit.ai/api/admin/email/test \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -d '{"to":"infra-test@syrabit.ai","template":"smoke_sendgrid"}' | jq .
# Expect: {"status":"sent", "transport":"sendgrid", ...}
```

If step 4 fails, **stop the cutover and re-investigate** — losing the
SES floor at the same time SendGrid is the primary leaves no
deliverability fallback at all. Do not proceed to §4 secret deletion
until step 4 is confirmed green; specifically, **do not delete
RESEND_API_KEY from any surface** while the SES floor is unverified
(the lint-guardian-allowlisted admin alert path on Resend is the
de-facto secondary deliverability floor for ops-critical mail
during this verification window).

---

## §7 — Phase F: Post-cutover smoke matrix

Run all rows green before declaring #364 finished. Record output in
the cutover ticket.

| # | Probe | Expected |
|---|---|---|
| 1 | `curl -fsS https://api.syrabit.ai/api/health \| jq .git_sha` | matches the new ACA revision sha (§2) |
| 2 | Send a real password-reset email to a known mailbox | arrives via SendGrid (check `X-SG-EID` header in source) |
| 3 | `python3 scripts/infra/check_legacy_secrets_purged.py …` | exit 0 |
| 4 | `python3 scripts/infra/check_email_dns_alignment.py --domain em.syrabit.ai --link-branding-cname em1234.em.syrabit.ai` (use the operator's actual prefix) | exit 0 |
| 5 | `curl -sI https://api.cloudflare.com/.../workers/scripts/syrabit-bedrock-proxy -H "Authorization: Bearer $CF_API_TOKEN"` | HTTP 404 |
| 6 | Stripe webhook probe: `curl -X POST https://api.syrabit.ai/webhooks/stripe -d '{}'` | HTTP 410 Gone |
| 7 | xAI probe: `curl -X POST https://api.syrabit.ai/api/ai/chat -H 'X-Force-Provider: grok'` | HTTP 400 with `unknown_provider` body |
| 8 | Workers AI fallback hit-rate over the prior hour > 0 (proves the chain still serves traffic from the replacement tier) | Cloudflare AI Gateway logs |
| 9 | `python3 scripts/infra/sendgrid_warmup_monitor.py --mode messages --window-minutes 360 --bounce-max-pct 2.0 --spam-max-pct 0.05 --min-messages 200` (or `--mode stats` if the SendGrid plan lacks the Email Activity History add-on, with `--window-days 1`) | exit 0 |
| 10 | DO rollback floor still serves `/api/health` (kept for 14 days post-cutover, per `aca-cutover.md` §99) | `curl -fsS https://syrabit-backend-app.ondigitalocean.app/api/health` returns 200 |

---

## §8 — Rollback per phase

| Phase | Rollback |
|---|---|
| §2 ACA swap | `aca-cutover.md` §2 (`az containerapp ingress traffic set --revision-weight <prev>=100`); restore the prior `SENDGRID-API-KEY` value from 1Password if it was overwritten. |
| §3 SendGrid warmup | Toggle **Settings → IP Addresses → Warmup → Off** in the SendGrid dashboard so the daily cap stops growing; for a hard break, also toggle **Sender Reputation → Pause Sending** so every send 4xx's into the SES tier within ~30 s. |
| §4 Secret deletion | Restore from 1Password `syrabit/cloud-cutover-2026-05`; `wrangler secret put` / `az keyvault secret recover` (within the 90-day soft-delete window) / `gh secret set`. |
| §5 Worker deletion | `wrangler deploy` from the prior commit of `workers/bedrock-proxy/`; restore `BEDROCK_PROXY_AUTH_TOKEN` first. **Note:** repo deleted the directory in #347, so this requires a `git checkout` of a pre-#347 sha. |
| §6 SES verification | Same as §3 rollback. |

---

## §9 — Decision log (operator fills in)

| Date | Phase | Action | Operator | Outcome |
|---|---|---|---|---|
|  | A | ACA revision swap | | |
|  | B | Enable SendGrid IP Warmup (30-day default) | | |
|  | B | Day 1 checkpoint (24 h soak) | | |
|  | B | Day 3 checkpoint (48 h soak) | | |
|  | B | Day 7 checkpoint (96 h soak) | | |
|  | B | Day 14 checkpoint (168 h soak) | | |
|  | B | Day 30 checkpoint (warmup complete) | | |
|  | C | Secret deletion (CF / Azure KV / GitHub) | | |
|  | D | `syrabit-bedrock-proxy` Worker deletion | | |
|  | E | SES fallback verification | | |
|  | F | Smoke matrix | | |

---

## §10 — Out of scope

- Removal of Workers AI fallback weights (handled by Task #366 once
  fallbacks are proven to actually serve traffic under load).
- Migration of admin alert emails off Resend onto SendGrid (Task #365 —
  this task only swaps user-visible transactional email; the admin
  alert path is allowlisted in the lint guardian per
  `providers-task-347-decommission.md` §"Lint guardian").
- Workers AI / Vertex / Azure OpenAI capacity work (covered by #363).
- Any new SendGrid template authoring; existing templates are reused.

---

End of `infra/cloud-cutover-364.md`.
