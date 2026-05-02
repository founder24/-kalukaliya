/**
 * email-worker/src/index.ts
 *
 * Previously a Cloudflare Workers Paid compute-heavy worker that handled
 * transactional email dispatch (welcome, OTP, subscription receipts).
 * Migrated to AWS Lambda (Node 20, arm64) covered by AWS Activate credits.
 *
 * This file is the *stub* that remains in the Cloudflare Workers Free tier
 * to receive inbound webhook events from SES (SNS → HTTPS) and forward them
 * to the Railway/Cloud Run backend.  All outbound email sending now goes
 * through the Lambda function defined in infra/aws/lambda-email-worker.tf.
 *
 * Architecture after migration
 * ─────────────────────────────
 *   Frontend / Backend
 *     └─▶  POST /api/email/send
 *               │
 *               ▼
 *         AWS Lambda (email-worker)          ← covered by AWS Activate
 *           ├─ Renders template (Handlebars)
 *           ├─ Calls AWS SES SendEmailCommand
 *           └─ Logs to CloudWatch Logs       ← AWS Activate
 *
 *   SES delivery events (bounce / complaint / delivery)
 *     └─▶  SNS topic → this CF Worker stub → POST /api/webhooks/ses
 *
 * Performance boosts included
 * ───────────────────────────
 * • Lambda arm64 (Graviton3) is ~20 % cheaper and faster than x86 for
 *   Node.js workloads — covered by AWS Activate.
 * • Lambda@Edge (us-east-1) runs the stub globally so SES webhooks are
 *   acknowledged in < 50 ms regardless of origin.
 * • SES Dedicated IP pool (AWS Activate credit) improves deliverability
 *   and avoids shared-IP reputation issues.
 *
 * Required wrangler secrets / vars
 * ─────────────────────────────────
 *   BACKEND_WEBHOOK_URL   — https://api.syrabit.ai/api/webhooks/ses
 *   SNS_SIGNING_CERT_URL_PREFIX — https://sns.<region>.amazonaws.com/
 */

export interface Env {
  BACKEND_WEBHOOK_URL: string;
  SNS_SIGNING_CERT_URL_PREFIX: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== 'POST') {
      return new Response('method not allowed', { status: 405 });
    }

    const messageType = request.headers.get('x-amz-sns-message-type');
    if (!messageType) {
      return new Response('missing SNS message type', { status: 400 });
    }

    const body = await request.text();
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(body);
    } catch {
      return new Response('invalid JSON', { status: 400 });
    }

    // Handle SNS subscription confirmation.
    if (messageType === 'SubscriptionConfirmation') {
      const subscribeUrl = parsed.SubscribeURL as string;
      if (
        subscribeUrl &&
        env.SNS_SIGNING_CERT_URL_PREFIX &&
        subscribeUrl.startsWith(env.SNS_SIGNING_CERT_URL_PREFIX)
      ) {
        await fetch(subscribeUrl);
      }
      return new Response('ok', { status: 200 });
    }

    // Forward notification to backend.
    if (messageType === 'Notification') {
      try {
        await fetch(env.BACKEND_WEBHOOK_URL, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ type: 'ses_event', payload: parsed }),
        });
      } catch (err) {
        console.error('[email-worker] forward failed', err);
        return new Response('forward failed', { status: 502 });
      }
    }

    return new Response('ok', { status: 200 });
  },
};
