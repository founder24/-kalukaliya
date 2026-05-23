/**
 * GlobalTrustpilotJsonLd — Task #727
 *
 * Mounts the Organization-level Trustpilot aggregate-rating JSON-LD on
 * every route in the app, not just the 5 content pages that render
 * <TrustpilotReviewsSection /> (Landing, Library, Subject, Chapter,
 * PYQ — Tasks #724/#725). This makes the FAQ page, About page,
 * Pricing page, Learn/blog routes, and any other indexable route
 * eligible for the Trustpilot star rich snippet in Google search
 * results.
 *
 * The injected <script type="application/ld+json"> uses a distinct id
 * so it can coexist harmlessly with the per-page JSON-LD already
 * emitted inside TrustpilotReviewsSection — Google merges duplicate
 * Organization schemas with identical aggregateRating values.
 *
 * Aggregate rating data is fetched once per app session via the
 * shared `fetchTrustpilotAggregateOnce()` cache, so this global mount
 * adds zero extra network cost on top of the existing per-page
 * fetches.
 */
import { useEffect, useState } from 'react';
import {
  TrustpilotAggregateRatingJsonLd,
  fetchTrustpilotAggregateOnce,
} from '@/components/content/TrustpilotReviewsSection';

export default function GlobalTrustpilotJsonLd() {
  const [aggregate, setAggregate] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchTrustpilotAggregateOnce().then((a) => {
      if (!cancelled && a) setAggregate(a);
    });
    return () => { cancelled = true; };
  }, []);

  if (!aggregate) return null;
  return (
    <TrustpilotAggregateRatingJsonLd
      id="trustpilot-aggregaterating-jsonld-global"
      ratingValue={aggregate.ratingValue}
      ratingCount={aggregate.ratingCount}
    />
  );
}
