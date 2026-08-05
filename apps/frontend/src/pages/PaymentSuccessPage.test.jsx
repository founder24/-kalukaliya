/**
 * Tests for PaymentSuccessPage — covers graceful rendering across all
 * URL-param combinations a user might arrive with (bookmarked, shared, or
 * re-opened after tab-close).
 *
 * renderToStaticMarkup + MemoryRouter is used so useEffect (the redirect
 * countdown) never fires and no timer mocking is required.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Lightweight stubs for layout components ─────────────────────────────────
vi.mock('@/components/layout/PublicLayout', () => ({
  PublicLayout: ({ children }) => <div data-testid="layout">{children}</div>,
}));

vi.mock('@/components/PageTitle', () => ({
  PageTitle: ({ title }) => <title>{title}</title>,
}));

// ── System under test ────────────────────────────────────────────────────────
import PaymentSuccessPage from './PaymentSuccessPage';

/** Helper: render the page at a given URL and return the HTML string. */
function renderAt(url) {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[url]}>
      <PaymentSuccessPage />
    </MemoryRouter>,
  );
}

// ────────────────────────────────────────────────────────────────────────────
describe('PaymentSuccessPage', () => {
  // Seed a receipt token before every test so the page guard passes.
  // The guard consumes the token (removeItem) on the first render, so each
  // test must set it afresh.
  beforeEach(() => {
    sessionStorage.setItem('receipt_token', 'test-receipt-token');
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  // ── Guard: no receipt token ───────────────────────────────────────────────
  describe('no receipt token — direct URL access', () => {
    it('redirects to /profile when sessionStorage has no receipt_token', () => {
      // Clear the token that beforeEach set so we can test the no-token path.
      sessionStorage.removeItem('receipt_token');
      const html = renderAt('/payment-success?type=subscription&plan=pro&amount=99900');
      // Navigate renders as empty markup in renderToStaticMarkup — success/receipt
      // content must not appear.
      expect(html).not.toContain('Pro Plan Activated');
      expect(html).not.toContain('Payment Successful!');
    });

    it('does not render the receipt card when no token is present', () => {
      sessionStorage.removeItem('receipt_token');
      const html = renderAt('/payment-success?order_id=ord_FAKE&payment_id=pay_FAKE');
      expect(html).not.toContain('ord_FAKE');
      expect(html).not.toContain('pay_FAKE');
    });
  });

  // ── Case 1: all params present (subscription) ────────────────────────────
  describe('all params present — Pro subscription', () => {
    const url =
      '/payment-success?type=subscription&plan=pro&amount=99900&order_id=ord_ABC123&payment_id=pay_XYZ789';

    it('renders without crashing', () => {
      expect(() => renderAt(url)).not.toThrow();
    });

    it('shows the plan-specific heading', () => {
      const html = renderAt(url);
      expect(html).toContain('Pro Plan Activated');
    });

    it('shows the formatted amount', () => {
      const html = renderAt(url);
      // 99900 paise → ₹999
      expect(html).toContain('₹999');
    });

    it('shows the order ID', () => {
      const html = renderAt(url);
      expect(html).toContain('ord_ABC123');
    });

    it('shows the payment ID', () => {
      const html = renderAt(url);
      expect(html).toContain('pay_XYZ789');
    });

    it('shows the plan label in the receipt card', () => {
      const html = renderAt(url);
      expect(html).toContain('Pro Plan');
    });
  });

  // ── Case 2: only order_id present ────────────────────────────────────────
  describe('only order_id present', () => {
    const url = '/payment-success?order_id=ord_ONLY';

    it('renders without crashing', () => {
      expect(() => renderAt(url)).not.toThrow();
    });

    it('shows a generic success heading', () => {
      const html = renderAt(url);
      expect(html).toContain('Payment Successful!');
    });

    it('shows a generic success sub-message', () => {
      const html = renderAt(url);
      expect(html).toContain('Your payment was processed successfully.');
    });

    it('shows the order ID', () => {
      const html = renderAt(url);
      expect(html).toContain('ord_ONLY');
    });

    it('does not crash on missing amount or payment_id', () => {
      // ReceiptRow returns null when value is falsy — no row should throw
      const html = renderAt(url);
      expect(html.length).toBeGreaterThan(0);
    });
  });

  // ── Case 3: type=topup with credits ──────────────────────────────────────
  describe('type=topup with credits', () => {
    const url =
      '/payment-success?type=topup&credits=500&amount=19900&order_id=ord_TOP&payment_id=pay_TOP';

    it('renders without crashing', () => {
      expect(() => renderAt(url)).not.toThrow();
    });

    it('shows a credits-specific heading', () => {
      const html = renderAt(url);
      expect(html).toContain('500 Credits Added');
    });

    it('shows a credits-specific sub-message', () => {
      const html = renderAt(url);
      expect(html).toContain('500 AI credits have been added to your account.');
    });

    it('shows the formatted amount', () => {
      const html = renderAt(url);
      // 19900 paise → ₹199
      expect(html).toContain('₹199');
    });

    it('shows the credits count in the receipt card', () => {
      const html = renderAt(url);
      expect(html).toContain('500 credits');
    });

    it('shows order and payment IDs', () => {
      const html = renderAt(url);
      expect(html).toContain('ord_TOP');
      expect(html).toContain('pay_TOP');
    });
  });

  // ── Case 4: completely empty params ──────────────────────────────────────
  describe('completely empty params', () => {
    const url = '/payment-success';

    it('renders without crashing', () => {
      expect(() => renderAt(url)).not.toThrow();
    });

    it('shows a generic success heading', () => {
      const html = renderAt(url);
      expect(html).toContain('Payment Successful!');
    });

    it('shows a generic success sub-message', () => {
      const html = renderAt(url);
      expect(html).toContain('Your payment was processed successfully.');
    });

    it('still renders navigation links', () => {
      const html = renderAt(url);
      expect(html).toContain('/profile');
      expect(html).toContain('/chat');
    });

    it('renders an HTML string of meaningful length', () => {
      const html = renderAt(url);
      expect(html.length).toBeGreaterThan(100);
    });
  });

  // ── Edge cases ────────────────────────────────────────────────────────────
  describe('edge cases', () => {
    it('treats an unknown plan key as a missing plan (generic heading)', () => {
      const html = renderAt('/payment-success?plan=enterprise&type=subscription');
      expect(html).toContain('Payment Successful!');
    });

    it('treats a non-numeric amount as missing (no Amount paid row rendered)', () => {
      const html = renderAt('/payment-success?amount=bad_value');
      expect(html).not.toContain('Amount paid');
    });

    it('renders the Starter plan correctly', () => {
      const html = renderAt(
        '/payment-success?type=subscription&plan=starter&amount=9900',
      );
      expect(html).toContain('Starter Plan Activated');
      expect(html).toContain('₹99');
    });

    it('shows "Credits Added" fallback heading when credits param is non-numeric', () => {
      const html = renderAt('/payment-success?type=topup&credits=abc&amount=19900');
      expect(html).not.toContain('NaN');
      expect(html).toContain('Credits Added');
    });

    it('shows "Credits Added" fallback heading when credits param is missing', () => {
      const html = renderAt('/payment-success?type=topup&amount=19900');
      expect(html).not.toContain('NaN');
      expect(html).toContain('Credits Added');
    });

    it('does not render the Amount paid row when amount is zero', () => {
      const html = renderAt('/payment-success?amount=0&type=subscription&plan=pro');
      expect(html).not.toContain('Amount paid');
    });

    it('does not render the Amount paid row when amount is negative', () => {
      const html = renderAt('/payment-success?amount=-500&type=subscription&plan=pro');
      expect(html).not.toContain('Amount paid');
    });
  });
});
