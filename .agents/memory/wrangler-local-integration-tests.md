---
name: Wrangler local integration tests
description: Keeping getPlatformProxy-based tests fully local and safe for untrusted pull requests.
---

When a test uses Wrangler's `getPlatformProxy()` with project bindings, explicitly set
`remoteBindings: false`; do not rely on `persist: false` to imply local-only execution.

**Why:** Current Wrangler defaults remote bindings to enabled, which can start a remote
proxy session and require Cloudflare authentication even when D1 persistence is in-memory.

**How to apply:** Use this setting for CI integration tests intended to run on fork pull
requests or any environment without Cloudflare credentials. Retain a separate, explicitly
authorized workflow for tests that deliberately exercise remote resources.