# Tollbit Worker rollout

The combined Tollbit analytics and Agent Site Worker is deployed to production
from `wrangler.tollbit-production.toml`. The isolated test configuration remains
in `wrangler.tollbit-draft.toml`.

## Current boundary

- Public routes `syrabit.ai/*` and `www.syrabit.ai/*` point to the combined
  Tollbit Worker. The more-specific `syrabit.ai/api/*` and `api.syrabit.ai/*`
  routes remain on `syrabitworker-prod`.
- The `tollbit_ai_forward` Snippet rule is disabled so it cannot intercept
  requests before the combined Worker.
- The existing HTTP Logpush job writes to R2 and is not the Worker-to-Tollbit
  connection. Do not use it as Tollbit ingestion. Keep it only as a separate
  archive until the Worker is verified and the user decides whether to stop it.
- Worker Trace Events Logpush is diagnostics only. It requires a separate
  account-level `workers_trace_events` job to a dedicated R2 prefix; it does
  not modify or feed the existing zone-level `http_requests` job.
- The user chose the standard API-key Worker integration despite Tollbit's
  guide recommending Enterprise Logpush plus Snippet for this account plan.
  Tollbit has not confirmed this exception in writing.

## Worker behavior

- Hardening changes in this branch require review and a protected release.
  Their source presence does not prove the deployed Worker has changed.
- Passes requests to the publisher origin unless the request is a public GET or
  HEAD from the approved paid AI/RAG agent list, or Cloudflare marks it as a
  verified bot in the `Search Engine` category.
- A search-engine user-agent string alone is never sufficient for free
  forwarding; the Worker requires both `request.cf.botManagement.verifiedBot`
  and `request.cf.verifiedBotCategory === "Search Engine"`.
- Public-content routes include `/`, `/home`, Library, and recognized English
  and Assamese curriculum routes. Discovery files and private/API routes are
  not paywalled by this Worker.
- Blocks robots-disallowed training agent names with a local, non-cacheable
  403 on public-content routes. Training matches take precedence over retrieval
  names. This is declared-UA policy enforcement, not proof of crawler identity.
- Includes Perplexity and YouBot alongside the existing retrieval agents.
- Cookies and application credentials no longer bypass forwarding on public
  routes. They are still stripped from all Agent Site requests.
- Excludes account, chat, admin, API, authentication, and other private routes.
- Builds the Agent Site request with an explicit header allowlist. It forwards
  the user agent, Accept, and Tollbit signature headers, but never cookies,
  authorization, API keys, or user identity headers.
- Drops query parameters from the Tollbit destination URL.
- Fails closed with a non-cacheable 503 if the Agent Site cannot be reached
  within eight seconds or attempts a redirect. Never serves origin content as
  an outage fallback for gated bots. Ordinary visitors retain origin routing.
- Sends eligible public page outcomes to `https://log.tollbit.com/log` using
  the `TollbitKey` secret binding. Logs include the standard IP, geographic,
  user-agent, response, referrer, and signature fields. Page query strings,
  cookies, authorization values, and identity headers are not included.
- The owner-selected analytics scope is every public-page request, including
  ordinary visitor requests, not only recognized crawlers. Signature fields
  are capped at 8 KiB and rejected if they contain control characters.
- Agent matching uses token boundaries to avoid matching generic substrings
  such as `Code` inside unrelated user-agent products. The supplied bot catalog
  is a routing list, not proof of identity; confirmed training matches still
  take precedence and are blocked.
- Logs token-required JSON responses, local training denials, HEAD requests,
  and provider errors, not just successful HTML pages.
- Delivers analytics asynchronously and does not block page responses.
- Bounds log submissions to five seconds without automatic retries that could
  duplicate records. Endpoint acceptance is not dashboard-ingestion proof.
- Emits the delivery outcome, provider HTTP status when available, and elapsed
  time for Tollbit submissions. It never logs the API key, response text,
  request headers, or analytics payload; endpoint acceptance is not proof of
  dashboard ingestion.
- In test mode, accepts content requests only through the `workers.dev`
  hostname and only from Tollbit's currently documented static egress IPs.
  Other requests receive 404; `GET /__tollbit_test_health` is a no-upstream
  health check. Test requests map to the fixed `syrabit.ai` origin.

## Verified search-engine rollout

- Keep the existing `syrabit.ai/*` and `www.syrabit.ai/*` Worker routes. Do not
  add a WAF Allow/Skip rule or enable the `tollbit_ai_forward` Snippet.
- Before the protected release, add or verify this Cloudflare custom rule with
  the **Log** action only. It is observability, not a forwarding or bypass rule:

  ```text
  http.host in {"syrabit.ai" "www.syrabit.ai"}
  and cf.client.bot
  and cf.verified_bot_category eq "Search Engine"
  and http.request.method in {"GET" "HEAD"}
  and (
    http.request.uri.path in {"/" "/home" "/home/" "/library" "/as/library"}
    or http.request.uri.path matches "^/(as/)?(ahsec|degree)(/|$)"
  )
  ```

- This repository does not apply Cloudflare zone rules or change Tollbit
  settings. In Tollbit, configure Open Access for specific traditional search
  crawlers (Googlebot, Bingbot, Applebot, and DuckDuckBot) on public curriculum
  paths only. Do not use the broad `google` group. Keep AI Search/RAG paid,
  training crawlers blocked, and uncertain bots paid.
- The Worker routes Cloudflare-verified Search Engine requests through the
  Agent Site. A request claiming one of the selected free search crawlers
  (Googlebot, Bingbot, Applebot, or DuckDuckBot) but lacking that verified
  signal receives a non-cacheable 403 instead of falling through to origin.
  Training denials take precedence; AI/RAG matching remains unchanged.
- This does not classify arbitrary unknown automation. Do not describe
  user-agent checks as covering unknown bots; any broader bot-management
  threshold or fail-closed policy requires separate testing.
- After release, verify real verified search-crawler requests reach the Agent
  Site and review Cloudflare/Tollbit analytics separately. Synthetic
  user-agent tests prove routing logic only, not crawler identity or referrals.

## Production verification status

1. The production route patterns `syrabit.ai/*` and `www.syrabit.ai/*` point to
   the combined Worker; the API routes remain on `syrabitworker-prod`.
2. The `tollbit_ai_forward` Snippet rule is disabled so requests reach the
   combined Worker rather than being intercepted first.
3. Verify provider-side ingestion and Agent Site behavior using real Tollbit
   traffic; never fabricate signature headers or claim a dashboard event from
   a synthetic request as verified attribution.
4. `TOLLBIT_API_KEY` remains present as a Cloudflare Worker secret; its value
   was not read or copied into the repository.
5. The HTTP Logpush job remains a separate R2 archive, not the Tollbit
   connection. It was not disabled.

## Protected release and acceptance

The protected Cloudflare workflow verifies the existing secret **name**,
deploys with `wrangler.tollbit-production.toml` (never the isolated draft
config), and runs the read-only HTTP checker after both Pages and Worker
deployment. No rates, licenses, payouts, or existing Logpush jobs are modified.

Run the local regression suite:

```sh
pnpm --filter syrabit-edge exec vitest run src/tollbit tests/tollbit-discovery.test.ts tests/tollbit-live-checker.test.ts
pnpm --filter syrabit-edge exec tsc --noEmit
```

After the approved release, run:

```sh
node apps/edge/scripts/verify-tollbit.mjs https://syrabit.ai
```

The checker verifies representative available English, Assamese, Degree, and
subject routes, root-page gating, cookie regression, denied training UAs,
ordinary-visitor delivery, plain-text `ai.txt`, and directory/sitemap parity.
Absent curriculum coverage is reported explicitly; it is not invented.
It does not claim authenticated crawler origin, licensed delivery, dashboard
ingestion, onboarding completion, or revenue.

### Licensed retrieval and private verification

Tollbit's documented subdomain contract uses a Tollbit-issued Bearer JWT:
https://docs.tollbit.com/reference/tollbitsubdomain_getcontent
Licensed clients must present that token directly to the Agent Site. Do not
forward the publisher's application Authorization header or use the analytics
key as a content token. Verify real licensed access only with a legitimately
issued token and explicit approval before any paid transaction.

Remaining provider-side acceptance:

1. Inspect the immediate response to the dashboard's Verify setup action;
   forwarding-test success and an owner preview do not prove the saved status.
2. Reconcile a controlled request window with Tollbit dashboard counts.
3. Verify legitimately licensed delivery, separately from unlicensed 402 tests.
4. Disable Enterprise HTTP Logpush only after the owner explicitly approves
   that separate cutover. Trace export is diagnostics and may remain enabled.

## Worker Trace Events export

The Cloudflare account must have a separate `workers_trace_events` Logpush job
targeting the existing `syrabit-tollbit-logpush` bucket under a distinct prefix
such as `tollbit/workers-trace-events/`. Keep the current `http_requests` job
and its `tollbit/http-requests/` prefix unchanged. The separate Worker dataset
is for diagnostics and does not replace Tollbit's HTTP analytics feed.

Create the job in Cloudflare Dashboard → Logs → Logpush, select **Workers Trace
Events**, and configure the R2 destination directly in the dashboard. Do not
copy or expose the existing Logpush write credentials. Filter the job to
`ScriptName = syrabit-tollbit-worker-draft` and select only
`ScriptName`, `EventTimestampMs`, `Outcome`, `Logs`, and `Exceptions`; avoid the
full `Event` field to keep request metadata out of this diagnostic export.
Verify that the job is enabled before deploying this Worker's `logpush = true`
setting; historical events are not backfilled.
