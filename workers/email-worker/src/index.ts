/**
 * Syrabit Email Worker — RETIRED (Task #556, 2026-05-07)
 *
 * SES is now the SOLE transactional email path. The previous
 * Cloudflare Worker → SendGrid v3 transport (Task #347) is fully
 * decommissioned: no provider abstraction, no fallback, no
 * break-glass. All transactional sends now flow through the FastAPI
 * backend's `email_templates._send_via_ses` helper (Amazon SES,
 * `us-east-1` primary / `ap-south-1` regional flip via the
 * `SES_REGION` env var).
 *
 * Bulk / digest / marketing fan-out goes through the separate
 * Cloudflare Email Workers `bulk-email` Worker (see
 * `workers/bulk-email/`) which the FastAPI side calls via
 * `bulk_email.send_bulk`.
 *
 * This stub stays deployed at the legacy `syrabit.ai/email/*` route
 * pattern only to keep the route record valid until the next
 * `wrangler.toml` cleanup PR removes the binding entirely. Every
 * request returns HTTP 410 Gone so any stale caller fails loud
 * instead of silently dropping mail (V4 §12 — no silent fallbacks).
 */

interface Env {
  // Worker takes no email-provider secrets any more. The shared
  // `BACKEND_AUTH_KEY` binding is kept (and ignored) so wrangler
  // doesn't refuse to deploy if the legacy secret is still set.
  BACKEND_AUTH_KEY?: string;
}

function jsonResp(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const RETIRED_PAYLOAD = {
  ok: false,
  error: "email-worker-retired",
  detail:
    "Task #556 — SES is the sole transactional email path. Call the " +
    "backend SES helper directly (POST /api/admin/diagnostics/email-smoke " +
    "for ops smoke; transactional flows are wired through " +
    "email_templates._send_via_ses). Bulk / digest fan-out goes through " +
    "the workers/bulk-email Worker (bulk_email.send_bulk).",
} as const;

export default {
  async fetch(request: Request, _env: Env): Promise<Response> {
    const { pathname } = new URL(request.url);
    if (pathname === "/email/health") {
      return jsonResp({
        ok: true,
        worker: "syrabit-email",
        status: "retired",
        ts: Date.now(),
      });
    }
    return jsonResp(RETIRED_PAYLOAD, 410);
  },
} satisfies ExportedHandler<Env>;
