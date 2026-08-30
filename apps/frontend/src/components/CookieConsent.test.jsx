import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import CookieConsent from './CookieConsent';

describe('CookieConsent with blocked browser storage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps the app usable and dismisses without persisting', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Access denied', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Access denied', 'SecurityError');
    });

    render(<CookieConsent />);
    await waitFor(() => expect(screen.getByText('Cookie Notice')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }));
    expect(screen.queryByText('Cookie Notice')).not.toBeInTheDocument();
  });
});