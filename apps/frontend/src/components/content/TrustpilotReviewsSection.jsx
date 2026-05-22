/**
 * TrustpilotReviewsSection — Task #126 / Task #138
 *
 * Renders a review-collection CTA that invites students to leave a
 * Trustpilot review for Syrabit.ai. The Trustpilot embed widget and its
 * bootstrap script have been removed; this section now shows a styled
 * card with a direct link to the Trustpilot review submission page.
 *
 * Task #138: when aggregate data is available (ratingValue + ratingCount),
 * the card shows a live star-rating row (e.g. ★★★★½ 4.7 · 320 reviews)
 * above the CTA button using data already fetched — no extra API call.
 * When aggregate data is unavailable the card renders without the rating
 * row so there is no layout shift.
 *
 * The profile URL is sourced from the server config endpoint so it
 * stays in sync with the backend Trustpilot secret — with a hardcoded
 * fallback so the CTA always renders even before the fetch resolves.
 *
 * The aggregate rating JSON-LD (for SEO / Google stars) is preserved
 * whenever the backend returns valid aggregate data.
 *
 * If the config endpoint returns null (Trustpilot not configured on the
 * server) the section hides itself gracefully — same behaviour as before.
 */
import { useEffect, useId, useState } from 'react';
import { API_BASE } from '@/utils/api';
import TrustpilotReviewModal from './TrustpilotReviewModal';

let _configPromise = null;
let _configCache = null;

export function fetchTrustpilotConfigOnce() {
  if (_configCache) return Promise.resolve(_configCache);
  if (_configPromise) return _configPromise;
  _configPromise = fetch(`${API_BASE}/config/trustpilot`, { credentials: 'omit' })
    .then((r) => (r.ok ? r.json() : null))
    .then((json) => {
      _configCache = json && typeof json === 'object' ? json : null;
      return _configCache;
    })
    .catch(() => null)
    .finally(() => { _configPromise = null; });
  return _configPromise;
}

let _aggregatePromise = null;
let _aggregateCache = null;

export function fetchTrustpilotAggregateOnce() {
  if (_aggregateCache) return Promise.resolve(_aggregateCache);
  if (_aggregatePromise) return _aggregatePromise;
  _aggregatePromise = fetch(`${API_BASE}/config/trustpilot/aggregate`, { credentials: 'omit' })
    .then((r) => (r.ok ? r.json() : null))
    .then((json) => {
      if (
        json &&
        typeof json === 'object' &&
        typeof json.ratingValue === 'number' &&
        typeof json.ratingCount === 'number' &&
        json.ratingCount > 0
      ) {
        _aggregateCache = json;
      } else {
        _aggregateCache = null;
      }
      return _aggregateCache;
    })
    .catch(() => null)
    .finally(() => { _aggregatePromise = null; });
  return _aggregatePromise;
}

/**
 * StarRow — Task #138
 *
 * Renders 5 SVG stars reflecting `rating` (0–5, decimal supported).
 * Each star position can be full, half, or empty:
 *   full  → rating >= position
 *   half  → rating >= position - 0.5 (but < position)
 *   empty → otherwise
 *
 * A per-star SVG clipPath drives partial fills — no Unicode hacks.
 * Trustpilot green (#00b67a) for filled portions; muted grey for empty.
 */
export function StarRow({ rating, className = '' }) {
  // useId produces a per-instance unique prefix so clip-path IDs never
  // collide when multiple StarRow instances are rendered on the same page.
  const uid = useId();

  const stars = [1, 2, 3, 4, 5].map((pos) => {
    let fill = 'empty';
    if (rating >= pos) fill = 'full';
    else if (rating >= pos - 0.5) fill = 'half';
    return fill;
  });

  return (
    <span
      className={`inline-flex items-center gap-0.5 ${className}`}
      aria-hidden="true"
    >
      {stars.map((fill, i) => {
        const clipId = `${uid}-tp-star-clip-${i}`;
        return (
          <svg
            key={i}
            viewBox="0 0 24 24"
            className="w-5 h-5 shrink-0"
            xmlns="http://www.w3.org/2000/svg"
          >
            {fill === 'half' && (
              <defs>
                <clipPath id={clipId}>
                  <rect x="0" y="0" width="12" height="24" />
                </clipPath>
              </defs>
            )}
            {/* Empty star background */}
            <path
              d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"
              fill="#d4d4d4"
            />
            {/* Filled overlay — full or half via clipPath */}
            {fill !== 'empty' && (
              <path
                d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"
                fill="#00b67a"
                clipPath={fill === 'half' ? `url(#${clipId})` : undefined}
              />
            )}
          </svg>
        );
      })}
    </span>
  );
}

export default function TrustpilotReviewsSection({
  heading = 'Share your experience',
  subheading = '',
  jsonLdId,
  jsonLdName,
  jsonLdUrl,
  subjectName = '',
  boardName = '',
  className = '',
}) {
  const [config, setConfig] = useState(_configCache);
  const [aggregate, setAggregate] = useState(_aggregateCache);
  const [failed, setFailed] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchTrustpilotConfigOnce().then((c) => {
      if (cancelled) return;
      setConfig(c);
      if (!c) setFailed(true);
    });
    fetchTrustpilotAggregateOnce().then((a) => {
      if (cancelled) return;
      if (a) setAggregate(a);
    });
    return () => { cancelled = true; };
  }, []);

  const profileUrl = config?.profileUrl || 'https://www.trustpilot.com/review/syrabit.ai';

  const jsonLd = aggregate ? (
    <TrustpilotAggregateRatingJsonLd
      id={jsonLdId}
      name={jsonLdName}
      url={jsonLdUrl}
      ratingValue={aggregate.ratingValue}
      ratingCount={aggregate.ratingCount}
    />
  ) : null;

  if (failed) return jsonLd;

  return (
    <>
      <section
        className="mt-12 max-w-5xl mx-auto px-4"
        aria-label="Leave a Trustpilot review"
      >
        {jsonLd}
        <div className="rounded-3xl border border-border/40 bg-gradient-to-br from-emerald-50/40 via-background to-violet-50/30 p-6 sm:p-8">
          <div className="mb-5">
            <h2 className="text-xl sm:text-2xl font-bold text-foreground">{heading}</h2>
            {subheading && (
              <p className="text-sm text-muted-foreground mt-1">{subheading}</p>
            )}
          </div>

          {/* Task #138 — live star rating row */}
          {aggregate && (
            <div
              className="flex items-center gap-2 mb-4"
              aria-label={`Rated ${aggregate.ratingValue.toFixed(1)} out of 5 from ${aggregate.ratingCount.toLocaleString()} reviews`}
              data-testid="tp-star-row"
            >
              <StarRow rating={aggregate.ratingValue} />
              <span className="text-sm font-semibold text-foreground" data-testid="tp-rating-value">
                {aggregate.ratingValue.toFixed(1)}
              </span>
              <span className="text-sm text-muted-foreground" data-testid="tp-review-count">
                &middot; {aggregate.ratingCount.toLocaleString()} reviews
              </span>
            </div>
          )}

          {/* Task #155 — open modal instead of direct external link */}
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-[#00b67a] hover:bg-[#00a368] active:bg-[#008f5a] transition-colors px-5 py-2.5 text-sm font-semibold text-white shadow-sm"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="currentColor"
              className="w-4 h-4 shrink-0"
            >
              <path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z" />
            </svg>
            Rate us on Trustpilot
          </button>
        </div>
      </section>

      <TrustpilotReviewModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        subjectName={subjectName}
        boardName={boardName}
        className={className}
      />
    </>
  );
}

/**
 * Optional aggregate rating JSON-LD wrapper. Trustpilot does not expose
 * aggregate ratings client-side via the embed widget, so unless a
 * `ratingValue` + `ratingCount` are explicitly provided we render
 * nothing — better to ship no aggregate-rating schema than stale or
 * fabricated numbers that could trigger search-console structured data
 * warnings. (Replaces the Google Places-backed
 * `ReviewsAggregateRatingJsonLd` from Task #652.)
 */
export function TrustpilotAggregateRatingJsonLd({
  id,
  name,
  url,
  ratingValue = null,
  ratingCount = null,
}) {
  useEffect(() => {
    if (typeof document === 'undefined') return;
    if (ratingValue == null || ratingCount == null) return;
    const elId = id || 'trustpilot-aggregaterating-jsonld';
    document.getElementById(elId)?.remove();
    const node = {
      '@context': 'https://schema.org',
      '@type': 'Organization',
      name: name || 'Syrabit.ai',
      url: url || (typeof window !== 'undefined' ? window.location.href : 'https://syrabit.ai'),
      aggregateRating: {
        '@type': 'AggregateRating',
        ratingValue: Number(ratingValue),
        reviewCount: Number(ratingCount),
        bestRating: 5,
        worstRating: 1,
      },
    };
    const s = document.createElement('script');
    s.type = 'application/ld+json';
    s.id = elId;
    s.text = JSON.stringify(node);
    document.head.appendChild(s);
    return () => { document.getElementById(elId)?.remove(); };
  }, [id, name, url, ratingValue, ratingCount]);
  return null;
}
