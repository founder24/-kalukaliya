import { beforeEach, describe, expect, it } from 'vitest';

import { getAnonId } from './api';

describe('anonymous browser identity', () => {
  beforeEach(() => {
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
});