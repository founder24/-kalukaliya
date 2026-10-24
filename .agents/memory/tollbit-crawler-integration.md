---
name: Tollbit crawler integration
description: User-provided Tollbit crawler identifiers and the project's integration boundary.
---

Do not route crawler requests to `gateway.tollbit.com` by matching raw User-Agent strings. Use a Tollbit-supported integration path and verify its current requirements in official documentation before changing production routing or analytics.

**Why:** the user stated that Tollbit integration should use server-side log ingestion (such as Logpush) or its supported CDN/DNS verification flow, rather than forwarding requests based on spoofable User-Agent headers.

**How to apply:** Treat the following as a user-provided reference list, not as independently verified or exhaustive. Confirm it against current Tollbit documentation before configuring policies:

- AgentNativeSearchBot
- AI2Bot
- AionBot
- AIWebIndex
- Amazonbot
- Amzn-SearchBot
- Amzn-User
- anthropic-ai
- atevesearchsourceurldiscovery
- azureai-searchbot
- Bravebot
- Bytespider
- CCBot
- chatgpt-user
- claude-code
- claude-searchbot
- claude-user
- claude-web
- claude/
- claudebot
- cloudflarebrowserrenderingcrawler
- cohere-ai
- oai-searchbot
