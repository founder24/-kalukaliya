import { Hono } from 'hono';
import type { Env } from '../types';

const IMAGE_MODEL = '@cf/moondream/moondream3.1-9B-A2B';
const TTS_MODEL = '@cf/myshell/melotts';
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_TTS_TEXT_LENGTH = 5000;
const RATE_LIMIT_WINDOW_MS = 60_000;

type AiRunner = {
  run: (model: string, input: Record<string, unknown>) => Promise<unknown>;
};

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const blockSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += blockSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + blockSize));
  }
  return btoa(binary);
}

async function mediaRateLimitResponse(
  request: Request,
  env: Env,
  kind: 'image' | 'tts',
  limit: number,
): Promise<Response | null> {
  const ip = request.headers.get('CF-Connecting-IP')?.trim();
  if (!ip) {
    return Response.json({ detail: 'Request identity unavailable.' }, { status: 503 });
  }

  const now = Date.now();
  const windowKey = Math.floor(now / RATE_LIMIT_WINDOW_MS);
  const resetAt = (windowKey + 1) * RATE_LIMIT_WINDOW_MS;
  const expiresAt = Math.ceil(resetAt / 1000) + 60;
  const nowSeconds = Math.floor(now / 1000);
  try {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(ip));
    const ipHash = Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('');
    const row = await env.DB.prepare(`
      INSERT INTO auth_rate_limits (bucket_key, request_count, expires_at, updated_at)
      VALUES (?, 1, ?, ?)
      ON CONFLICT(bucket_key) DO UPDATE SET
        request_count = MIN(auth_rate_limits.request_count + 1, ?),
        expires_at = excluded.expires_at,
        updated_at = excluded.updated_at
      RETURNING request_count
    `).bind(`chat-media:${kind}:${ipHash}:${windowKey}`, expiresAt, nowSeconds, limit + 1)
      .first<{ request_count: number }>();

    if (!row || !Number.isSafeInteger(row.request_count)) {
      throw new Error('D1 returned an invalid media rate limit counter');
    }
    if (row.request_count <= limit) return null;

    return Response.json({ detail: 'Too many requests. Please wait a minute and try again.' }, {
      status: 429,
      headers: {
        'X-RateLimit-Limit': String(limit),
        'X-RateLimit-Remaining': '0',
        'X-RateLimit-Reset': String(Math.floor(resetAt / 1000)),
        'Retry-After': String(Math.max(1, Math.ceil((resetAt - now) / 1000))),
      },
    });
  } catch (error) {
    console.error('[chat-media] rate limit storage unavailable:', error);
    return Response.json({ detail: 'Service temporarily unavailable.' }, { status: 503 });
  }
}

function extractImageText(result: unknown): string {
  if (typeof result === 'string') return result.trim();
  if (!result || typeof result !== 'object') return '';
  const value = result as Record<string, unknown>;
  for (const key of ['answer', 'description', 'response', 'text']) {
    if (typeof value[key] === 'string') return (value[key] as string).trim();
  }
  return '';
}

async function audioBytes(result: unknown): Promise<Uint8Array | null> {
  if (result instanceof Uint8Array) return result;
  if (result instanceof ArrayBuffer) return new Uint8Array(result);
  if (ArrayBuffer.isView(result)) {
    return new Uint8Array(result.buffer, result.byteOffset, result.byteLength);
  }
  if (result instanceof ReadableStream) {
    const response = new Response(result);
    return new Uint8Array(await response.arrayBuffer());
  }
  if (result && typeof result === 'object') {
    const audio = (result as Record<string, unknown>).audio;
    if (audio instanceof Uint8Array) return audio;
    if (audio instanceof ArrayBuffer) return new Uint8Array(audio);
    if (typeof audio === 'string') {
      try {
        const binary = atob(audio);
        return Uint8Array.from(binary, (char) => char.charCodeAt(0));
      } catch {
        return null;
      }
    }
  }
  return null;
}

export const chatMediaRouter = new Hono<{ Bindings: Env }>();

chatMediaRouter.post('/image', async (c) => {
  const limited = await mediaRateLimitResponse(c.req.raw, c.env, 'image', 8);
  if (limited) return limited;

  let body: Record<string, string | File> = {};
  try {
    body = await c.req.parseBody();
  } catch {
    return c.json({ detail: 'Could not read the uploaded image.' }, 400);
  }

  const file = body.file;
  if (!(file instanceof File)) {
    return c.json({ detail: 'Image file is required.' }, 400);
  }
  const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
  if (!allowedTypes.has(file.type)) {
    return c.json({ detail: 'Unsupported image type. Allowed: JPEG, PNG, GIF, WebP.' }, 400);
  }
  if (file.size === 0 || file.size > MAX_IMAGE_BYTES) {
    return c.json({ detail: 'Image must be between 1 byte and 8 MB.' }, 400);
  }

  try {
    const image = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
    const result = await (c.env.AI as unknown as AiRunner).run(IMAGE_MODEL, {
      task: 'query',
      image: `data:${file.type};base64,${image}`,
      question: typeof body.prompt === 'string' && body.prompt.trim()
        ? body.prompt.trim().slice(0, 500)
        : 'Extract all readable text from this image. Preserve the original language and line breaks.',
      reasoning: false,
      max_tokens: 512,
    });
    return c.json({ text: extractImageText(result), model: IMAGE_MODEL });
  } catch (error) {
    console.error('[chat-media] image analysis failed:', error);
    return c.json({ detail: 'Image analysis failed. Please try again.' }, 502);
  }
});

chatMediaRouter.post('/tts', async (c) => {
  const limited = await mediaRateLimitResponse(c.req.raw, c.env, 'tts', 30);
  if (limited) return limited;

  let payload: unknown;
  try {
    payload = await c.req.json();
  } catch {
    return c.json({ detail: 'Invalid text-to-speech request.' }, 400);
  }
  if (!payload || typeof payload !== 'object') {
    return c.json({ detail: 'Invalid text-to-speech request.' }, 400);
  }

  const { text, lang } = payload as { text?: unknown; lang?: unknown };
  if (typeof text !== 'string' || !text.trim() || text.length > MAX_TTS_TEXT_LENGTH) {
    return c.json({ detail: 'Text must contain 1 to 5000 characters.' }, 400);
  }
  if (lang !== undefined && lang !== 'en' && lang !== 'as') {
    return c.json({ detail: 'Unsupported speech language.' }, 400);
  }

  try {
    const result = await (c.env.AI as unknown as AiRunner).run(TTS_MODEL, {
      prompt: text.trim(),
      lang: lang ?? 'en',
    });
    const audio = await audioBytes(result);
    if (!audio?.byteLength) throw new Error('Workers AI returned no audio');
    return new Response(audio, {
      headers: {
        'Content-Type': 'audio/mpeg',
        'Cache-Control': 'no-store',
        'X-AI-Model': TTS_MODEL,
      },
    });
  } catch (error) {
    console.error('[chat-media] text-to-speech failed:', error);
    return c.json({ detail: 'Text-to-speech failed. Please try again.' }, 502);
  }
});
