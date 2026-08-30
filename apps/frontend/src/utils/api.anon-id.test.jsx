import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { anonHeaders, getAnonConversations, getAnonId } from './api';

describe('anonymous browser identity', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('persists one valid ID across reload-style reads', () => {
    const first = getAnonId();
    const second = getAnonId();

    expect(first).toMatch(/^anon_[a-f0-9]{32}$/);
    expect(second).toBe(first);
    expect(localStorage.getItem('syrabit_anon_id')).toBe(first);
  });

  it('replaces malformed stored identities before sending requests', () => {
    localStorage.setItem('syrabit_anon_id', 'anon_wrong');

    const id = getAnonId();

    expect(id).toMatch(/^anon_[a-f0-9]{32}$/);
    expect(id).not.toBe('anon_wrong');
    expect(localStorage.getItem('syrabit_anon_id')).toBe(id);
  });

  it('hands storage-disabled browsers off to the signed cookie fallback', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Access denied', 'SecurityError');
    });
    const get = vi.spyOn(axios, 'get').mockResolvedValue({ data: { conversations: [] } });

    expect(getAnonId()).toBeNull();
    expect(anonHeaders()).toEqual({});
    await getAnonConversations();

    expect(get).toHaveBeenCalledWith(
      expect.stringContaining('/conversations/anon'),
      { headers: {}, withCredentials: true },
    );
  });
});