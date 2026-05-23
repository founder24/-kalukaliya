# Architecture Decision Records

## ADR-001: Vertex AI (Gemini) for English Content

**Status**: Accepted  
**Context**: Need a high-quality LLM for English educational content generation.  
**Decision**: Use Google Vertex AI with Gemini 1.5 Pro.  
**Rationale**: Best-in-class English reasoning, strong instruction following, supports streaming, competitive pricing for education use case. Google Cloud integration with Azure via service account credentials.

## ADR-002: Sarvam AI for Assamese Content

**Status**: Accepted  
**Context**: Need native Indic language support for Assamese students.  
**Decision**: Use Sarvam AI with OpenHathi model.  
**Rationale**: Purpose-built for Indian languages with native Assamese understanding. Avoids translation artifacts. OpenAI-compatible API simplifies integration. Better cultural context than translated English models.

## ADR-003: Cloudflare Edge Worker

**Status**: Accepted  
**Context**: Need edge-level protection and routing before requests hit origin.  
**Decision**: Use Cloudflare Workers as the edge gateway.  
**Rationale**: Bot protection (Turnstile CAPTCHA), CORS handling at edge, rate limiting close to user, global edge network (300+ PoPs), sub-10ms overhead. Eliminates need for separate WAF/CDN.

## ADR-004: Azure Container Apps for Backend

**Status**: Accepted  
**Context**: Need managed container hosting with auto-scaling.  
**Decision**: Use Azure Container Apps.  
**Rationale**: Native auto-scaling (scale-to-zero capable), managed infrastructure (no K8s overhead), KeyVault integration for secrets, India Central region for data residency, built-in health probes and traffic splitting for blue/green deploys.

## ADR-005: Cloudflare Pages for Frontend

**Status**: Accepted  
**Context**: Need fast, globally-distributed frontend hosting.  
**Decision**: Use Cloudflare Pages for the React/Vite SPA.  
**Rationale**: Global CDN with edge caching, instant deploys via Git integration, preview deployments per PR, free SSL, integrated with existing Cloudflare DNS. Zero-config builds for Vite projects.

## ADR-006: MongoDB Atlas for Data Storage

**Status**: Accepted  
**Context**: Need a primary database for user data, chat history, and subscriptions.  
**Decision**: Use MongoDB Atlas (M10 cluster).  
**Rationale**: Flexible schema ideal for evolving chat message structures, native async driver support (Motor/Beanie ODM), VNet peering with Azure, automatic backups, India region availability. Chat history benefits from document model (messages nested in conversations).

## ADR-007: Upstash Redis for Caching and Rate Limiting

**Status**: Accepted  
**Context**: Need a fast key-value store for rate limiting, session caching, and real-time counters.  
**Decision**: Use Upstash Redis (serverless).  
**Rationale**: HTTP-based (works in serverless/edge environments), pay-per-request pricing, global replication, no connection pool management needed. Used for rate limiting (token bucket), auth attempt counters, and subscription cache.
