/**
 * Task #422 — Tests for <TurnstileWidget /> render branches and ref API.
 *
 * Covers:
 *   - Renders nothing when the backend config reports disabled.
 *   - Renders the container with the configured site key when enabled.
 *   - Exposes getToken() and reset() via the imperative ref handle,
 *     and proxies reset() to the underlying window.turnstile.reset.
 *   - getToken() returns the latest token captured from the Turnstile
 *     ``callback`` and clears to '' after reset().
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, screen } from '@testing-library/react';
import React, { createRef } from 'react';

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local/api',
}));

const mockUseTurnstileConfig = vi.fn();
const mockLoadTurnstileScript = vi.fn();

vi.mock('@/hooks/useTurnstile', () => ({
  useTurnstileConfig: () => mockUseTurnstileConfig(),
  loadTurnstileScript: (...args) => mockLoadTurnstileScript(...args),
}));

import TurnstileWidget from './TurnstileWidget';

let renderedOptions = null;

function installTurnstileGlobal() {
  const stub = {
    render: vi.fn((el, opts) => {
      renderedOptions = opts;
      return 'widget-id-1';
    }),
    reset: vi.fn(),
    remove: vi.fn(),
  };
  window.turnstile = stub;
  return stub;
}

beforeEach(() => {
  renderedOptions = null;
  mockUseTurnstileConfig.mockReset();
  mockLoadTurnstileScript.mockReset();
  delete window.turnstile;
});

afterEach(() => {
  delete window.turnstile;
  vi.restoreAllMocks();
});

describe('<TurnstileWidget />', () => {
  it('renders nothing while config is still loading (ready=false)', () => {
    mockUseTurnstileConfig.mockReturnValue({ ready: false, enabled: false, siteKey: null });
    mockLoadTurnstileScript.mockResolvedValue(null);

    const { container } = render(<TurnstileWidget action="login" />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when config is ready but Turnstile is disabled', () => {
    mockUseTurnstileConfig.mockReturnValue({ ready: true, enabled: false, siteKey: null });
    mockLoadTurnstileScript.mockResolvedValue(null);

    const { container } = render(<TurnstileWidget action="login" />);
    expect(container.firstChild).toBeNull();
    expect(mockLoadTurnstileScript).not.toHaveBeenCalled();
  });

  it('renders nothing when enabled is true but siteKey is missing', () => {
    mockUseTurnstileConfig.mockReturnValue({ ready: true, enabled: true, siteKey: null });
    mockLoadTurnstileScript.mockResolvedValue(null);

    const { container } = render(<TurnstileWidget action="login" />);
    expect(container.firstChild).toBeNull();
    expect(mockLoadTurnstileScript).not.toHaveBeenCalled();
  });

  it('renders a container carrying the site key when enabled', async () => {
    installTurnstileGlobal();
    mockUseTurnstileConfig.mockReturnValue({ ready: true, enabled: true, siteKey: 'SITE-KEY-XYZ' });
    mockLoadTurnstileScript.mockResolvedValue(window.turnstile);

    await act(async () => {
      render(<TurnstileWidget action="login" className="my-cls" />);
    });

    const widget = screen.getByTestId('turnstile-widget');
    expect(widget).toBeInTheDocument();
    expect(widget.getAttribute('data-sitekey')).toBe('SITE-KEY-XYZ');
    expect(widget.parentElement.className).toBe('my-cls');
    expect(window.turnstile.render).toHaveBeenCalledTimes(1);
    expect(renderedOptions.sitekey).toBe('SITE-KEY-XYZ');
    expect(renderedOptions.action).toBe('login');
  });

  it('exposes getToken() and reset() via ref, and proxies reset to window.turnstile', async () => {
    const stub = installTurnstileGlobal();
    mockUseTurnstileConfig.mockReturnValue({ ready: true, enabled: true, siteKey: 'KEY-1' });
    mockLoadTurnstileScript.mockResolvedValue(stub);

    const ref = createRef();
    await act(async () => {
      render(<TurnstileWidget ref={ref} action="signup" />);
    });

    expect(typeof ref.current.getToken).toBe('function');
    expect(typeof ref.current.reset).toBe('function');
    expect(ref.current.enabled).toBe(true);

    // No token captured yet.
    expect(ref.current.getToken()).toBe('');

    // Simulate the Turnstile callback firing with a successful token.
    act(() => {
      renderedOptions.callback('cf-turnstile-response-token');
    });
    expect(ref.current.getToken()).toBe('cf-turnstile-response-token');

    // reset() must clear the cached token AND ask Turnstile to refresh.
    act(() => {
      ref.current.reset();
    });
    expect(ref.current.getToken()).toBe('');
    expect(stub.reset).toHaveBeenCalledWith('widget-id-1');
  });

  it('clears the token when the error-callback fires and surfaces an alert', async () => {
    const stub = installTurnstileGlobal();
    mockUseTurnstileConfig.mockReturnValue({ ready: true, enabled: true, siteKey: 'KEY-2' });
    mockLoadTurnstileScript.mockResolvedValue(stub);

    const ref = createRef();
    await act(async () => {
      render(<TurnstileWidget ref={ref} />);
    });

    act(() => {
      renderedOptions.callback('tok');
    });
    expect(ref.current.getToken()).toBe('tok');

    act(() => {
      renderedOptions['error-callback']();
    });
    expect(ref.current.getToken()).toBe('');
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});
