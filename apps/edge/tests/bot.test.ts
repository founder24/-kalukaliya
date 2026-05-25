import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { turnstileVerify } from '../src/middleware/bot';

describe('turnstileVerify', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('returns true on successful verification', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: true }),
    });

    const result = await turnstileVerify('valid-token', 'test-secret');
    expect(result).toBe(true);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      'https://challenges.cloudflare.com/turnstile/v0/siteverify',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ secret: 'test-secret', response: 'valid-token' }),
      }),
    );
  });

  it('returns false on failed verification', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: false }),
    });

    const result = await turnstileVerify('invalid-token', 'test-secret');
    expect(result).toBe(false);
  });

  it('returns false when fetch throws an error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    const result = await turnstileVerify('any-token', 'test-secret');
    expect(result).toBe(false);
  });
});
