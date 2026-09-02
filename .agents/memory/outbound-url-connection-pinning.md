---
name: Outbound URL connection pinning
description: Durable SSRF rule for request-derived outbound HTTP targets and redirects.
---

Validating a hostname before an HTTP request is not sufficient. Request-derived fetches must connect only to the public IP addresses returned by the validated lookup, while retaining the original hostname for TLS verification, and must repeat validation and pinning for every redirect.

**Why:** A second DNS lookup inside the HTTP client creates a check-to-use race where an allowed hostname can rebind from a public address to a private, loopback, link-local, or metadata-service address.

**How to apply:** Route request-derived downloads through the shared backend outbound-fetch utility rather than constructing an HTTP client locally. Add exact HTTPS host/path rules as feature-specific validation; keep bounded per-hop validation and pinning enabled.