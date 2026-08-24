import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import worker from '../src/index';

// Helper: create a valid HS256 JWT (matches the JWT_SECRET in createMockEnv)
async function createTestJWT(
  payload: Record<string, unknown>,
  secret: string,
): Promise<string> {
  const b64url = (s: string) =>
    btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const header = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = b64url(JSON.stringify(payload));
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(`${header}.${body}`));
  const sigB64 = btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${header}.${body}.${sigB64}`;
}

function createMockEnv(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
    BACKEND_URL: 'http://localhost:8000',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    RATE_LIMIT_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    ISR_CACHE_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    ...overrides,
  };
}

function createMockCtx(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  };
}

describe('Deployment Audit - Full Worker Fetch Handler', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('CORS preflight (OPTIONS /api/v1/chat) returns 200 with CORS headers', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'OPTIONS',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
    expect(response.headers.get('Access-Control-Allow-Methods')).toContain('POST');
    expect(response.headers.get('Access-Control-Allow-Headers')).toContain('Authorization');
  });

  it('Health endpoint returns edge status directly', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();

    const request = new Request('https://syrabit.ai/health', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const json = await response.json() as Record<string, unknown>;
    expect(json.status).toBe('healthy');
    expect(json.service).toBe('syrabit-backend');
    expect(json.timestamp).toBeDefined();
    expect(json.backend_mode).toBe('cloud-run');
    expect(response.headers.get('X-Syrabit-Health-Backend')).toBe('cloud-run');
  });

  it('Health endpoint identifies the API Worker service-binding probe', async () => {
    const env = createMockEnv({
      API_WORKER: {
        fetch: vi.fn(async () => new Response(null, { status: 200 })),
      } as unknown as { fetch(request: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
    });

    const response = await worker.fetch(
      new Request('https://syrabit.ai/health', { method: 'GET' }),
      env,
      createMockCtx(),
    );

    expect(response.status).toBe(200);
    const json = await response.json() as Record<string, unknown>;
    expect(json.backend_mode).toBe('api-worker');
    expect(response.headers.get('X-Syrabit-Health-Backend')).toBe('api-worker');
  });

  it('/robots.txt returns robots content', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/robots.txt', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const text = await response.text();
    expect(text).toContain('User-agent: *');
    expect(text).toContain('Sitemap:');
    expect(response.headers.get('Content-Type')).toContain('text/plain');
  });

  it('routes root crawler artifacts to the API Worker without noindex headers', async () => {
    let forwardedPath = '';
    const apiFetch = vi.fn(async (req: Request) => {
      forwardedPath = new URL(req.url).pathname;
      return new Response('crawler artifact', {
        headers: {
          'Content-Type': 'application/feed+json; charset=utf-8',
          'X-Syrabit-Route': 'worker-native',
        },
      });
    });
    const env = createMockEnv({
      API_WORKER: { fetch: apiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const response = await worker.fetch(
      new Request('https://syrabit.ai/feed.json'),
      env,
      createMockCtx(),
    );

    expect(response.status).toBe(200);
    expect(forwardedPath).toBe('/api/v1/seo/feed.json');
    expect(response.headers.get('X-Robots-Tag')).toBeNull();
    expect(response.headers.get('X-Syrabit-Route')).toBe('worker-native');
  });

  it('/assets/missing returns 404', async () => {
    const env = createMockEnv({
      R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    });
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/assets/missing.js', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(404);
  });

  it('/assets/found returns R2 object with correct headers', async () => {
    const mockBody = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('file-content'));
        controller.close();
      },
    });
    const mockR2Object = {
      body: mockBody,
      writeHttpMetadata: vi.fn((headers: Headers) => {
        headers.set('Content-Type', 'application/javascript');
      }),
    };
    const env = createMockEnv({
      R2_BUCKET: { get: vi.fn(async () => mockR2Object) } as unknown as R2Bucket,
    });
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/assets/app.js', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    expect(response.headers.get('Content-Type')).toBe('application/javascript');
    expect(response.headers.get('Cache-Control')).toContain('immutable');
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });

  it('Unknown path returns 404', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/unknown-path', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(404);
  });

  it('JWT-protected endpoint without token gets passed through to backend', async () => {
    const env = createMockEnv({ ALLOWED_ORIGIN: 'http://localhost:3000' });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    // /api/v1/users is not in PUBLIC_PATHS, so JWT will return
    // "Missing or invalid Authorization header" -- but the edge does NOT reject that
    const request = new Request('https://syrabit.ai/api/v1/users', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    // Should pass through to backend (not 401)
    expect(response.status).toBe(200);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('http://localhost:8000/api/v1/users'),
      expect.anything(),
    );
  });

  it('Rate-limited chat POST with KV mock - allowed when under limit', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ reply: 'hello' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    const response = await worker.fetch(request, env, ctx);

    // Should not be rate limited (under limit)
    expect(response.status).not.toBe(429);
  });

  it('Rate-limited chat POST - blocked when over limit (returns 429)', async () => {
    const store: Record<string, string> = {};

    const env = createMockEnv({
      ALLOWED_ORIGIN: 'http://localhost:3000',
      RATE_LIMIT_KV: {
        get: vi.fn(async (key: string) => {
          // Return 30 (at limit) for any rate-limit key
          if (key.startsWith('rl:')) return '30';
          return store[key] || null;
        }),
        put: vi.fn(async () => {}),
        delete: vi.fn(async () => {}),
      } as unknown as KVNamespace,
    });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => {
      return new Response(JSON.stringify({ reply: 'hello' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(429);
    const body = await response.json();
    expect(body).toHaveProperty('error', 'Rate limit exceeded');
  });

  // ── Service Binding / API Worker routing ─────────────────────────────────

  it('routes all API requests to API Worker when API_WORKER_LIVE=true', async () => {
    const mockApiFetch = vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'Invalid credentials' }), {
        status: 401, headers: { 'Content-Type': 'application/json' },
      })
    );
    // Use a non-localhost BACKEND_URL so the production-safety check (line ~60)
    // does not trigger a 503 before routing reaches the Service Binding logic.
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const request = new Request('https://syrabit.ai/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'test@example.com', password: 'wrong' }),
    });

    const response = await worker.fetch(request, env as unknown as Env, createMockCtx());

    expect(mockApiFetch).toHaveBeenCalled();
    expect(response.status).toBe(401);
  });

  it('preserves Authorization header when forwarding to API Worker', async () => {
    // Create a valid HS256 JWT so it passes the edge JWT middleware.
    // The secret must match JWT_SECRET in createMockEnv.
    const JWT_TEST_SECRET = 'test-secret-for-unit-tests-at-least-32-characters';
    const validToken = await createTestJWT(
      { sub: 'user-abc', exp: Math.floor(Date.now() / 1000) + 3600, type: 'access' },
      JWT_TEST_SECRET,
    );

    let capturedAuth = '';
    const mockApiFetch = vi.fn(async (req: Request) => {
      capturedAuth = req.headers.get('Authorization') || '';
      return new Response(JSON.stringify({ user: { id: 'user-abc' } }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    });
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const request = new Request('https://syrabit.ai/api/v1/auth/me', {
      headers: { Authorization: `Bearer ${validToken}` },
    });

    await worker.fetch(request, env as unknown as Env, createMockCtx());

    expect(mockApiFetch).toHaveBeenCalled();
    expect(capturedAuth).toBe(`Bearer ${validToken}`);
  });

  it('falls back to Cloud Run HTTP proxy when API_WORKER_LIVE is not set', async () => {
    const mockCloudRunFetch = vi.fn(async () =>
      new Response(JSON.stringify({ subjects: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    vi.stubGlobal('fetch', mockCloudRunFetch);

    // Use a non-localhost BACKEND_URL to bypass the production safety check
    const env = createMockEnv({ BACKEND_URL: 'https://cloud-run.example.com' });
    const request = new Request('https://syrabit.ai/api/v1/content/subjects');

    const response = await worker.fetch(request, env, createMockCtx());

    expect(response.status).toBe(200);
    expect(mockCloudRunFetch).toHaveBeenCalled();
  });

  it('API Worker fallback: X-Cloud-Run-Token is not forwarded to caller', async () => {
    const mockApiFetch = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const request = new Request('https://syrabit.ai/api/v1/content/subjects');
    const response = await worker.fetch(request, env as unknown as Env, createMockCtx());

    // The response returned to the browser must not expose internal OIDC token headers
    expect(response.headers.get('x-cloud-run-token')).toBeNull();
  });

  // ── Service Binding integration: response shape contracts ────────────────────

  it('auth login via service binding returns expected JWT token shape', async () => {
    const mockApiFetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          access_token: 'eyJ.test.token',
          refresh_token: 'eyJ.test.refresh',
          token_type: 'Bearer',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    );
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const response = await worker.fetch(
      new Request('https://syrabit.ai/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'user@test.com', password: 'pass1234' }),
      }),
      env as unknown as Env,
      createMockCtx(),
    );

    expect(mockApiFetch).toHaveBeenCalled();
    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    // Verify the D1-backed auth Worker returns the established JWT contract
    expect(body).toHaveProperty('access_token');
    expect(body).toHaveProperty('refresh_token');
    expect(body).toHaveProperty('token_type', 'Bearer');
  });

  it('content/subjects D1 route returns published-subjects array with correct field names', async () => {
    // /api/v1/content/subjects is now D1-backed in the API Worker.
    // The API Worker responds with the Cloud Run contract: a direct array of subject
    // objects with id, name, slug, stream_id, status, description, icon, thumbnail_url, tags.
    const d1Subjects = [
      {
        id: 'sub-1',
        name: 'Physics',
        slug: 'physics',
        stream_id: 'stream-1',
        status: 'published',
        description: null,
        icon: null,
        thumbnail_url: null,
        tags: [],
      },
    ];
    const mockApiFetch = vi.fn(async () =>
      new Response(JSON.stringify(d1Subjects), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const response = await worker.fetch(
      new Request('https://syrabit.ai/api/v1/content/subjects?stream_id=stream-1'),
      env as unknown as Env,
      createMockCtx(),
    );

    expect(mockApiFetch).toHaveBeenCalled();
    expect(response.status).toBe(200);
    const body = await response.json() as unknown[];
    // Cloud Run contract: direct array (no wrapper object)
    expect(Array.isArray(body)).toBe(true);
    const subject = body[0] as Record<string, unknown>;
    expect(subject).toHaveProperty('id');
    expect(subject).toHaveProperty('name');
    expect(subject).toHaveProperty('slug');
    expect(subject).toHaveProperty('stream_id');
    expect(subject).toHaveProperty('status', 'published');
    expect(subject).toHaveProperty('icon');
    expect(subject).toHaveProperty('thumbnail_url');
    expect(subject).toHaveProperty('tags');
  });

  it('content/chapters/:subjectId D1 route returns chapter-list array with correct field names', async () => {
    // /api/v1/content/chapters/{subjectId} is D1-backed.
    // Cloud Run contract: direct array with chapter_id (NOT id), title, slug,
    // chapter_number, notes_generated, has_assamese, title_as.
    const d1Chapters = [
      {
        chapter_id: 'ch-1',
        title: 'Physical World',
        title_as: null,
        slug: 'physical-world',
        chapter_number: 1,
        notes_generated: true,
        has_assamese: false,
      },
    ];
    const mockApiFetch = vi.fn(async () =>
      new Response(JSON.stringify(d1Chapters), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const response = await worker.fetch(
      new Request('https://syrabit.ai/api/v1/content/chapters/sub-1'),
      env as unknown as Env,
      createMockCtx(),
    );

    expect(mockApiFetch).toHaveBeenCalled();
    expect(response.status).toBe(200);
    const body = await response.json() as unknown[];
    expect(Array.isArray(body)).toBe(true);
    const ch = body[0] as Record<string, unknown>;
    // Must use chapter_id (not id) to match Cloud Run contract
    expect(ch).toHaveProperty('chapter_id');
    expect(ch).not.toHaveProperty('id');   // 'id' alone is the wrong field name
    expect(ch).toHaveProperty('title');
    expect(ch).toHaveProperty('title_as');
    expect(ch).toHaveProperty('slug');
    expect(ch).toHaveProperty('chapter_number');
    expect(ch).toHaveProperty('notes_generated');
    expect(ch).toHaveProperty('has_assamese');
  });

  it('users/profile D1 route returns full profile with credits and academic fields', async () => {
    // /api/v1/users/profile and /api/v1/user/profile are D1-backed.
    // Cloud Run _build_profile_response contract includes subscription_tier, plan,
    // credits_used, credits_limit, credits_remaining, board_id, stream_id, etc.
    const profileShape = {
      id: 'user-1',
      name: 'Test User',
      email: 'test@example.com',
      role: 'student',
      subscription_tier: 'free',
      plan: 'free',
      monthly_message_count: 0,
      preferred_language: 'as',
      onboarding_done: false,
      ads_opt_out: false,
      saved_subjects: [],
      phone: null,
      board_id: null,
      board_name: null,
      class_id: null,
      class_name: null,
      stream_id: null,
      stream_name: null,
      credits_used: 0,
      credits_limit: 30,
      credits_remaining: 30,
      status: 'active',
      deletion_hard_at: null,
    };
    const mockApiFetch = vi.fn(async () =>
      new Response(JSON.stringify(profileShape), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const JWT_TEST_SECRET = 'test-secret-for-unit-tests-at-least-32-characters';
    const token = await createTestJWT(
      { sub: 'user-1', exp: Math.floor(Date.now() / 1000) + 3600, type: 'access' },
      JWT_TEST_SECRET,
    );

    const response = await worker.fetch(
      new Request('https://syrabit.ai/api/v1/user/profile', {
        headers: { Authorization: `Bearer ${token}` },
      }),
      env as unknown as Env,
      createMockCtx(),
    );

    expect(mockApiFetch).toHaveBeenCalled();
    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    // Verify full Cloud Run profile contract fields are present
    expect(body).toHaveProperty('subscription_tier');
    expect(body).toHaveProperty('plan');               // alias of subscription_tier
    expect(body).toHaveProperty('credits_used');
    expect(body).toHaveProperty('credits_limit');
    expect(body).toHaveProperty('credits_remaining');
    expect(body).toHaveProperty('board_id');
    expect(body).toHaveProperty('stream_id');
    expect(body).toHaveProperty('status', 'active');
    expect(body).toHaveProperty('deletion_hard_at');
  });

  it('admin/payment routes not in API Worker still reach Cloud Run via fallback', async () => {
    // Admin and payment routes are not yet ported (Phase 7).
    // With API_WORKER_LIVE=true they arrive at the API Worker and
    // must fall through to Cloud Run — not return 404 from the Worker itself.
    const mockApiFetch = vi.fn(async () =>
      new Response(JSON.stringify({ users: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    const env = createMockEnv({
      API_WORKER: { fetch: mockApiFetch } as unknown as { fetch(r: Request): Promise<Response> },
      API_WORKER_LIVE: 'true',
      BACKEND_URL: 'https://cloud-run.example.com',
    });

    const response = await worker.fetch(
      new Request('https://syrabit.ai/api/v1/admin/users'),
      env as unknown as Env,
      createMockCtx(),
    );

    // API Worker must forward this to Cloud Run (via fallback proxy) — service binding used
    expect(mockApiFetch).toHaveBeenCalled();
    expect(response.status).toBe(200);
  });

  it('Request without RATE_LIMIT_KV binding skips rate limiting gracefully', async () => {
    const env = createMockEnv({
      RATE_LIMIT_KV: undefined as unknown as KVNamespace,
    });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => {
      return new Response(JSON.stringify({ reply: 'hello' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    // Should not throw - should skip rate limiting and proxy to backend
    const response = await worker.fetch(request, env, ctx);

    expect(response.status).not.toBe(429);
    expect(response.status).not.toBe(500);
  });
});
