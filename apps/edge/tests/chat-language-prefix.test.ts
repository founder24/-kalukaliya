import { describe, expect, it } from 'vitest';
import { readChatLanguagePrefix } from '../src/index';

describe('readChatLanguagePrefix', () => {
  it('finds the language without consuming the original request body', async () => {
    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lang: 'as',
        message: 'x'.repeat(100_000),
      }),
    });

    await expect(readChatLanguagePrefix(request)).resolves.toBe('as');
    await expect(request.json()).resolves.toMatchObject({
      lang: 'as',
      message: 'x'.repeat(100_000),
    });
  });

  it('returns null when the language is absent or unsupported', async () => {
    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      body: JSON.stringify({ lang: 'hi', message: 'hello' }),
    });

    await expect(readChatLanguagePrefix(request)).resolves.toBeNull();
  });
});