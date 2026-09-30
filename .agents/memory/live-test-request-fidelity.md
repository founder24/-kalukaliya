---
name: Live test request fidelity
description: Ensure shell-based security probes send the intended URL and keep redirect checks bounded to the configured origin.
---

For curl-based path-traversal probes, use `--path-as-is`; otherwise curl may remove `../` segments before sending the request, so a 404 can test a different route rather than prove the server blocked traversal.

For redirect checks that require same-origin destinations, do not follow redirects blindly with `curl -L`. Resolve each `Location` against the current URL, compare scheme, host, and effective port with the configured origin, then request the next hop only after it matches. Bound the hop count and make required destination-validation failures critical.

Avoid `echo "$body" | grep -q` for large response bodies under `pipefail`: grep may stop reading early, and a producer that receives SIGPIPE can make a successful match appear to fail. Use an in-shell match for known markers or a checker that consumes the complete input.

**Why:** Curl performs URL normalization and redirect following client-side; shell pipelines also report the producer's status under `pipefail`. These behaviors can make a probe appear to validate the wrong route, miss a valid marker, or contact an unintended origin.

**How to apply:** Use this for production shell tests that exercise traversal paths, large response bodies, or redirect destinations. Verify with local mocks that raw paths reach the server, large HTML is recognized, and off-origin targets receive no request.