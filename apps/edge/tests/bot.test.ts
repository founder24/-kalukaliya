import { describe, it, expect, vi, beforeEach } from 'vitest';
import { turnstileVerify } from '../src/middleware/bot';

describe('Bot Detection - Turnstile Verification', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('returns true for valid turnstile token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ success: true }),
    }));

    const result = await turnstileVerify('valid-token', 'test-secret');
    expect(result).toBe(true);
    expect(fetch).toHaveBeenCalledWith(
      'https://challenges.cloudflare.com/turnstile/v0/siteverify',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('returns false for invalid turnstile token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ success: false }),
    }));

    const result = await turnstileVerify('invalid-token', 'test-secret');
    expect(result).toBe(false);
  });

  it('returns false when fetch throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network error')));

    const result = await turnstileVerify('any-token', 'test-secret');
    expect(result).toBe(false);
  });

  it('sends correct payload to Cloudflare', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      json: () => Promise.resolve({ success: true }),
    }));

    await turnstileVerify('my-token', 'my-secret');

    const fetchCall = (fetch as any).mock.calls[0];
    const body = JSON.parse(fetchCall[1].body);
    expect(body.secret).toBe('my-secret');
    expect(body.response).toBe('my-token');
  });
});
