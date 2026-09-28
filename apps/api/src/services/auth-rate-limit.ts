const AUTH_RATE_LIMIT_WINDOW_MS = 60_000;

export const AUTH_RATE_LIMITS = {
  login: 10,
  signup: 5,
  'reset-password-request': 3,
  'reset-password-confirm': 10,
  'admin-login': 5,
} as const;

export type AuthRateLimitRoute = keyof typeof AUTH_RATE_LIMITS;

function unavailableResponse(): Response {
  return Response.json({
    detail: 'Authentication service temporarily unavailable',
    error_code: 'rate_limit_storage_unavailable',
  }, { status: 503 });
}

/**
 * Enforce authentication limits in the API Worker itself. The API Worker has
 * a public workers.dev origin used by internal workflows, so callers can
 * bypass the edge Worker; D1 must enforce the same policy at the route owner.
 */
export async function enforceAuthRateLimit(
  db: D1Database,
  request: Request,
  route: AuthRateLimitRoute,
  nowMs = Date.now(),
): Promise<Response | null> {
  const clientIp = request.headers.get('CF-Connecting-IP')?.trim();
  if (!clientIp) return unavailableResponse();

  const limit = AUTH_RATE_LIMITS[route];
  const windowKey = Math.floor(nowMs / AUTH_RATE_LIMIT_WINDOW_MS);
  const resetAt = (windowKey + 1) * AUTH_RATE_LIMIT_WINDOW_MS;
  const nowSeconds = Math.floor(nowMs / 1000);
  const expiresAt = Math.ceil(resetAt / 1000) + 60;

  try {
    const digest = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(clientIp),
    );
    const ipHash = Array.from(new Uint8Array(digest))
      .map(byte => byte.toString(16).padStart(2, '0'))
      .join('');
    const bucketKey = `auth:${route}:${ipHash}:${windowKey}`;

    const result = await db.prepare(`
      INSERT INTO auth_rate_limits (bucket_key, request_count, expires_at, updated_at)
      VALUES (?, 1, ?, ?)
      ON CONFLICT(bucket_key) DO UPDATE SET
        request_count = MIN(auth_rate_limits.request_count + 1, ?),
        expires_at = excluded.expires_at,
        updated_at = excluded.updated_at
      RETURNING request_count
    `).bind(bucketKey, expiresAt, nowSeconds, limit + 1)
      .first<{ request_count: number }>();

    if (!result || !Number.isSafeInteger(result.request_count)) {
      throw new Error('D1 returned an invalid authentication counter');
    }

    if (result.request_count <= limit) return null;

    return Response.json({
      detail: 'Too many authentication attempts',
      error_code: 'auth_rate_limited',
    }, {
      status: 429,
      headers: {
        'X-RateLimit-Limit': String(limit),
        'X-RateLimit-Remaining': '0',
        'X-RateLimit-Reset': String(Math.floor(resetAt / 1000)),
        'Retry-After': String(Math.max(1, Math.ceil((resetAt - nowMs) / 1000))),
      },
    });
  } catch (error) {
    console.error('[auth-rate-limit] D1 storage unavailable:', error);
    return unavailableResponse();
  }
}