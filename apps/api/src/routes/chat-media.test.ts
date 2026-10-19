import { describe, expect, it, vi } from 'vitest';
import type { Env } from '../types';
import { chatMediaRouter } from './chat-media';
import { api } from './index';

function makeEnv(aiRun: ReturnType<typeof vi.fn>, requestCount = 1) {
  const first = vi.fn().mockResolvedValue({ request_count: requestCount });
  const bind = vi.fn(() => ({ first }));
  const prepare = vi.fn(() => ({ bind }));
  return {
    env: {
      AI: { run: aiRun },
      DB: { prepare },
    } as unknown as Env,
    prepare,
    bind,
  };
}

describe('chat Workers AI media routes', () => {
  it('sends uploaded images to the OCR model and returns extracted text', async () => {
    const aiRun = vi.fn().mockResolvedValue({ answer: 'Question from image' });
    const { env } = makeEnv(aiRun);
    const form = new FormData();
    form.append('file', new File([new Uint8Array([0, 1, 2])], 'question.png', {
      type: 'image/png',
    }));
    const request = new Request('https://api.example/image', {
      method: 'POST',
      headers: { 'CF-Connecting-IP': '203.0.113.10' },
      body: form,
    });

    const response = await chatMediaRouter.fetch(request, env);

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      text: 'Question from image',
      model: '@cf/moondream/moondream3.1-9B-A2B',
    });
    expect(aiRun).toHaveBeenCalledWith(
      '@cf/moondream/moondream3.1-9B-A2B',
      expect.objectContaining({
        task: 'query',
        image: `data:image/png;base64,${btoa(String.fromCharCode(0, 1, 2))}`,
        reasoning: false,
        max_tokens: 512,
      }),
    );
  });

  it('uses the Workers AI MeloTTS contract and returns MP3 bytes', async () => {
    const aiRun = vi.fn().mockResolvedValue(new Uint8Array([1, 2, 3]));
    const { env } = makeEnv(aiRun);
    const request = new Request('https://api.example/tts', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'CF-Connecting-IP': '203.0.113.10',
      },
      body: JSON.stringify({ text: 'Hello there', lang: 'as' }),
    });

    const response = await chatMediaRouter.fetch(request, env);

    expect(response.status).toBe(200);
    expect(response.headers.get('Content-Type')).toBe('audio/mpeg');
    expect(Array.from(new Uint8Array(await response.arrayBuffer()))).toEqual([1, 2, 3]);
    expect(aiRun).toHaveBeenCalledWith('@cf/myshell/melotts', {
      prompt: 'Hello there',
      lang: 'as',
    });
  });

  it('mounts the media routes under the public API chat path', async () => {
    const aiRun = vi.fn().mockResolvedValue(new Uint8Array([4, 5]));
    const { env } = makeEnv(aiRun);
    const request = new Request('https://api.example/api/v1/chat/tts', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'CF-Connecting-IP': '203.0.113.11',
      },
      body: JSON.stringify({ text: 'Mounted route check' }),
    });

    const response = await api.fetch(request, env);

    expect(response.status).toBe(200);
    expect(aiRun).toHaveBeenCalledWith('@cf/myshell/melotts', {
      prompt: 'Mounted route check',
      lang: 'en',
    });
  });

  it('rejects unsupported image types before invoking Workers AI', async () => {
    const aiRun = vi.fn();
    const { env } = makeEnv(aiRun);
    const form = new FormData();
    form.append('file', new File(['not an image'], 'notes.txt', { type: 'text/plain' }));
    const request = new Request('https://api.example/image', {
      method: 'POST',
      headers: { 'CF-Connecting-IP': '203.0.113.10' },
      body: form,
    });

    const response = await chatMediaRouter.fetch(request, env);

    expect(response.status).toBe(400);
    expect(aiRun).not.toHaveBeenCalled();
  });

  it('blocks image OCR when the per-IP limit is exceeded', async () => {
    const aiRun = vi.fn();
    const { env } = makeEnv(aiRun, 9);
    const form = new FormData();
    form.append('file', new File(['x'], 'question.png', { type: 'image/png' }));
    const request = new Request('https://api.example/image', {
      method: 'POST',
      headers: { 'CF-Connecting-IP': '203.0.113.10' },
      body: form,
    });

    const response = await chatMediaRouter.fetch(request, env);

    expect(response.status).toBe(429);
    expect(aiRun).not.toHaveBeenCalled();
  });
});
