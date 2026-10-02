---
name: Cloudflare analytics workflow permissions
description: Permission boundaries and diagnosis for Cloudflare analytics and WAF audits.
---

**Event metadata:** `firewallEventsAdaptive`'s `metadata { key, value }` can expose `ruleset_version`, `version`, `score_total`, and `score_rules` even when these are not top-level fields. Map the opaque IDs in `score_rules` against the event's managed ruleset version to resolve contributing rule descriptions/actions; this still does not identify the matched request variable or value.

Cloudflare GraphQL access is scoped by dataset and field. The live zone type is lowercase `zone`; `firewallEventsAdaptive` exposes `wafAttackScore`, `wafAttackScoreClass`, and separate attack-class scores, not the older `score_total` / `score_rules` names. `httpRequestsAdaptive` can retrieve an individual request by `rayName` and expose edge response status and security action/source. Signature fields such as `wafRequestSignatureCategories` and `wafRequestSignatureRefs` may appear in schema introspection but still return an authorization error under Zone Analytics Read, and can remain unavailable even with additional WAF read scopes.

In this environment, adding `Account WAF:Read` enabled zone/account ruleset detail and version reads; `Account Rulesets:Read` plus `Zone WAF:Read` alone returned 403. Managed ruleset responses expose IDs, refs, descriptions, and actions, but not expressions or matched variables. The event description `949110: Inbound Anomaly Score Exceeded` identifies the managed blocking threshold, not which underlying signature contributed.

**Why:** Schema introspection and access to event rows do not imply access to signature details or ruleset configuration. Cloudflare's WAF attack-score classifier is separate from the managed-rule anomaly score, so a request classified as clean can still be blocked by the managed threshold. `firewallEventsAdaptive` rejects query windows wider than 4w3d.

**How to apply:** Keep audit credentials separate from deployment credentials and restrict them to the target zone plus its account-level ruleset resource. Use `httpRequestsAdaptive` for a Ray-specific HTTP status and `firewallEventsAdaptive` for matched security actions; keep event queries within 4w3d. If GraphQL signature fields remain denied, use redacted Security Events Additional logs to identify the matched variable/key. Do not bypass the threshold before identifying the contributing signatures and request field.