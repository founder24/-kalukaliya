const WINDOW_MS = 60_000;
const REQUEST_LIMIT = 20;

function unavailableResponse(): Response {
  return Response.json({
    detail: 'Student search is temporarily unavailable',
    error_code: 'student_search_rate_limit_unavailable',
  }, { status: 503 });
}

/** Keep paid search calls behind an atomic, IP-hashed D1 rate limit. */
export async function enforceStudentSearchRateLimit(
  db: D1Database,
  request: Request,
  nowMs = Date.now(),
): Promise<Response | null> {
  const clientIp = request.headers.get('CF-Connecting-IP')?.trim();
  if (!clientIp) return unavailableResponse();

  const windowKey = Math.floor(nowMs / WINDOW_MS);
  const resetAt = (windowKey + 1) * WINDOW_MS;
  const nowSeconds = Math.floor(nowMs / 1000);
  const expiresAt = Math.ceil(resetAt / 1000) + 60;
  try {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(clientIp));
    const ipHash = Array.from(new Uint8Array(digest))
      .map(byte => byte.toString(16).padStart(2, '0'))
      .join('');
    const bucketKey = `student-search:${ipHash}:${windowKey}`;
    const result = await db.prepare(`
      INSERT INTO auth_rate_limits (bucket_key, request_count, expires_at, updated_at)
      VALUES (?, 1, ?, ?)
      ON CONFLICT(bucket_key) DO UPDATE SET
        request_count = MIN(auth_rate_limits.request_count + 1, ?),
        expires_at = excluded.expires_at,
        updated_at = excluded.updated_at
      RETURNING request_count
    `).bind(bucketKey, expiresAt, nowSeconds, REQUEST_LIMIT + 1)
      .first<{ request_count: number }>();

    if (!result || !Number.isSafeInteger(result.request_count)) throw new Error('Invalid D1 counter');
    if (result.request_count <= REQUEST_LIMIT) return null;
    return Response.json({
      detail: 'Too many searches. Please wait a minute and try again.',
      error_code: 'student_search_rate_limited',
    }, {
      status: 429,
      headers: {
        'X-RateLimit-Limit': String(REQUEST_LIMIT),
        'X-RateLimit-Remaining': '0',
        'X-RateLimit-Reset': String(Math.floor(resetAt / 1000)),
        'Retry-After': String(Math.max(1, Math.ceil((resetAt - nowMs) / 1000))),
      },
    });
  } catch (error) {
    console.error('[student-search-rate-limit] D1 storage unavailable:', error);
    return unavailableResponse();
  }
}
