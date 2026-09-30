---
name: Live test request fidelity
description: Ensure shell-based security probes send the intended URL and keep redirect checks bounded to the configured origin.
---

For curl-based path-traversal probes, use `--path-as-is`; otherwise curl may remove `../` segments before sending the request, so a 404 can test a different route rather than prove the server blocked traversal.

For redirect checks that require same-origin destinations, do not follow redirects blindly with `curl -L`. Resolve each `Location` against the current URL, compare scheme, host, and effective port with the configured origin, then request the next hop only after it matches. Bound the hop count and make required destination-validation failures critical.

**Why:** Curl performs URL normalization and redirect following client-side. Unbounded or automatic behavior can make a probe appear to validate server handling while it actually requests a different route or origin.

**How to apply:** Use this for production shell tests that exercise traversal paths or redirect destinations. Verify with local mocks that raw paths reach the server and off-origin targets receive no request.