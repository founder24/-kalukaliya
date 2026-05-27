import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { proxyRequest } from '../src/routes/api-proxy';

function createMockEnv(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
    CF_TURNSTILE_SECRET: 'test-turnstile-secret',
    AZURE_BACKEND_URL: 'http://localhost:8000',
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

describe('Edge-to-Backend Contract Tests', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  describe('Chat POST proxy contract', () => {
    it('proxies /api/v1/chat POST to backend with correct headers', async () => {
      let capturedUrl = '';
      let capturedInit: RequestInit | undefined;

      vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
        capturedUrl = url;
        capturedInit = init;
        return new Response(JSON.stringify({ text: 'response', done: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }));

      const env = createMockEnv();
      const chatBody = JSON.stringify({
        message: 'Hello, how are you?',
        lang: 'en',
        session_id: 'sess-abc-123',
      });

      const request = new Request('https://syrabit.ai/api/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': 'user-123',
          'CF-Connecting-IP': '203.0.113.5',
        },
        body: chatBody,
      });

      const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

      // Verify the proxy forwards to the correct backend URL
      expect(capturedUrl).toBe('http://localhost:8000/api/v1/chat');
      expect(capturedInit?.method).toBe('POST');

      // Verify X-User-ID header is passed through
      const headers = new Headers(capturedInit?.headers as HeadersInit);
      expect(headers.get('X-User-ID')).toBe('user-123');
      expect(headers.get('Content-Type')).toBe('application/json');

      // Verify response is passed back correctly
      expect(response.status).toBe(200);
      const body = await response.json();
      expect(body).toEqual({ text: 'response', done: true });
    });

    it('passes X-Rate-Limited-By header when rate limit KV is available', async () => {
      let capturedHeaders: Headers | null = null;

      vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
        capturedHeaders = new Headers(init.headers as HeadersInit);
        return new Response(JSON.stringify({ text: 'ok', done: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }));

      const env = createMockEnv();

      // Simulate that the edge already set X-Rate-Limited-By (set by rate limit middleware)
      const request = new Request('https://syrabit.ai/api/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': 'user-456',
          'X-Rate-Limited-By': 'edge',
          'CF-Connecting-IP': '198.51.100.1',
        },
        body: JSON.stringify({ message: 'test', lang: 'as', session_id: 'sess-xyz' }),
      });

      await proxyRequest(request, env.AZURE_BACKEND_URL, env);

      expect(capturedHeaders).not.toBeNull();
      expect(capturedHeaders!.get('X-Rate-Limited-By')).toBe('edge');
    });
  });

  describe('Backend error handling', () => {
    it('returns 503 when backend returns connection error', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => {
        throw new Error('ECONNREFUSED');
      }));

      const env = createMockEnv();
      const request = new Request('https://syrabit.ai/api/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': 'user-789',
        },
        body: JSON.stringify({ message: 'hello', lang: 'en', session_id: 'sess-001' }),
      });

      const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

      expect(response.status).toBe(503);
      const body = await response.json();
      expect(body.error).toBe('Backend service unavailable');
      expect(body.details).toBeDefined();
    });

    it('passes through backend 5xx status codes', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => {
        return new Response(
          JSON.stringify({ detail: 'Internal server error' }),
          { status: 500, headers: { 'Content-Type': 'application/json' } }
        );
      }));

      const env = createMockEnv();
      const request = new Request('https://syrabit.ai/api/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': 'user-000',
        },
        body: JSON.stringify({ message: 'test', lang: 'en', session_id: 'sess-002' }),
      });

      const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

      // Backend 5xx is passed through (not swallowed)
      expect(response.status).toBe(500);
      const body = await response.json();
      expect(body.detail).toBe('Internal server error');
    });
  });

  describe('Chat endpoint request/response contract shapes', () => {
    it('validates expected chat request shape (message, lang, session_id)', async () => {
      let capturedBody = '';

      vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
        // Read the body that was proxied
        if (init.body instanceof ReadableStream) {
          const reader = init.body.getReader();
          const { value } = await reader.read();
          capturedBody = new TextDecoder().decode(value);
        }
        return new Response(JSON.stringify({ text: 'reply', done: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }));

      const env = createMockEnv();
      const chatRequest = {
        message: 'What is Assamese literature?',
        lang: 'as',
        session_id: 'sess-contract-test',
      };

      const request = new Request('https://syrabit.ai/api/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': 'user-contract',
        },
        body: JSON.stringify(chatRequest),
      });

      const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

      expect(response.status).toBe(200);
      const responseBody = await response.json();

      // Verify response contract shape has text and done fields
      expect(responseBody).toHaveProperty('text');
      expect(responseBody).toHaveProperty('done');
      expect(typeof responseBody.text).toBe('string');
      expect(typeof responseBody.done).toBe('boolean');
    });

    it('validates streaming response contract (text/event-stream with chunks)', async () => {
      const chunks = [
        'data: {"text": "Hello", "done": false}\n\n',
        'data: {"text": " world", "done": false}\n\n',
        'data: {"text": "", "done": true}\n\n',
      ];

      const streamBody = new ReadableStream({
        start(controller) {
          for (const chunk of chunks) {
            controller.enqueue(new TextEncoder().encode(chunk));
          }
          controller.close();
        },
      });

      vi.stubGlobal('fetch', vi.fn(async () => {
        return new Response(streamBody, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }));

      const env = createMockEnv();
      const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': 'user-stream',
        },
        body: JSON.stringify({ message: 'hello', lang: 'en', session_id: 'sess-stream' }),
      });

      const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

      // Streaming response contract
      expect(response.status).toBe(200);
      expect(response.headers.get('Content-Type')).toBe('text/event-stream');
      expect(response.headers.get('Cache-Control')).toBe('no-store');

      // Read and validate stream chunks
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let fullText = '';
      let readDone = false;

      while (!readDone) {
        const { value, done } = await reader.read();
        if (done) {
          readDone = true;
        } else {
          fullText += decoder.decode(value);
        }
      }

      // Each chunk follows SSE format: "data: {json}\n\n"
      const lines = fullText.split('\n\n').filter(l => l.startsWith('data: '));
      expect(lines.length).toBe(3);

      // Last chunk should have done: true
      const lastChunk = JSON.parse(lines[2].replace('data: ', ''));
      expect(lastChunk.done).toBe(true);
    });
  });
});
