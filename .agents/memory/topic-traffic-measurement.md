---
name: Topic traffic measurement
description: Evidence boundaries for human discovery and verified crawler topic-page reporting.
---

Treat consented browser sessions as a discovery proxy, not a verified-human count. Keep organic-search and AI-referral entries, site sessions, browser page views, Search Console clicks/impressions, and server requests separate. Full-window targets require known source coverage; otherwise show N/A rather than zero. Count a crawler fetch only with authoritative identity plus final successful HTML evidence; never infer verification or paid Tollbit access from a User-Agent or aggregate bot total.

**Why:** These measurements come from different systems and have different coverage and identity guarantees. Combining them or filling gaps with zero would create unsupported traffic claims.

**How to apply:** For topic-level reporting, join against the versioned canonical published-topic inventory, preserve exclusions, expose source freshness and setup gaps, and keep unverified or incomplete crawler outcomes outside successful-fetch totals.
