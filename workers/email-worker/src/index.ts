/**
 * Syrabit Email Worker — SendGrid v3 HTTP transport (Task #347)
 *
 * Cloudflare Worker that handles transactional email delivery for syrabit.ai
 * by forwarding to the SendGrid v3 Web API. Resend has been removed; the
 * earlier `cloudflare:email` send_email binding is no longer used because we
 * now standardise on a single transactional provider.
 *
 * POST /email/send     — Send a transactional email
 * POST /email/welcome  — Welcome email to new user
 * POST /email/otp      — OTP verification email
 * POST /email/reset    — Password reset email
 * GET  /email/health   — Worker health check
 *
 * Required secrets (set via `wrangler secret put <NAME>`):
 *   SENDGRID_API_KEY   — SendGrid v3 API key with "Mail Send" full access
 *   BACKEND_AUTH_KEY   — Shared secret authenticating backend → worker
 * Optional:
 *   EMAIL_FROM_ADDRESS — Sender address (default: noreply@syrabit.ai)
 *   EMAIL_FROM_NAME    — Sender display name (default: Syrabit.ai)
 */

interface Env {
  SENDGRID_API_KEY: string;
  BACKEND_AUTH_KEY: string;
  EMAIL_FROM_ADDRESS?: string;
  EMAIL_FROM_NAME?: string;
}

interface EmailPayload {
  to: string;
  subject: string;
  html?: string;
  text?: string;
}

interface WelcomePayload {
  to: string;
  name: string;
  class?: string;
  board?: string;
}

interface OtpPayload {
  to: string;
  name?: string;
  otp: string;
  purpose?: "signup" | "login" | "reset";
}

interface ResetPayload {
  to: string;
  name?: string;
  reset_link: string;
}

// ─── Auth middleware ───────────────────────────────────────────────────────────
function authenticate(request: Request, env: Env): boolean {
  const authHeader = request.headers.get("Authorization") || "";
  const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : "";
  return env.BACKEND_AUTH_KEY ? token === env.BACKEND_AUTH_KEY : true;
}

// ─── HTML templates ──────────────────────────────────────────────────────────
function baseHtml(title: string, bodyHtml: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${title}</title>
  <style>
    body { font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; margin: 0; padding: 0; }
    .wrapper { max-width: 560px; margin: 40px auto; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }
    .header { background: linear-gradient(135deg, #1a237e 0%, #283593 100%); padding: 28px 32px; text-align: center; }
    .header h1 { color: #fff; font-size: 22px; margin: 0; font-weight: 600; letter-spacing: 0.3px; }
    .body { padding: 32px; color: #333; font-size: 15px; line-height: 1.7; }
    .otp-box { background: #e8eaf6; border: 2px dashed #3f51b5; border-radius: 8px; text-align: center; padding: 20px; margin: 24px 0; font-size: 36px; font-weight: 700; letter-spacing: 10px; color: #1a237e; }
    .btn { display: inline-block; background: #3f51b5; color: #fff !important; text-decoration: none; padding: 12px 28px; border-radius: 6px; font-size: 15px; font-weight: 600; margin: 16px 0; }
    .footer { background: #f5f5f5; padding: 18px 32px; text-align: center; font-size: 12px; color: #888; }
    .footer a { color: #3f51b5; text-decoration: none; }
    p { margin: 0 0 14px; }
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>Syrabit.ai</h1>
    </div>
    <div class="body">
      ${bodyHtml}
    </div>
    <div class="footer">
      &copy; ${new Date().getFullYear()} Syrabit Educational Technologies &bull;
      <a href="https://syrabit.ai">syrabit.ai</a> &bull;
      Guwahati, Assam, India<br />
      <small>You received this because you have an account on Syrabit.ai.</small>
    </div>
  </div>
</body>
</html>`;
}

function welcomeHtml(name: string, cls?: string, board?: string): string {
  const greeting = name ? `Hi ${name}!` : "Welcome!";
  const details = cls
    ? `<p>You're registered as a <strong>Class ${cls}</strong> student${board ? ` (${board} board)` : ""}.</p>`
    : "";
  return baseHtml(
    "Welcome to Syrabit.ai",
    `<p>${greeting}</p>
    <p>Welcome to <strong>Syrabit.ai</strong> — your AI-powered study companion for Assam Board (AHSEC / SEBA) exams.</p>
    ${details}
    <p>Here's what you can do:</p>
    <ul>
      <li>Access chapter summaries and important questions</li>
      <li>Chat with our AI tutor in Assamese or English</li>
      <li>Generate practice papers and mock tests</li>
      <li>Listen to audio explanations in your language</li>
    </ul>
    <p><a href="https://syrabit.ai/library" class="btn">Start Learning</a></p>
    <p>If you have any questions, just reply to this email.</p>`,
  );
}

function otpHtml(otp: string, name?: string, purpose?: string): string {
  const purposeLabel =
    purpose === "reset"
      ? "password reset"
      : purpose === "login"
        ? "sign-in"
        : "verification";
  const greeting = name ? `Hi ${name},` : "Hello,";
  return baseHtml(
    "Your OTP — Syrabit.ai",
    `<p>${greeting}</p>
    <p>Here is your <strong>${purposeLabel} OTP</strong> for Syrabit.ai:</p>
    <div class="otp-box">${otp}</div>
    <p>This code is valid for <strong>10 minutes</strong>. Do not share it with anyone.</p>
    <p>If you didn't request this, you can safely ignore this email.</p>`,
  );
}

function resetHtml(reset_link: string, name?: string): string {
  const greeting = name ? `Hi ${name},` : "Hello,";
  return baseHtml(
    "Reset your password — Syrabit.ai",
    `<p>${greeting}</p>
    <p>We received a request to reset your Syrabit.ai password.</p>
    <p><a href="${reset_link}" class="btn">Reset Password</a></p>
    <p>This link expires in <strong>30 minutes</strong>. If you didn't request this, your account is safe — just ignore this email.</p>
    <p style="font-size:12px;color:#888;">If the button doesn't work, copy this URL:<br />${reset_link}</p>`,
  );
}

// ─── SendGrid send helper ────────────────────────────────────────────────────
async function sendEmail(
  env: Env,
  to: string,
  subject: string,
  html: string,
  text: string,
): Promise<void> {
  if (!env.SENDGRID_API_KEY) {
    throw new Error("SENDGRID_API_KEY secret not set on Worker");
  }
  const fromAddress = env.EMAIL_FROM_ADDRESS || "noreply@syrabit.ai";
  const fromName = env.EMAIL_FROM_NAME || "Syrabit.ai";
  const body = {
    personalizations: [{ to: [{ email: to }] }],
    from: { email: fromAddress, name: fromName },
    subject,
    content: [
      ...(text ? [{ type: "text/plain", value: text }] : []),
      { type: "text/html", value: html },
    ],
  };
  const resp = await fetch("https://api.sendgrid.com/v3/mail/send", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.SENDGRID_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (resp.status >= 300) {
    const detail = await resp.text();
    throw new Error(`SendGrid HTTP ${resp.status}: ${detail.slice(0, 200)}`);
  }
}

function jsonResp(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ─── Router ──────────────────────────────────────────────────────────────────
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;

    if (pathname === "/email/health") {
      return jsonResp({
        ok: true,
        worker: "syrabit-email",
        provider: "sendgrid",
        ts: Date.now(),
      });
    }

    if (request.method !== "POST") {
      return jsonResp({ error: "Method Not Allowed" }, 405);
    }

    if (!authenticate(request, env)) {
      return jsonResp({ error: "Unauthorized" }, 401);
    }

    let payload: Record<string, string>;
    try {
      payload = (await request.json()) as Record<string, string>;
    } catch {
      return jsonResp({ error: "Invalid JSON body" }, 400);
    }

    try {
      if (pathname === "/email/send") {
        const { to, subject, html, text } = payload as unknown as EmailPayload;
        if (!to || !subject || (!html && !text)) {
          return jsonResp(
            { error: "Missing required fields: to, subject, html/text" },
            422,
          );
        }
        await sendEmail(env, to, subject, html || text || "", text || "");
        return jsonResp({ ok: true });
      }

      if (pathname === "/email/welcome") {
        const {
          to,
          name,
          class: cls,
          board,
        } = payload as unknown as WelcomePayload;
        if (!to) return jsonResp({ error: "Missing field: to" }, 422);
        const html = welcomeHtml(name, cls, board);
        const text = `Welcome to Syrabit.ai, ${name || "student"}! Visit https://syrabit.ai/library to start learning.`;
        await sendEmail(env, to, "Welcome to Syrabit.ai!", html, text);
        return jsonResp({ ok: true });
      }

      if (pathname === "/email/otp") {
        const { to, name, otp, purpose } = payload as unknown as OtpPayload;
        if (!to || !otp)
          return jsonResp(
            { error: "Missing required fields: to, otp" },
            422,
          );
        const html = otpHtml(otp, name, purpose);
        const text = `Your Syrabit.ai OTP: ${otp} (valid 10 min). Do not share this code.`;
        const subjectLabel =
          purpose === "reset"
            ? "Password Reset"
            : purpose === "login"
              ? "Sign-in"
              : "Verification";
        await sendEmail(
          env,
          to,
          `Your ${subjectLabel} OTP — Syrabit.ai`,
          html,
          text,
        );
        return jsonResp({ ok: true });
      }

      if (pathname === "/email/reset") {
        const { to, name, reset_link } = payload as unknown as ResetPayload;
        if (!to || !reset_link)
          return jsonResp(
            { error: "Missing required fields: to, reset_link" },
            422,
          );
        const html = resetHtml(reset_link, name);
        const text = `Reset your Syrabit.ai password: ${reset_link} (valid 30 min)`;
        await sendEmail(
          env,
          to,
          "Reset your Syrabit.ai password",
          html,
          text,
        );
        return jsonResp({ ok: true });
      }

      return jsonResp({ error: "Not Found" }, 404);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[email-worker] error:", message);
      return jsonResp(
        { error: "Internal server error", detail: message },
        500,
      );
    }
  },
} satisfies ExportedHandler<Env>;
