# Topic Search and Bot Traffic Baseline — 2026-10-24

## Status

This is the dated evidence baseline for the topic-traffic report. The report is a live, D1-backed admin view; this document does not claim a production inventory count or invent traffic totals. No production rows were queried for this baseline.

- Report schema: `topic-traffic-report-v1`
- Inventory rules: `topic-url-inventory-v1`
- Target: aspirational 10 human-discovery visits and 10 verified crawler fetches per eligible language-specific topic URL per day (70 of each per rolling 7 days)
- Timezone: UTC
- Current-day measurements: partial

The inventory reports candidate, eligible, and excluded language-specific URLs separately. English and Assamese URLs are separate candidates. The eligibility denominator excludes drafts, unpublished curriculum parents, unreviewed or incomplete content, duplicate definitions or canonical paths, and incomplete route hierarchies. Exclusion reasons remain visible in the admin report.

## Measurement definitions

- **Organic-search landing sessions:** consented first-party browser sessions whose first recorded consented page view is marked as the session entry and whose referrer host matches a known search provider.
- **AI-referral landing sessions:** the same session-entry rule, with a known AI answer-provider referrer.
- **Human-discovery visits:** the sum of those two distinct session-entry measures. This is a consented browser-telemetry proxy, not a verified-human count and not all site traffic.
- **Site sessions:** distinct consented session IDs with a page-view event on a topic URL.
- **Browser page-view events:** consented browser beacons. These are not server HTTP requests.
- **Search Console impressions and clicks:** separate search-performance measurements; neither is interchangeable with browser sessions or page requests.
- **Verified crawler fetches:** require authoritative crawler identity, the final successful response, `text/html`, and an eligible canonical topic URL. A User-Agent string or aggregate bot count is not sufficient.
- **Tollbit-attributed or paid requests:** reported only after supported publisher integration and request identity are verified.

## Source coverage at baseline

| Measure | Baseline status | Reason and action |
| --- | --- | --- |
| Topic inventory | Available on report request; production totals not certified in this snapshot | Review the live inventory version, denominator, and exclusion reasons in the admin report. |
| First-party browser analytics | Partial; full-window target evaluation is N/A | D1 contains consent-based page-view beacons. The event ledger has no verified collection-start marker, older events do not carry the session-entry marker, and visitors who decline optional analytics are absent. Use observed values only as a partial sample; do not interpret missing topic/day records as zero. |
| Search Console clicks and impressions | N/A | Search Console search-performance reporting is not connected to this report. Connect the verified `syrabit.ai` property and import the relevant history before evaluating indexing or discovery gaps. |
| Cloudflare request-level crawler outcomes | N/A; no records are ingested | A request-log configuration was observed with fields suitable for identity and final-response checks, but the application has no safe ingestion path and no log records were read. Add a privacy-safe ingestion and aggregation path; count only authoritative verified identity plus final 200 `text/html` responses for eligible topic URLs. Any embedded destination credentials exposed during configuration review must be rotated before that destination is reused. |
| Tollbit attribution, paid requests, and revenue | N/A / unverified | A log-job label or general crawler request does not prove a publisher Tollbit integration, paid access, or revenue. Verify current supported requirements and identity evidence before reporting any attribution. |

## Baseline conclusion

Neither the 10-per-day human-discovery target nor the 10-per-day verified-crawler target can be certified from this baseline. First-party observed events can appear in the report, but collection coverage is unverified and optional-consent telemetry is incomplete by design. Search Console and request-level Cloudflare outcomes remain N/A, so indexing, full site sessions, server page requests, crawler response status, and successful verified crawler fetches cannot yet be evaluated.

The report preserves the existing canonical topic routes, content-review gates, no-training policy, and crawler protections. It does not create topics to meet a quota, auto-publish content, grant access based on User-Agent, or characterize general crawler traffic as Tollbit paid traffic.
