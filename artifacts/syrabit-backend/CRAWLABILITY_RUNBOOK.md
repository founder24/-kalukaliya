# Crawlability Runbook — Syrabit.ai

Last updated: 2026-05-02 (Task #259)

This document covers the one-time and recurring steps needed to keep
Syrabit.ai fully crawlable by Google and Bing, and to distinguish real
crawler traffic from internal prewarm traffic in analytics.

---

## 1. Google Search Console (GSC) — Initial Property Setup

**One-time step. Do this once before the first sitemap submission.**

1. Go to [search.google.com/search-console](https://search.google.com/search-console)
2. Click **Add property** → choose **URL prefix** → enter `https://syrabit.ai/`
3. Under **HTML file** verification, Google will reference the file
   `googlefbe0a804ad7e5fdd.html` — this file is already deployed at
   `artifacts/syrabit/public/googlefbe0a804ad7e5fdd.html` and is served
   directly by Cloudflare Pages (it is listed in `_routes.json` under
   `exclude` so it bypasses the worker and is served as a static asset).
4. Click **Verify** — GSC will fetch `https://syrabit.ai/googlefbe0a804ad7e5fdd.html`
   and confirm the property.

---

## 2. Submit the Sitemap to Google Search Console

**One-time step after property verification. Re-submit if the sitemap URL changes.**

1. In GSC, open the verified `https://syrabit.ai/` property.
2. In the left sidebar, go to **Indexing → Sitemaps**.
3. In the "Add a new sitemap" field, enter:
   ```
   sitemap-index.xml
   ```
   (GSC prepends the property URL, resulting in `https://syrabit.ai/sitemap-index.xml`)
4. Click **Submit**.
5. GSC will fetch the sitemap index and discover all child sitemaps
   (`sitemap-subjects.xml`, `sitemap-chapters.xml`, `sitemap-notes.xml`, etc.).

**Verify the submission succeeded:**
- Status should show **Success** within a few minutes.
- The "Discovered URLs" count should increase within 24–72 hours.
- If status shows "Fetch error", run:
  ```bash
  curl -I https://syrabit.ai/sitemap-index.xml
  ```
  Expected: `HTTP/2 200` with `content-type: application/xml`.
  If you see `text/html`, the CF Pages Worker proxy gap is back — check
  `SEO_PASSTHROUGH_RE` in `artifacts/syrabit/public/_worker.js`.

---

## 3. Submit the Sitemap to Bing Webmaster Tools

**One-time step.**

1. Go to [bing.com/webmasters](https://www.bing.com/webmasters)
2. Add site `https://syrabit.ai/` and verify ownership (use the HTML
   meta tag method or the XML file method — either works).
3. In the left sidebar, go to **Sitemaps** → **Submit sitemap**.
4. Enter `https://syrabit.ai/sitemap-index.xml` and submit.

---

## 4. Verify Cloudflare Bot Fight Mode Allows Verified Bots

**Check before any sitemap submission and after any Cloudflare dashboard change.**

The most common cause of zero Googlebot traffic is `sbfm_verified_bots`
being set to `managed_challenge` or `block` instead of `allow`. Verified
search engine bots (Googlebot, Bingbot) will be silently blocked or shown
a challenge they cannot solve, and no traffic will appear in logs.

**To check the current value:**
```bash
CLOUDFLARE_API_TOKEN=<your-token> \
  node artifacts/syrabit/scripts/nightly-smoke.js
```

Look for the line:
```
✓  sbfm_verified_bots: "allow"
```

If it shows `✗` with any other value:

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com) →
   syrabit.ai → **Security → Bots**.
2. Under **Super Bot Fight Mode**, find **Verified bots** and set it
   to **Allow**.
3. Save and re-run the smoke check to confirm.

**The nightly CI smoke run (`nightly-smoke.js`) asserts this as a hard
failure** — any accidental dashboard revert will surface overnight.

---

## 5. Verify Sitemap Endpoints Return Correct Content-Type

Run these `curl` checks after any CF Pages deployment or Worker change:

```bash
# Root sitemap index — must be application/xml
curl -sI https://syrabit.ai/sitemap-index.xml | grep -i content-type

# Root alias — must be application/xml (not text/html)
curl -sI https://syrabit.ai/sitemap-subjects.xml | grep -i content-type

# /api/seo/ sub-path — must be application/xml (not text/html)
curl -sI https://syrabit.ai/api/seo/sitemap-subjects.xml | grep -i content-type

# robots.txt — must be text/plain
curl -sI https://syrabit.ai/robots.txt | grep -i content-type
```

All four must return `content-type: application/xml` (or `text/plain` for
robots.txt). If any returns `text/html` the CF Pages Worker's
`SEO_PASSTHROUGH_RE` is not matching the path.

**The nightly smoke run checks all of these automatically** — see the
"Task #259 — Sitemap Content-Type checks" section in `nightly-smoke.js`.

---

## 6. Distinguish Real Googlebot from Internal Prewarm Traffic

The prewarm system (`routes/bot_discovery.py::prewarm_bot_cache`) spoofs
Googlebot's UA so the CF Pages Worker serves the bot-rendered HTML path
on cold-cache hits. Without a filter, every prewarm run inflates the
Googlebot row in Cloudflare analytics.

**How to separate them:**

Every internal prewarm request carries two identifiers:

| Identifier | Value |
|-----------|-------|
| `User-Agent` suffix | `SyrabitInternal` |
| Custom header | `X-Syrabit-Internal: 1` |

**In Cloudflare Analytics:**
- Go to **Analytics → Traffic** → filter on
  `User Agent contains "SyrabitInternal"` to see only prewarm traffic.
- Negate that filter to see only real bot traffic.

**In Cloudflare Logpush (R2 / BigQuery):**
```sql
-- Real Googlebot only (exclude prewarm)
SELECT *
FROM http_requests
WHERE lower(ClientRequestUserAgent) LIKE '%googlebot%'
  AND lower(ClientRequestUserAgent) NOT LIKE '%syrabitinternal%'
  AND lower(RequestHeaders['x-syrabit-internal']) IS DISTINCT FROM '1'
```

**In Cloudflare WAF Custom Rules:**
The WAF Custom Rule (`CLOUDFLARE_INTERNAL_BOT_TAGGING.md`) tags all
requests with `X-Syrabit-Internal: 1` as "Syrabit Internal Bot" — they
appear in Firewall Analytics under that tag and can be excluded from
bot-traffic dashboards with a single filter.

---

## 7. Recurring Checks

| Frequency | Check | Tool |
|-----------|-------|------|
| Nightly (CI) | `sbfm_verified_bots === 'allow'` | `nightly-smoke.js` |
| Nightly (CI) | Sitemap Content-Type: application/xml | `nightly-smoke.js` |
| Weekly | GSC Coverage report — "Discovered, not indexed" count | GSC dashboard |
| Weekly | Bing Webmaster Tools — crawl errors | Bing WMT dashboard |
| On each CF Pages deploy | `curl -I https://syrabit.ai/sitemap-index.xml` | Manual |
| Quarterly | Cloudflare zone settings audit | `cloudflare-annual-review.js` |

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Sitemaps return `text/html` | `SEO_PASSTHROUGH_RE` doesn't match path | Update regex in `_worker.js` |
| Zero Googlebot traffic | `sbfm_verified_bots` ≠ `allow` | CF Dashboard → Security → Bots → Allow verified bots |
| GSC shows "Fetch error" on sitemap | Backend down or Worker misconfigured | Check `api.syrabit.ai/health`, re-deploy Worker |
| Prewarm inflating Googlebot count | Missing `X-Syrabit-Internal` header filter | Apply the WAF rule and add Logpush filter |
| Child sitemaps 404 in GSC | `<loc>` entries use `/api/seo/` prefix AND Worker regex gap | Fixed in Task #259 — use root aliases in sitemap index |
