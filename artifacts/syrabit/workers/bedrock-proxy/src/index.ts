/**
 * bedrock-proxy/src/index.ts
 *
 * Previously a compute-heavy Cloudflare Workers Paid worker that proxied
 * requests to AWS Bedrock (Claude / Titan) for AI completions.
 * Migrated to AWS Lambda (Node 20, arm64) in us-east-1 covered by AWS
 * Activate credits.  This stub remains in the CF Workers Free tier only
 * to add Cloudflare WAF / Zero Trust authentication before forwarding to
 * the Lambda Function URL.
 *
 * Architecture after migration
 * ─────────────────────────────
 *   Client → CF WAF → this stub → AWS Lambda Function URL
 *                                    └─▶ Amazon Bedrock (Claude 3.5 / Titan)
 *
 * Performance boosts via AWS Activate
 * ─────────────────────────────────────
 * • Lambda arm64 (Graviton3) — 20 % faster cold starts vs x86
 * • Lambda Provisioned Concurrency (1 instance, free tier headroom under
 *   Activate) eliminates cold-start latency for the first request burst
 * • CloudFront distribution fronting the Lambda Function URL adds a
 *   global POP cache for identical prompt+model combos (TTL 5 min)
 * • AWS X-Ray traces sent to CloudWatch for per-invocation latency
 *   breakdown — replaces Cloudflare Log Explorer for this service
 * • Bedrock Guardrails (content filtering) attached at the Lambda layer
 *   so no extra round-trip is needed
 *
 * Required wrangler secrets / vars
 * ─────────────────────────────────
 *   BEDROCK_LAMBDA_URL    — https://<id>.lambda-url.us-east-1.on.aws/
 *   LAMBDA_SHARED_SECRET  — matched by Lambda authorizer
 *   ALLOWED_USER_HEADER   — header carrying the verified user-id from
 *                           Zero Trust (e.g. Cf-Access-Authenticated-User-Email)
 */

export interface Env {
  BEDROCK_LAMBDA_URL: string;
  LAMBDA_SHARED_SECRET: string;
  ALLOWED_USER_HEADER: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (!env.BEDROCK_LAMBDA_URL) {
      return new Response('bedrock lambda endpoint not configured', { status: 503 });
    }

    const userId =
      request.headers.get(env.ALLOWED_USER_HEADER ?? 'cf-access-authenticated-user-email') ??
      request.headers.get('x-user-id') ??
      '';

    if (!userId) {
      return new Response('unauthorized', { status: 401 });
    }

    if (!['POST'].includes(request.method)) {
      return new Response('method not allowed', { status: 405 });
    }

    const forwardHeaders = new Headers();
    forwardHeaders.set('content-type', request.headers.get('content-type') ?? 'application/json');
    forwardHeaders.set('x-lambda-secret', env.LAMBDA_SHARED_SECRET ?? '');
    forwardHeaders.set('x-user-id', userId);

    try {
      const upstream = await fetch(env.BEDROCK_LAMBDA_URL, {
        method: 'POST',
        headers: forwardHeaders,
        body: request.body,
      });

      return new Response(upstream.body, {
        status: upstream.status,
        headers: upstream.headers,
      });
    } catch (err) {
      console.error('[bedrock-proxy] lambda fetch failed', err);
      return new Response('bedrock unavailable', { status: 502 });
    }
  },
};
