# Cloudflare Monthly Cost Review

**Cadence**: first business day of each calendar month.
**Owner**: rotating — assign in the project task tracker.
**Time budget**: ~20 minutes once you know where to click.
**Source-of-truth doc**: `docs/cloudflare-cost-map.md` (update it if you
discover the inventory has drifted).

The point of this review is to catch **non-Workers-AI credit drawdown
early**, while there's still time to cap or migrate it, so the $5,000
Cloudflare-for-Startups pool stays as close to 100% reserved for Workers
AI inference as possible.

---

## Step-by-step

1. **Pull the latest invoice.**
   Cloudflare Dashboard → Billing → Invoices → most recent month.
   Download the PDF and save it to a private archive (do not commit
   invoices to git — they include the card-on-file last-4).

2. **Record the credit balance.**
   Cloudflare Dashboard → Billing → Subscriptions → "Cloudflare for
   Startups" panel.
   Note: granted, consumed, remaining, and days remaining in the
   12-month window.

3. **Break down the spend by product.**
   On the invoice, list every line item with its dollar amount. Map
   each line item back to one of the bucket symbols in the cost map
   (🟢 / 🟦 / 🟪 / 🔴 / ⚪).

4. **Flag anomalies.** For each line item, ask:
   - Is this product listed in `docs/cloudflare-cost-map.md`? If not,
     someone enabled a new product without updating the cost map —
     update it in the same PR as this review entry.
   - Is the line item over the per-product cap from the cost map?
     (R2 >$10/mo, D1 >$5/mo, KV >$10/mo, DO >$10/mo, Vectorize >$15/mo,
     Analytics Engine >$5/mo, **any non-Workers-AI line item >$20/mo**.)
     If yes, file a follow-up task to investigate before next month.

5. **Verify the Workers AI tagging is intact.**
   Cloudflare Dashboard → AI → AI Gateway → `syrabit-ai-gw` (or whatever
   `WORKERS_AI_GATEWAY_ID` is set to) → Logs.
   Confirm recent requests carry the `workers-ai-fallback:*` and
   `workers-ai-edge-vector-search` metadata tags. If the tag list is
   empty or contains untagged calls, something on the worker drifted —
   `WORKERS_AI_GATEWAY_ID` may have been unset, or a new
   `env.AI.run(...)` callsite was added without going through
   `aiGatewayOpts(env, ...)`. Fix before next review.

6. **Confirm AI Gateway caching is still saving credits.**
   Same panel → Cache hit-rate. Anything below ~30% on the embed /
   classification routes is suspicious; raise the per-route TTL or
   inspect whether prompts have started embedding a timestamp /
   nonce that's defeating the cache.

7. **Append a row to the running log** (table below). Commit the
   updated doc.

8. **If month index ≥ 9** (i.e. ~February 2027), trigger the top-up
   email — see `docs/cloudflare-startup-credits-emails.md` §2.

---

## Running log

Append a new row at the **top** of the table each month. Round dollars
to the nearest dollar; round percentages to the nearest whole number.

| Month | Date reviewed | Reviewer | Credits remaining | Workers AI $ | Non-AI $ | Largest non-AI line item | Action items / anomalies |
|---|---|---|---|---|---|---|---|
| _e.g. 2026-06_ | _2026-06-01_ | _@name_ | _$4,720_ | _$210_ | _$70_ | _R2 $42_ | _none_ |

(Add new rows above this placeholder; delete the placeholder once the
first real entry exists.)

---

## When something goes wrong

- **Sudden spike in a non-AI line item.** Don't wait for next month;
  open the cost map, find the per-product policy, and act on the
  mitigation listed there (lifecycle rule, TTL bump, sampling drop,
  retiring a binding).
- **Untagged Workers AI calls on the invoice.** Search the codebase for
  `env.AI.run(` and confirm every callsite uses `aiGatewayOpts(...)`.
  Add a regression test if a new callsite was missed.
- **Credit pool draining faster than `(remaining_credits / months_left)`
  would predict.** Bring forward the top-up email from §2 of
  `docs/cloudflare-startup-credits-emails.md`. Better to ask in
  month 6 with real growth data than in month 11 in panic.

---

## Month-9 reminder

- **Trigger date**: 2027-02-03 (first business day on/after 2027-02-01).
- **Action**: send the email in `docs/cloudflare-startup-credits-emails.md` §2.
- **Where the reminder lives**: a project task titled "Send Cloudflare
  startup credits month-9 top-up email" should be created at the same
  time as this doc, due 2027-02-03, owner = whoever runs infra at that
  time. If that task is missing, create it now before closing this doc.
