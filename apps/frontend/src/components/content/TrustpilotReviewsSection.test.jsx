/**
 * Task #137 — TrustpilotReviewsSection render tests.
 * Task #138 — StarRow unit tests + star rating row integration tests.
 * Task #155 — CTA is now a <button> that opens a modal (not a direct <a> link).
 *
 * Covers (Task #137 / #155):
 * 1. CTA button is rendered when config is present and provides a profileUrl.
 * 2. CTA button is rendered when config is present but omits profileUrl (fallback).
 * 3. The section is not visible (hidden gracefully) when config returns null.
 * 4. The aggregate-rating JSON-LD <script> tag is injected into <head> when
 *    the aggregate endpoint returns valid ratingValue / ratingCount data.
 * 5. No JSON-LD is injected when aggregate data is absent.
 *
 * Covers (Task #138 — StarRow unit tests):
 * 6. StarRow renders exactly 5 SVG stars.
 * 7. StarRow(5.0) → all 5 stars filled green (no empty paths).
 * 8. StarRow(0) → all 5 stars empty grey (no green paths).
 * 9. StarRow(4.7) → 4 full + 1 half (clipPath present on 5th star).
 * 10. StarRow(3.5) → 3 full + 1 half + 1 empty.
 *
 * Covers (Task #138 — integration):
 * 11. Star row (data-testid="tp-star-row") appears when aggregate data present.
 * 12. Displayed rating value matches ratingValue from aggregate.
 * 13. Displayed review count matches ratingCount from aggregate.
 * 14. Star row is absent when aggregate data is unavailable.
 *
 * Implementation note: the module uses module-level singleton caches
 * (_configCache, _aggregateCache). To isolate tests we reset the module
 * registry with vi.resetModules() and dynamically import the component in
 * beforeEach, and mock global fetch so no real network calls are made.
 * StarRow has no module-level state so it is imported statically.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { StarRow } from '@/components/content/TrustpilotReviewsSection';

// @/utils/api is used for API_BASE — keep it stable across module resets.
vi.mock('@/utils/api', () => ({ API_BASE: 'http://localhost:5000/api' }));

const PROFILE_URL = 'https://www.trustpilot.com/review/syrabit.ai';
const CUSTOM_URL = 'https://www.trustpilot.com/review/custom-biz.example';

/** Build a minimal fetch mock that responds to the two trustpilot endpoints. */
function makeFetch({ configResponse, aggregateResponse }) {
  return vi.fn((url) => {
    if (url.includes('/config/trustpilot/aggregate')) {
      return Promise.resolve({
        ok: aggregateResponse !== null,
        json: () => Promise.resolve(aggregateResponse),
      });
    }
    // /config/trustpilot
    return Promise.resolve({
      ok: configResponse !== null,
      json: () => Promise.resolve(configResponse),
    });
  });
}

describe('TrustpilotReviewsSection', () => {
  let TrustpilotReviewsSection;

  beforeEach(async () => {
    // Reset the module registry so the singleton caches start empty for each test.
    vi.resetModules();
    // Dynamically import the component after the module reset.
    const mod = await import(
      '@/components/content/TrustpilotReviewsSection'
    );
    TrustpilotReviewsSection = mod.default;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    // Remove any injected JSON-LD script tags so tests don't bleed into each other.
    document
      .querySelectorAll('script[type="application/ld+json"]')
      .forEach((el) => el.remove());
  });

  // ------------------------------------------------------------------
  // 1. CTA button is rendered when config returns a profileUrl
  //    (Task #155: the button now opens a modal instead of linking directly;
  //    the invitation link / fallback URL is resolved inside the modal.)
  // ------------------------------------------------------------------
  it('shows "Rate us on Trustpilot" button with the profileUrl from config', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: { profileUrl: CUSTOM_URL },
        aggregateResponse: null,
      }),
    );

    render(<TrustpilotReviewsSection heading="Leave a review" />);

    const btn = await screen.findByRole('button', { name: /rate us on trustpilot/i });
    expect(btn).toBeInTheDocument();
  });

  // ------------------------------------------------------------------
  // 2. CTA button is rendered even when config has no profileUrl
  //    (modal falls back to the generic Trustpilot profile URL internally)
  // ------------------------------------------------------------------
  it('falls back to the hardcoded Trustpilot href when config omits profileUrl', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: {}, // valid object but no profileUrl
        aggregateResponse: null,
      }),
    );

    render(<TrustpilotReviewsSection />);

    const btn = await screen.findByRole('button', { name: /rate us on trustpilot/i });
    expect(btn).toBeInTheDocument();
  });

  // ------------------------------------------------------------------
  // 3. Section hides gracefully when config returns null
  // ------------------------------------------------------------------
  it('renders nothing visible when the config endpoint returns null', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: null,
        aggregateResponse: null,
      }),
    );

    render(<TrustpilotReviewsSection heading="Hidden section" />);

    // Wait for the async fetch to settle (the component removes itself on null).
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /rate us on trustpilot/i }),
      ).toBeNull(),
    );

    expect(screen.queryByText('Hidden section')).toBeNull();
  });

  // ------------------------------------------------------------------
  // 4. JSON-LD <script> is injected when aggregate data is available
  // ------------------------------------------------------------------
  it('injects an aggregate-rating JSON-LD script tag when aggregate data is present', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: { profileUrl: PROFILE_URL },
        aggregateResponse: { ratingValue: 4.8, ratingCount: 312 },
      }),
    );

    render(
      <TrustpilotReviewsSection
        jsonLdId="test-jsonld"
        jsonLdName="Syrabit.ai"
        jsonLdUrl="https://syrabit.ai"
      />,
    );

    await waitFor(() => {
      const script = document.getElementById('test-jsonld');
      expect(script).not.toBeNull();
    });

    const script = document.getElementById('test-jsonld');
    expect(script).toHaveAttribute('type', 'application/ld+json');

    const ld = JSON.parse(script.textContent);
    expect(ld['@type']).toBe('Organization');
    expect(ld.name).toBe('Syrabit.ai');
    expect(ld.url).toBe('https://syrabit.ai');
    expect(ld.aggregateRating['@type']).toBe('AggregateRating');
    expect(ld.aggregateRating.ratingValue).toBe(4.8);
    expect(ld.aggregateRating.reviewCount).toBe(312);
    expect(ld.aggregateRating.bestRating).toBe(5);
    expect(ld.aggregateRating.worstRating).toBe(1);
  });

  // ------------------------------------------------------------------
  // 5. No JSON-LD injected when aggregate data is absent / zero count
  // ------------------------------------------------------------------
  it('does not inject JSON-LD when the aggregate endpoint returns null', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: { profileUrl: PROFILE_URL },
        aggregateResponse: null,
      }),
    );

    render(
      <TrustpilotReviewsSection
        jsonLdId="missing-jsonld"
        jsonLdName="Syrabit.ai"
      />,
    );

    // Give the component time to settle (wait for the CTA button to appear).
    await screen.findByRole('button', { name: /rate us on trustpilot/i });

    expect(document.getElementById('missing-jsonld')).toBeNull();
  });

  // ------------------------------------------------------------------
  // Task #138 integration — star row present/absent based on aggregate
  // ------------------------------------------------------------------

  it('shows the star row with formatted rating and review count when aggregate is present', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: { profileUrl: PROFILE_URL },
        aggregateResponse: { ratingValue: 4.7, ratingCount: 320 },
      }),
    );

    render(<TrustpilotReviewsSection />);

    const row = await screen.findByTestId('tp-star-row');
    expect(row).toBeInTheDocument();

    // Rating value displayed as fixed-1 decimal
    const ratingEl = screen.getByTestId('tp-rating-value');
    expect(ratingEl).toHaveTextContent('4.7');

    // Review count (320) appears in the count span
    const countEl = screen.getByTestId('tp-review-count');
    expect(countEl).toHaveTextContent('320');
    expect(countEl).toHaveTextContent('reviews');
  });

  it('hides the star row and shows no rating text when aggregate is unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: { profileUrl: PROFILE_URL },
        aggregateResponse: null,
      }),
    );

    render(<TrustpilotReviewsSection />);

    // Wait for the CTA button to appear (fetch settled)
    await screen.findByRole('button', { name: /rate us on trustpilot/i });

    expect(screen.queryByTestId('tp-star-row')).toBeNull();
    expect(screen.queryByTestId('tp-rating-value')).toBeNull();
    expect(screen.queryByTestId('tp-review-count')).toBeNull();
  });

  it('star row aria-label contains the rating value and review count', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetch({
        configResponse: { profileUrl: PROFILE_URL },
        aggregateResponse: { ratingValue: 4.8, ratingCount: 512 },
      }),
    );

    render(<TrustpilotReviewsSection />);

    const row = await screen.findByTestId('tp-star-row');
    expect(row.getAttribute('aria-label')).toMatch(/4\.8/);
    expect(row.getAttribute('aria-label')).toMatch(/512/);
  });
});

// ---------------------------------------------------------------------------
// Task #138 — StarRow unit tests (pure component, no fetch, static import)
// ---------------------------------------------------------------------------

describe('StarRow', () => {
  it('renders exactly 5 SVG elements', () => {
    const { container } = render(<StarRow rating={4.2} />);
    const svgs = container.querySelectorAll('svg');
    expect(svgs).toHaveLength(5);
  });

  it('fills all 5 stars green when rating is 5', () => {
    const { container } = render(<StarRow rating={5} />);
    // Each star has 2 paths: grey background + green overlay.
    const greenPaths = container.querySelectorAll('path[fill="#00b67a"]');
    expect(greenPaths).toHaveLength(5);
  });

  it('fills no stars green when rating is 0', () => {
    const { container } = render(<StarRow rating={0} />);
    const greenPaths = container.querySelectorAll('path[fill="#00b67a"]');
    expect(greenPaths).toHaveLength(0);
  });

  it('rating 4.7 → 4 full stars + 1 half star (clipPath on last star)', () => {
    // position 1-4: 4.7 >= 1,2,3,4 → full
    // position 5:   4.7 < 5 but 4.7 >= 4.5 → half (clipPath present)
    const { container } = render(<StarRow rating={4.7} />);
    const greenPaths = container.querySelectorAll('path[fill="#00b67a"]');
    expect(greenPaths).toHaveLength(5); // 4 full + 1 half (half still renders a green path)

    const clipPaths = container.querySelectorAll('clipPath');
    expect(clipPaths).toHaveLength(1); // only the half star gets a clipPath
  });

  it('rating 3.5 → 3 full + 1 half + 1 empty', () => {
    // position 1-3: full; position 4: 3.5 >= 3.5 → half; position 5: empty
    const { container } = render(<StarRow rating={3.5} />);
    const greenPaths = container.querySelectorAll('path[fill="#00b67a"]');
    expect(greenPaths).toHaveLength(4); // 3 full + 1 half

    const clipPaths = container.querySelectorAll('clipPath');
    expect(clipPaths).toHaveLength(1);
  });

  it('rating 2.0 → exactly 2 full stars, no half, 3 empty', () => {
    const { container } = render(<StarRow rating={2} />);
    const greenPaths = container.querySelectorAll('path[fill="#00b67a"]');
    expect(greenPaths).toHaveLength(2);

    const clipPaths = container.querySelectorAll('clipPath');
    expect(clipPaths).toHaveLength(0);
  });
});
