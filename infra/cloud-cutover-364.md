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
| 8 | `scripts/infra/check_email_dns_alignment.py --domain em.syrabit.ai` exits 0 | `python3 scripts/infra/check_email_dns_alignment.py --domain em.syrabit.ai` | exit 0 + all 4 rows OK |

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

## §3 — Phase B: SendGrid traffic warmup (1 % → 10 % → 50 % → 100 %)

The CF Email Worker (`workers/email-worker`) is the first hop for every
transactional email; it picks SendGrid vs the Resend-shaped legacy path
by reading the Worker var `SENDGRID_TRAFFIC_PCT` (0–100). Backend
`email_templates.py` reads the same value via the
`EMAIL_WORKER_URL`-routed Worker so both hops obey the same percentage.

> **Why warm up at all?** Even though `em.syrabit.ai` was already SES-
> validated for the legacy SES path, this is SendGrid's first time
> sending under that domain. SendGrid's IP reputation system penalises
> any sudden volume spike from a new sending identity; a phased ramp
> keeps bounces below the **5 %** threshold and complaints below
> **0.1 %**, the two SendGrid auto-suspension floors.

| T+ | Worker var | Min soak | Gate before next step |
|---|---|---|---|
| 0 h | `SENDGRID_TRAFFIC_PCT=1` | 4 h | bounce rate < 2 %, spam rate < 0.05 %, zero `permission_denied` from SendGrid |
| 4 h | `SENDGRID_TRAFFIC_PCT=10` | 6 h | same thresholds; ≥ 50 messages sent in the soak window |
| 10 h | `SENDGRID_TRAFFIC_PCT=50` | 8 h | same thresholds; ≥ 200 messages sent |
| 18 h | `SENDGRID_TRAFFIC_PCT=100` | 6 h | thresholds hold; SES fallback path served < 1 % |
| 24 h | finalize: delete Resend secrets per §4 | n/a | post-finalize smoke (§5) green |

Operator commands:

```bash
# Step the Worker var (repeat at each T+ row)
cd workers/email-worker
wrangler secret put SENDGRID_TRAFFIC_PCT --env production
# (paste the integer, e.g. 1, then 10, then 50, then 100)

# Backend reads the same value via the Email Worker; no separate
# backend deploy is required because email_templates.py defers the
# routing decision to the Worker on every request.

# Verify the Worker picked up the value:
wrangler tail syrabit-email --env production --format pretty \
  | rg "SENDGRID_TRAFFIC_PCT"
```

Run the SendGrid warmup monitor at each soak window:

```bash
SENDGRID_API_KEY=$(az keyvault secret show \
  --vault-name syrabit-prod-kv --name SENDGRID-API-KEY \
  --query value -o tsv) \
python3 scripts/infra/sendgrid_warmup_monitor.py \
  --window-minutes 60 \
  --bounce-max-pct 2.0 \
  --spam-max-pct 0.05 \
  --min-messages 50
```

Exit codes: `0` = OK, ramp to next step; `1` = threshold breach, **do
not ramp** — investigate; `2` = harness failure (network / credentials).

### B.1 Rollback within Phase B

Setting `SENDGRID_TRAFFIC_PCT=0` and redeploying the Worker reverts
**all** new email sends to the SES-only fallback path within ~30 s.
Do this immediately if either threshold is exceeded for two
consecutive 5-minute windows.

---

## §4 — Phase C: Legacy secret deletion (only after §3 finalizes)

> Pre-condition: §3 row "T+18 h → SENDGRID_TRAFFIC_PCT=100" has held
> green for 6 h **and** `scripts/infra/check_email_dns_alignment.py`
> still exits 0.

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
decommission.md`. Confirm it still works after the SendGrid cutover:

```bash
# Force the Worker to bypass SendGrid so SES handles a single test
# message; then revert.
wrangler secret put SENDGRID_FORCE_BYPASS --env production   # → "1"
curl -fsS -X POST https://api.syrabit.ai/api/admin/email/test \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -d '{"to":"infra-test@syrabit.ai","template":"smoke_ses"}' | jq .
# Expect: {"status":"queued", "transport":"ses_fallback", ...}

# Confirm Lambda + SES delivery from CloudWatch
aws logs tail /aws/lambda/syrabit-email-worker --since 5m \
  | rg -i "ses_send_ok"

# Revert
wrangler secret delete SENDGRID_FORCE_BYPASS --env production
```

If the test fails, **stop the cutover and re-investigate** — losing
the SES floor at the same time SendGrid is the primary leaves no
deliverability fallback at all.

---

## §7 — Phase F: Post-cutover smoke matrix

Run all rows green before declaring #364 finished. Record output in
the cutover ticket.

| # | Probe | Expected |
|---|---|---|
| 1 | `curl -fsS https://api.syrabit.ai/api/health \| jq .git_sha` | matches the new ACA revision sha (§2) |
| 2 | Send a real password-reset email to a known mailbox | arrives via SendGrid (check `X-SG-EID` header in source) |
| 3 | `python3 scripts/infra/check_legacy_secrets_purged.py …` | exit 0 |
| 4 | `python3 scripts/infra/check_email_dns_alignment.py --domain em.syrabit.ai` | exit 0 |
| 5 | `curl -sI https://api.cloudflare.com/.../workers/scripts/syrabit-bedrock-proxy -H "Authorization: Bearer $CF_API_TOKEN"` | HTTP 404 |
| 6 | Stripe webhook probe: `curl -X POST https://api.syrabit.ai/webhooks/stripe -d '{}'` | HTTP 410 Gone |
| 7 | xAI probe: `curl -X POST https://api.syrabit.ai/api/ai/chat -H 'X-Force-Provider: grok'` | HTTP 400 with `unknown_provider` body |
| 8 | Workers AI fallback hit-rate over the prior hour > 0 (proves the chain still serves traffic from the replacement tier) | Cloudflare AI Gateway logs |
| 9 | `python3 scripts/infra/sendgrid_warmup_monitor.py --window-minutes 360 --bounce-max-pct 2.0 --spam-max-pct 0.05 --min-messages 200` | exit 0 |
| 10 | DO rollback floor still serves `/api/health` (kept for 14 days post-cutover, per `aca-cutover.md` §99) | `curl -fsS https://syrabit-backend-app.ondigitalocean.app/api/health` returns 200 |

---

## §8 — Rollback per phase

| Phase | Rollback |
|---|---|
| §2 ACA swap | `aca-cutover.md` §2 (`az containerapp ingress traffic set --revision-weight <prev>=100`); restore the prior `SENDGRID-API-KEY` value from 1Password if it was overwritten. |
| §3 SendGrid warmup | Set `SENDGRID_TRAFFIC_PCT=0` and redeploy the Worker; verify SES tier handles 100 % of new sends; do not proceed until thresholds are re-investigated. |
| §4 Secret deletion | Restore from 1Password `syrabit/cloud-cutover-2026-05`; `wrangler secret put` / `az keyvault secret recover` (within the 90-day soft-delete window) / `gh secret set`. |
| §5 Worker deletion | `wrangler deploy` from the prior commit of `workers/bedrock-proxy/`; restore `BEDROCK_PROXY_AUTH_TOKEN` first. **Note:** repo deleted the directory in #347, so this requires a `git checkout` of a pre-#347 sha. |
| §6 SES verification | Same as §3 rollback. |

---

## §9 — Decision log (operator fills in)

| Date | Phase | Action | Operator | Outcome |
|---|---|---|---|---|
|  | A | ACA revision swap | | |
|  | B | SENDGRID_TRAFFIC_PCT 0 → 1 | | |
|  | B | SENDGRID_TRAFFIC_PCT 1 → 10 | | |
|  | B | SENDGRID_TRAFFIC_PCT 10 → 50 | | |
|  | B | SENDGRID_TRAFFIC_PCT 50 → 100 | | |
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
