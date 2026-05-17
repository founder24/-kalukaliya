# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| main    | ✅        |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email **security@syrabit.ai** with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive a response within 48 hours. We follow responsible disclosure — we will work with you to understand and fix the issue before any public disclosure.

## Security Architecture

Syrabit.ai is a bilingual educational platform (English + Assamese) for AHSEC/SEBA students. The security model is described in [`threat_model.md`](threat_model.md).

**Trust boundaries:**
- All frontend input is untrusted; backend enforces auth on every protected route
- Supabase is the sole auth/IdP; backend verifies JWTs via JWKS
- `/api/admin/*` is additionally protected by Cloudflare Access
- Edge worker injects trusted headers; backend rejects client-supplied equivalents
- User-supplied URLs pass through `url_safety.py` allowlist before any fetch

**Security controls in place:**
- GitHub CodeQL (static analysis — Python, JavaScript, TypeScript, Actions)
- Dependabot (weekly automated dependency updates for npm, pip, GitHub Actions)
- Secret scanning + push protection enabled on this repository
- Bandit (Python SAST — CI enforced)
- Branch protection: PRs required, status checks must pass, force-push blocked

## Secrets & Credentials

- **Never commit** `.env`, API keys, tokens, or credentials
- All secrets are managed via Replit Secrets / GitHub Secrets / Azure Key Vault
- GITHUB_TOKEN, JWT_SECRET, ADMIN_JWT_SECRET, MONGO_URL, and all API keys rotate on exposure
- See [`docs/infra/env-vars.md`](docs/infra/env-vars.md) for the full env-var inventory

## Known Accepted Risks

| Risk | Status | Rationale |
|------|--------|-----------|
| MD5/SHA1 for cache keys | Fixed (usedforsecurity=False) | Non-security use; fingerprinting only |
| XML ElementTree (RSS/sitemap) | Accepted | Input from known publishers, not user-controlled |
| Binding to all interfaces | Accepted | Behind Cloudflare/ACA reverse proxy; never direct |
| asyncpg $1/$2 query templates | Accepted | Parameterised queries; string is the template, not user data |

## Dependency Management

- **Python:** `pip audit` + `safety check` in CI; Dependabot weekly
- **JavaScript/TypeScript:** `pnpm audit` in CI; Dependabot weekly
- **GitHub Actions:** all `uses:` references pinned to 40-char commit SHAs; Dependabot bumps them

## Security Scanning

| Tool | Scope | Schedule |
|------|-------|----------|
| GitHub CodeQL | Python, JS/TS, Actions | Every push to main + weekly |
| Bandit | Python backend | Every CI run |
| pnpm audit | Frontend JS/TS | Every CI run |
| Dependabot | npm + pip + GitHub Actions | Weekly (Monday 03:00 UTC) |
| Secret scanning | All files | Every push (push protection on) |

## DPDP Compliance (India)

Syrabit.ai collects data from students in Assam and complies with India's Digital Personal Data Protection Act 2023:
- Data retention enforced via `data_retention.py`
- 7-year archival via AWS Glacier (`glacier-archive.tf`)
- Right to erasure: `routes/admin_archive.py`
