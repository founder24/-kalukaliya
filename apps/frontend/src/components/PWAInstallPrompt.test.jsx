import { render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PWAInstallPrompt from './PWAInstallPrompt';

describe('PWAInstallPrompt with blocked browser storage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not crash while checking the dismissal timestamp', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })));
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Access denied', 'SecurityError');
    });

    expect(() => render(<PWAInstallPrompt />)).not.toThrow();
  });
});