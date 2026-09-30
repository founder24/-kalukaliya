const STAGING_ACCESS_HEADER = 'X-Syrabit-Staging-Token';

function constantTimeEqual(left: string, right: string): boolean {
  const encoder = new TextEncoder();
  const leftBytes = encoder.encode(left);
  const rightBytes = encoder.encode(right);
  if (leftBytes.length !== rightBytes.length) return false;

  let mismatch = 0;
  for (let i = 0; i < leftBytes.length; i += 1) {
    mismatch |= leftBytes[i] ^ rightBytes[i];
  }
  return mismatch === 0;
}

function failure(status: number, error: string, errorCode: string): Response {
  return new Response(JSON.stringify({ error, error_code: errorCode }), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    },
  });
}

/**
 * Fail closed for public staging Worker requests. OPTIONS is allowed so a
 * browser can complete CORS preflight; it never reaches the API binding.
 */
export function stagingGateFailure(
  request: Request,
  env: { APP_ENV?: string; STAGING_ACCESS_TOKEN?: string },
): Response | null {
  if (env.APP_ENV !== 'staging' || request.method === 'OPTIONS') return null;
  if (!env.STAGING_ACCESS_TOKEN) {
    return failure(503, 'Staging access is not configured', 'staging_access_unconfigured');
  }

  const supplied = request.headers.get(STAGING_ACCESS_HEADER) ?? '';
  if (!constantTimeEqual(supplied, env.STAGING_ACCESS_TOKEN)) {
    return failure(401, 'Staging access required', 'staging_access_required');
  }
  return null;
}