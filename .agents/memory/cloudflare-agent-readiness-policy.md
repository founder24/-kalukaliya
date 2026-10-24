---
name: Cloudflare Agent Readiness policy
description: Syrabit's readiness metadata must describe only real, verified public capabilities.
---

For Syrabit's Cloudflare Agent Readiness work, publish accurate discovery metadata only for services that actually exist. Do not claim OAuth, MCP, A2A, WebMCP, or DNS-AID capabilities unless they are implemented and verified. The owner chose safe metadata changes and prefers unsupported checks to remain unscored rather than be represented by placeholder endpoints.

**Why:** the user selected truthful metadata-only improvements and explicitly deferred unsupported protocol integrations.

**How to apply:** Before changing readiness files or scores, verify each linked endpoint and its response. Ask before adding new public auth or agent services; never create a card that points to a SPA fallback or redirect instead of a working protocol endpoint.
