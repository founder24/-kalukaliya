---
name: Tollbit crawler integration
description: User-provided Tollbit crawler identifiers and the project's integration boundary.
---

Do not route crawler requests to `gateway.tollbit.com` by matching raw User-Agent strings. For Cloudflare Enterprise with Logpush, use Tollbit's documented Cloudflare Snippet integration for Agent Site; scope it to the publisher's website hosts and exclude the Tollbit host to prevent loops. Keep training-only crawlers out of the forwarding list; the user wants recurring search/retrieval and user-initiated agents, not one-time training crawlers.

**Why:** the user wants Tollbit monetization without disrupting normal visitors or routing one-time training crawlers. Current Tollbit documentation says Enterprise uses Logpush for analytics and a Snippet for Agent Site; the supported Snippet uses a curated User-Agent list, not the raw-UA gateway approach the user rejected.

**How to apply:** Confirm the current Cloudflare/Tollbit integration instructions before production changes. Treat the following as a user-provided reference list, not as independently verified or exhaustive, and select only agents consistent with the user's recurring search/retrieval goal:

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
