# Workspace — Syrabit.ai

## Overview

Syrabit.ai is an AI-powered educational platform designed for students in Assam, India, focusing on AHSEC Class 11/12 and Degree curricula. The platform provides localized learning resources across 55 subjects, leveraging AI for content generation, syllabus management, and SEO optimization. Its core purpose is to deliver personalized, accessible, and high-quality educational content through chapter-level RAG chunks and a comprehensive admin panel, aiming to be an affordable, AI-first learning experience with significant market potential.

## User Preferences

I prefer iterative development with clear communication on major changes. I value detailed explanations for complex features and architectural decisions. Please ensure that the development process prioritizes modularity and maintainability.

## System Architecture

The project is structured as a pnpm workspace monorepo, combining a React + Vite frontend with a FastAPI Python backend.

**Frontend Architecture:**
- **UI/UX:** Built with React, Vite, React Router, and Tailwind CSS, following a mobile-first, light-only theme design.
- **Admin Panel:** A robust Content Management System (CMS) for managing content, blogs, SEO, QA, and system intelligence.
- **SEO & Bot Management:** Features bot-aware pre-rendering, manages `robots.txt`, `sitemap.xml`, and integrates with IndexNow. It includes AI plugin discovery and RSS feeds for bot discovery.
- **PWA:** Offers offline capabilities through a multi-cache service worker.
- **Content & Localization:** Supports bilingual content (English and Assamese) via UI toggles, with a library page featuring subject cards, lesson pages, reading progress, and sticky Table of Contents.
- **Analytics:** Implements multi-source analytics for Core Web Vitals, including Cloudflare, GA4, and server-side tracking.

**Backend Architecture:**
- **Modular Design:** Utilizes an app factory pattern with shared and route modules.
- **AI Integration:** All AI calls are routed through Cloudflare Workers AI via a CF AI Gateway, using models for embeddings, vision/OCR, translation, and content generation. The `vertex_chat` slug specifically resolves to Workers AI `llama-3.3-70b-instruct-fp8-fast`.
- **Content Pipeline:** Supports parallel generation of notes, MCQs, and flashcards with detailed prompts, including auto-detection of thin chapters and quality gates for content healing.
- **Admin Analytics:** Provides a dashboard for RAG telemetry, chat latency, user counts, content heatmaps, and a historical alert log.
- **Educational Features:** Processes PYQ PDFs via Gemini Vision OCR for SEO-optimized HTML and includes a Syllabus Embedder for generating chapter/topic embeddings stored in Cloudflare Vectorize. An in-app educational browser with grounded AI chat and content filtering is also supported.
- **Monetization:** Implements a credit-based usage model supporting free, starter, and pro plans.
- **Security & Privacy:** Features ASGI-native security headers, prompt safety measures, bot UA monitoring, automated IP blocking, and DPDP Act consent tracking.
- **Performance:** Optimizations include bounded content caching, efficient JWT decoding, thread pooling, MongoDB compound indexes, hierarchy caching, AsyncOpenAI client pooling, and parallelized chat pre-processing.
- **Unified Log Explorer:** Centralizes logs from frontend, edge-proxy, and backend into a single MongoDB collection for comprehensive monitoring and tracing.
- **Supply-Chain Hardening:** Employs GitHub Actions best practices for security, including SHA-pinned actions and workflow-security linting.
- **LLM Provider Benchmarking:** Includes a script for benchmarking LLM providers based on various metrics like TTFT, total latency, and tokens per second across different prompt suites.

## External Dependencies

- **Databases:** MongoDB, PostgreSQL, Cloudflare D1, Cloudflare Vectorize.
- **Authentication:** Supabase Auth for email/password and Google OAuth, issuing custom httpOnly session cookies and JWTs.
- **Caching:** Cloudflare AI Gateway (LLM cache) and Cloudflare edge worker KV bindings. Neural Mesh provides multi-tier caching and inflight deduplication.
- **LLM Providers:** Cloudflare Workers AI (primary for chat, content generation, translation, embeddings), with Gemini, Groq, Cerebras, and OpenRouter as fallbacks. Pinecone is used for inference (embeddings and reranking).
- **Translation:** Sarvam `translate:v1` (primary for Indic languages), with Gemini as fallback, and an Assamese translation cache in Upstash Redis.
- **Payment Gateways:** Razorpay (INR) and Stripe (USD).
- **Email Service:** Cloudflare Email Worker (primary) with Resend as a fallback.
- **UI/UX Frameworks:** React, Vite, React Router, Tailwind CSS.
- **ORM:** Drizzle ORM.
- **API Framework:** FastAPI.
- **Schema Validation:** Zod.
- **API Codegen:** Orval.
- **Build Tools:** esbuild, pnpm, Docker.
- **Production Deployment:** A hybrid architecture leveraging FastAPI on Railway, Cloudflare Worker edge proxy, and frontend on Cloudflare Pages.
- **Cloudflare Services (Enterprise):** Cache Purge API, Worker Cache API, IndexNow, Vectorize, D1, KV namespaces, Smart Placement, Workers Observability, Workers Logpush, Enterprise WAF. Analytics Engine for request metrics and RateLimiter Durable Object for robust rate limiting.
- **Observability:** Firebase Performance Monitoring for RUM and Core Web Vitals, OpenTelemetry for distributed tracing to Cloud Trace.
- **GCP Services:**
    - **API-Key Only:** Knowledge Graph Search, PageSpeed Insights, Fact Check Tools, Cloud Natural Language, Web Risk, Books API.
    - **SA-Gated (via `GOOGLE_APPLICATION_CREDENTIALS_JSON`):** Cloud Scheduler, Cloud Tasks, Web Security Scanner, Discovery Engine. These are integrated with OIDC authentication and Slack notifications for operational workflows.