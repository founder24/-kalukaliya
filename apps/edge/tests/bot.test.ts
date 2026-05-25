import { describe, it, expect, vi, beforeEach } from 'vitest';
import { turnstileVerify } from '../src/middleware/bot';

describe('Turnstile Bot Verification', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('returns true when Cloudflare returns success', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: true }),
    });
    const result = await turnstileVerify('valid-token', 'test-secret');
    expect(result).toBe(true);
    expect(fetch).toHaveBeenCalledWith(
      'https://challenges.cloudflare.com/turnstile/v0/siteverify',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('returns false when Cloudflare returns failure', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: false }),
    });
    const result = await turnstileVerify('invalid-token', 'test-secret');
    expect(result).toBe(false);
  });

  it('returns false when fetch throws', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));
    const result = await turnstileVerify('any-token', 'test-secret');
    expect(result).toBe(false);
  });
});
