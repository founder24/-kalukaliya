# Syrabit Agent Readiness evidence

**Reviewed:** 2026-10-24 (UTC)  
**Public site scanned:** `https://syrabit.ai`  
**Scanner target after redirect:** `https://syrabit.ai/library`

## Findings at a glance

The fresh Cloudflare Agent Readiness scanner run reported **Level 4/5 — Agent-Integrated**. It passed the public-content discovery and access checks already implemented by Syrabit. It did not pass DNS-AID, OAuth discovery, OAuth Protected Resource, Auth.md agent registration, MCP Server Card, or Personal Agent Protocol checks. Those failures do not justify publishing records for services Syrabit does not provide.

The Cloudflare URL Scanner report was also run and finished successfully as an **unlisted** scan. Its report page shows an Agent Readiness tab, but the public URL Scanner result API does not include that tab's check breakdown. The tab was not opened in this capture. The task's starting context reported a dashboard score of Level 4/5; the fresh detailed score below comes from the separate live Agent Readiness scanner and is not represented as a verified dashboard-tab result.

## Cloudflare URL Scanner report

| Item | Evidence |
| --- | --- |
| Scan ID | `29f7f969-5625-46aa-9b21-5ce469d3f5ec` |
| Submitted | 2026-10-24 12:27:59 UTC |
| Finished | 2026-10-24 12:28:23 UTC |
| Status / visibility | Finished / Unlisted |
| Submitted URL / final page | `https://syrabit.ai` → `https://syrabit.ai/library` |
| Report | [Cloudflare Radar URL Scanner report](https://radar.cloudflare.com/scan/29f7f969-5625-46aa-9b21-5ce469d3f5ec) |
| Readiness option | Requested with `agentReadiness: true` |

The report page rendered the **Agent Readiness** tab alongside Summary, Security, Network, Behavior, Content, Indicators, and Related. The API result exposed scan metadata and network results, but no Agent Readiness score or checks. The separately queried public scanner returned its detailed readiness breakdown at 2026-10-24 12:26:48 UTC.

## Live Agent Readiness scanner findings

The public scanner returned **Level 4 — Agent-Integrated**, targeting `/library` after the root URL redirected there. This run had 23 displayed checks: 9 pass, 6 fail, 7 neutral, and 1 unable to check. Statuses below preserve the scanner's wording and distinguish an unsupported capability from one that is not applicable.

| Scanner category | Check | Result | Finding and interpretation |
| --- | --- | --- | --- |
| Discoverability | `robots.txt` | Pass | Valid `robots.txt` returned with 200. |
| Discoverability | Sitemap | Pass | Valid `sitemap-index.xml` discovered from `robots.txt`. |
| Discoverability | Link headers | Pass | Found `api-catalog`, `service-desc`, and `service-doc` relations. |
| Discoverability | DNS-AID | Fail | No DNS-AID entrypoint records. DNSSEC is disabled; do not add DNS-AID records. |
| Content accessibility | Markdown negotiation | Pass | The target page returned `text/markdown; charset=utf-8` for `Accept: text/markdown`. |
| Bot access control | AI bot rules | Pass | Explicit AI bot rules were found. |
| Bot access control | Content Signals | Pass | Found `search=yes`, `ai-input=yes`, `ai-train=no`. |
| Bot access control | Web Bot Auth | Neutral | The well-known path returned HTML, treated as informational by the scanner. Syrabit does not currently offer this protocol. |
| Protocol discovery | API Catalog | Pass | One public API listed with the expected linkset content type. |
| Protocol discovery | OAuth/OIDC discovery | Fail | No authorization server metadata; Syrabit has no agent OAuth server. |
| Protocol discovery | OAuth Protected Resource | Fail | No protected-resource metadata; the published content search is public and read-only. |
| Protocol discovery | Auth.md registration | Fail | `/auth.md` exists, but correctly documents that there is no agent registration flow. |
| Protocol discovery | MCP Server Card | Fail | The scanner interpreted `/.well-known/mcp.json` as a card and found no `serverInfo.name` or `name`. Syrabit has no MCP server; this is not a reason to invent a card. |
| Protocol discovery | A2A Agent Card | Neutral | Excluded by the scanner's default configuration; no A2A service is published. |
| Protocol discovery | Agent Skills | Pass | Valid discovery index, version 0.2.0, with one skill. |
| Protocol discovery | WebMCP | Unable to check | The scanner's browser execution context was destroyed during navigation; this is not a verified pass or fail. |
| Protocol discovery | ARD | Pass | Found one valid agentic resource in `/.well-known/ai-catalog.json`. |
| Protocol discovery | Personal Agent Protocol (Poppy) | Fail | No Poppy manifest; the unimplemented path returned the site's HTML fallback. |
| Commerce | x402, MPP, UCP, ACP, AP2 | Neutral | Scanner classified Syrabit as not a commerce site; these checks are not applicable. |

The scanner's stated next-level requirements were **Auth.md agent registration** and an **MCP Server Card**. Both depend on services Syrabit does not currently provide, so they remain incomplete rather than being represented by placeholders.

## DNS-AID eligibility

Read-only checks confirmed that `syrabit.ai` is active on Cloudflare nameservers. Cloudflare's zone DNSSEC API reported **`status: disabled`**. Independent DNS-over-HTTPS queries found no apex DS or DNSKEY records, and the scanner found no DNS-AID records at its tested entrypoints.

Cloudflare documents DNSSEC support, but support by the provider is not proof that it is enabled for this zone. DNS-AID remains incomplete. No DNS records, DNSSEC settings, nameservers, or registrar settings were changed. Enabling DNSSEC or publishing DNS-AID records requires separate approval and a verified endpoint that serves a real discovery resource.

## Policy and scope

- No changes were made to `robots.txt`, edge bot enforcement, content permissions, routing, or authentication.
- Search and answer crawlers remain allowed according to the existing policy; training-only crawlers such as GPTBot, ClaudeBot, CCBot, and Google-Extended remain disallowed.
- The public content API remains read-only and unauthenticated. Student and staff website sessions are not agent API credentials.
- No MCP or A2A service, OAuth metadata, or DNS-AID record was published.
- Cloudflare AEO Performance remains unavailable; the dashboard reports **“Coming soon.”** It is not scored here.

## Decision

No protocol, DNS, bot-policy, routing, or authentication change was appropriate for this task: the supported public-content capabilities passed, and the remaining meaningful failures require services or DNS prerequisites that do not exist. The fresh scanner evidence and these limits are recorded here without claiming a 5/5 result.

## Sources

- [Cloudflare Agent Readiness overview](https://blog.cloudflare.com/agent-readiness/)
- [Cloudflare URL Scanner API](https://developers.cloudflare.com/radar/investigate/url-scanner/)
- [Cloudflare DNSSEC documentation](https://developers.cloudflare.com/dns/dnssec/)
- [DNS-AID Internet-Draft](https://www.ietf.org/archive/id/draft-mozleywilliams-dnsop-dnsaid-02.html)
- [Live Agent Readiness scanner](https://isitagentready.com/)
