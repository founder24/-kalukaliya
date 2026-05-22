/**
 * analytics.js — Syrabit.ai event tracking
 *
 * Tracks custom events to PostHog (already loaded in index.html).
 * Page-view tracking is handled by Cloudflare Web Analytics (beacon in index.html).
 *
 * Usage: import Analytics from '@/utils/analytics'; Analytics.signup(email);
 */

// ── Internal helpers ─────────────────────────────────────────────────────────

const track = (event, properties = {}) => {
  try {
    if (window.posthog && typeof window.posthog.capture === 'function') {
      window.posthog.capture(event, {
        app: 'syrabit.ai',
        timestamp: new Date().toISOString(),
        ...properties,
      });
    }
  } catch (e) {
    if (import.meta.env.DEV) console.debug('[PostHog]', event, properties);
  }
  // Task #408: also mirror hydrate-lifecycle events to our own backend
  // so the admin dashboard can render an ops-health tile without
  // depending on PostHog's API. Best-effort, fire-and-forget.
  if (typeof event === 'string' && event.startsWith('hydrate_')) {
    try { mirrorHydrateEvent(event, properties); } catch {}
  }
  // Task #551: mirror ad-viewability pings to /analytics/ad-impression
  // so the admin Ads dashboard can roll up impressions per network +
  // placement without a PostHog API integration.
  if (event === 'ad_slot_viewed') {
    try { mirrorAdImpression(properties); } catch {}
  }
  // Task #654: mirror review-prompt funnel events to our backend so the
  // admin dashboard can render shown / clicked / dismissed counts +
  // CTR + per-reason breakdown without a PostHog API integration.
  if (typeof event === 'string' && event.startsWith('review_prompt_')) {
    try { mirrorReviewPromptEvent(event, properties); } catch {}
  }
};

let _reviewPromptMirrorBlocked = false;
const mirrorReviewPromptEvent = (event, properties) => {
  if (_reviewPromptMirrorBlocked) return;
  if (typeof window === 'undefined') return;
  try {
    const apiBase =
      (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_BACKEND_URL)
        ? `${import.meta.env.VITE_BACKEND_URL.replace(/\/$/, '')}/api`
        : '/api';
    const payload = JSON.stringify({
      event,
      reason: (properties && properties.reason) || null,
    });
    const url = `${apiBase}/analytics/review-prompt-event`;
    const blob = new Blob([payload], { type: 'application/json' });
    if (navigator.sendBeacon && navigator.sendBeacon(url, blob)) return;
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
      credentials: 'omit',
    }).catch(() => { _reviewPromptMirrorBlocked = true; });
  } catch {
    _reviewPromptMirrorBlocked = true;
  }
};

let _adMirrorBlocked = false;
const mirrorAdImpression = (properties) => {
  if (_adMirrorBlocked) return;
  if (typeof window === 'undefined') return;
  if (!properties || !properties.placement || !properties.network) return;
  try {
    const apiBase =
      (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_BACKEND_URL)
        ? `${import.meta.env.VITE_BACKEND_URL.replace(/\/$/, '')}/api`
        : '/api';
    // ``enabled`` used to be in this payload but it was always true
    // at the AdSlot call site (the IntersectionObserver only fires
    // after a cfg.enabled gate) and the backend never read it back.
    // Dropped in the admin-panel audit; backend ignores the extra
    // key from older bundles, so this is a one-sided rollout.
    const payload = JSON.stringify({
      placement: properties.placement,
      network: properties.network,
    });
    const url = `${apiBase}/analytics/ad-impression`;
    const blob = new Blob([payload], { type: 'application/json' });
    if (navigator.sendBeacon && navigator.sendBeacon(url, blob)) return;
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
      credentials: 'omit',
    }).catch(() => { _adMirrorBlocked = true; });
  } catch {
    _adMirrorBlocked = true;
  }
};

// Internal: tiny beacon to /api/analytics/hydrate-event. Uses
// sendBeacon when available (survives page unload during reload),
// otherwise fetch with keepalive. Guarded against repeated failures
// (e.g. backend down) to avoid noisy retries on every event.
let _hydrateMirrorBlocked = false;
const mirrorHydrateEvent = (event, properties) => {
  if (_hydrateMirrorBlocked) return;
  if (typeof window === 'undefined') return;
  try {
    // Resolve API base from the existing axios setup if possible,
    // else fall back to same-origin /api.
    const apiBase =
      (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_BACKEND_URL)
        ? `${import.meta.env.VITE_BACKEND_URL.replace(/\/$/, '')}/api`
        : '/api';
    const payload = JSON.stringify({
      event,
      kind: properties?.kind ?? null,
      path: properties?.path ?? (typeof location !== 'undefined' ? location.pathname : null),
      auto_reload: properties?.auto_reload ?? null,
      preload_failed: properties?.preload_failed ?? null,
      message: properties?.message ?? null,
      name: properties?.error_name ?? properties?.name ?? null,
      elapsed_ms: properties?.elapsed_ms ?? null,
      ms_since_reload: properties?.ms_since_reload ?? null,
    });
    const url = `${apiBase}/analytics/hydrate-event`;
    const blob = new Blob([payload], { type: 'application/json' });
    if (navigator.sendBeacon && navigator.sendBeacon(url, blob)) return;
    // Fallback — keepalive lets the request survive page unload.
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
      credentials: 'omit',
    }).catch(() => { _hydrateMirrorBlocked = true; });
  } catch {
    _hydrateMirrorBlocked = true;
  }
};

const identify = (userId, traits = {}) => {
  try {
    if (window.posthog) window.posthog.identify(userId, traits);
  } catch {}
};

const reset = () => {
  try { if (window.posthog) window.posthog.reset(); } catch {}
};

const _getAttribution = () => {
  try {
    const params = new URLSearchParams(window.location.search);
    const curSource = params.get('utm_source') || params.get('ref') || '';
    const curMedium = params.get('utm_medium') || '';
    const curCampaign = params.get('utm_campaign') || '';
    const curContent = params.get('utm_content') || '';
    const curTerm = params.get('utm_term') || '';

    if (curSource && !localStorage.getItem('syrabit_ft_utm_source')) {
      localStorage.setItem('syrabit_ft_utm_source', curSource);
      localStorage.setItem('syrabit_ft_utm_medium', curMedium);
      localStorage.setItem('syrabit_ft_utm_campaign', curCampaign);
      localStorage.setItem('syrabit_ft_utm_content', curContent);
      localStorage.setItem('syrabit_ft_utm_term', curTerm);
      localStorage.setItem('syrabit_ft_landing_page', window.location.pathname);
      localStorage.setItem('syrabit_ft_timestamp', new Date().toISOString());
    }

    if (curSource) {
      sessionStorage.setItem('syrabit_utm_source', curSource);
      sessionStorage.setItem('syrabit_utm_medium', curMedium);
      sessionStorage.setItem('syrabit_utm_campaign', curCampaign);
    }
    if (!sessionStorage.getItem('syrabit_landing_page')) {
      sessionStorage.setItem('syrabit_landing_page', window.location.pathname);
    }

    const ftSource = localStorage.getItem('syrabit_ft_utm_source') || '';
    const ftMedium = localStorage.getItem('syrabit_ft_utm_medium') || '';
    const ftCampaign = localStorage.getItem('syrabit_ft_utm_campaign') || '';
    const ftLanding = localStorage.getItem('syrabit_ft_landing_page') || '';

    const source = ftSource || sessionStorage.getItem('syrabit_utm_source') || '';
    const medium = ftMedium || sessionStorage.getItem('syrabit_utm_medium') || '';
    const campaign = ftCampaign || sessionStorage.getItem('syrabit_utm_campaign') || '';
    const landing = ftLanding || sessionStorage.getItem('syrabit_landing_page') || '';
    return [source, medium, campaign, landing].filter(Boolean).join(' | ') || 'direct';
  } catch { return 'direct'; }
};

// ── Public API ───────────────────────────────────────────────────────────────

export const Analytics = {

  // ── Page views ─────────────────────────────────────────────────────────────
  pageView: (path, title) => {
    track('$pageview', { path: path || window.location.pathname });
  },

  // ── Auth ───────────────────────────────────────────────────────────────────
  signup: (email, plan = 'free') => {
    track('user_signed_up', { email_domain: email.split('@')[1], plan, attribution_source: _getAttribution() });
    identify(email, { plan, signed_up_at: new Date().toISOString() });
  },

  login: (userId, email) => {
    track('user_logged_in', { user_id: userId });
    identify(userId, { email, last_login: new Date().toISOString() });
  },

  logout: () => {
    track('user_logged_out');
    reset();
  },

  onboardingComplete: (boardName, className, streamName) => {
    track('onboarding_completed', { board: boardName, class: className, stream: streamName });
  },

  // ── Chat ───────────────────────────────────────────────────────────────────
  chatStart: (subjectId, subjectName, model) => {
    track('chat_started', { subject_id: subjectId, subject: subjectName, model });
  },

  chatMessage: (source, creditsRemaining, model) => {
    track('chat_message_sent', { source, credits_remaining: creditsRemaining, model });
  },

  chatCreditsExhausted: () => {
    track('credits_exhausted');
  },

  chapterView: (chapterId, chapterTitle, subjectName, board, wordCount) => {
    track('chapter_viewed', { chapter_id: chapterId, chapter: chapterTitle, subject: subjectName, board, word_count: wordCount });
  },

  chapterShare: (chapterTitle, url) => {
    track('chapter_shared', { chapter: chapterTitle, url });
  },

  chapterRetry: (chapterSlug) => {
    track('chapter_retry', { chapter: chapterSlug });
  },

  chapterAskAi: (subjectSlug, chapterTitle) => {
    track('chapter_ask_ai_clicked', { subject: subjectSlug, chapter: chapterTitle });
  },

  tocClick: (heading, chapterTitle) => {
    track('toc_click', { heading, chapter: chapterTitle });
  },

  scrollDepth: (depth, chapterTitle) => {
    track('scroll_depth', { depth, chapter: chapterTitle });
  },

  searchUsed: (query, resultCount) => {
    track('search_used', { query, result_count: resultCount });
  },

  // ── Library ────────────────────────────────────────────────────────────────
  subjectBookmarked: (subjectName, saved) => {
    track('subject_bookmarked', { subject: subjectName, action: saved ? 'save' : 'unsave' });
  },

  subjectOpened: (subjectId, subjectName) => {
    track('subject_opened', { subject_id: subjectId, subject: subjectName });
  },

  subjectShared: (subjectName, url) => {
    track('subject_shared', { subject: subjectName, url });
  },

  // ── SEO Content pages ──────────────────────────────────────────────────────
  seoPageView: (board, classSlug, subjectSlug, topicSlug, pageType) => {
    track('seo_page_viewed', { board, class: classSlug, subject: subjectSlug, topic: topicSlug, type: pageType });
  },

  // ── Payment ────────────────────────────────────────────────────────────────
  // Task #731 S7 — defensive normalizer for the `priceInr` argument.
  //
  // The 4 call sites in ProfilePage.jsx (Razorpay full plan + 3 top-up
  // paths) pass `orderData.amount` directly, and Razorpay returns that
  // amount in PAISE (so a ₹99 plan shows up as 9900). For ~6 months the
  // analytics events have therefore been emitting `price_inr=9900`,
  // making PostHog/GA4 revenue dashboards 100x too high.
  //
  // Rather than rely on every future caller remembering to divide by
  // 100, we normalize here: any value above PAISE_THRESHOLD that's also
  // an exact multiple of 100 is treated as paise and converted to
  // rupees. This is safe because (a) Razorpay paise are always integer
  // multiples of 100 and (b) no real plan price exceeds ₹50000 — well
  // below the 5_000_000 paise floor a future ₹50000 plan would have to
  // exceed to misclassify.
  _normalizePriceInr: (v) => {
    if (typeof v !== "number" || !Number.isFinite(v)) return v;
    const PAISE_THRESHOLD = 50_000;       // ₹50000 max plausible plan price
    if (v >= PAISE_THRESHOLD && v % 100 === 0) return v / 100;
    return v;
  },
  upgradeInitiated: (plan, priceInr) => {
    track('upgrade_initiated', { plan, price_inr: Analytics._normalizePriceInr(priceInr), attribution_source: _getAttribution() });
  },

  purchaseComplete: (plan, priceInr, orderId) => {
    track('purchase_completed', { plan, price_inr: Analytics._normalizePriceInr(priceInr), order_id: orderId, attribution_source: _getAttribution() });
  },

  purchaseFailed: (plan, reason, orderId) => {
    track('purchase_failed', { plan, reason, order_id: orderId, attribution_source: _getAttribution() });
  },

  paymentModalClosed: (plan) => {
    track('payment_modal_closed', { plan, attribution_source: _getAttribution() });
  },

  // ── Admin ──────────────────────────────────────────────────────────────────
  // ── PWA ────────────────────────────────────────────────────────────────
  pwaPromptShown: () => {
    track('pwa_prompt_shown');
  },

  pwaInstalled: () => {
    track('pwa_installed');
  },

  pwaPromptDismissed: () => {
    track('pwa_prompt_dismissed');
  },

  // ── Trustpilot review prompt (Task #652; relabeled #726, migrated #724) ──
  // Fired when the in-app prompt asking engaged students to leave a
  // Trustpilot review is shown / clicked through / dismissed. The CTA opens the
  // Trustpilot "write a review" form in a new tab — Trustpilot does not
  // permit posting reviews via API, so click-through is the closest
  // proxy we have for measuring conversion to actual reviews. Event
  // names (review_prompt_*) are intentionally generic and unchanged
  // from the Google-era so historical funnels keep working.
  reviewPromptShown: (reason) => {
    track('review_prompt_shown', { reason });
  },

  reviewPromptClicked: (reason) => {
    track('review_prompt_clicked', { reason });
  },

  reviewPromptDismissed: (reason) => {
    track('review_prompt_dismissed', { reason });
  },

  getFirstTouchAttribution: () => {
    try {
      return {
        source: localStorage.getItem('syrabit_ft_utm_source') || '',
        medium: localStorage.getItem('syrabit_ft_utm_medium') || '',
        campaign: localStorage.getItem('syrabit_ft_utm_campaign') || '',
        content: localStorage.getItem('syrabit_ft_utm_content') || '',
        term: localStorage.getItem('syrabit_ft_utm_term') || '',
        landing_page: localStorage.getItem('syrabit_ft_landing_page') || '',
        timestamp: localStorage.getItem('syrabit_ft_timestamp') || '',
      };
    } catch { return {}; }
  },

  // ── Ads (Task #528) ──────────────────────────────────────────────────────
  // Fired once per AdSlot mount when the slot first crosses 50% in-viewport.
  // Gated by ad consent in the caller so opt-out users emit nothing.
  // ``enabled`` was dropped — see mirrorAdImpression() comment for
  // rationale (always true at call site + never read by backend).
  adSlotViewed: ({ placement, network } = {}) => {
    track('ad_slot_viewed', { placement, network });
  },

  adminLogin: (email) => {
    track('admin_logged_in', { email_domain: email.split('@')[1] });
  },

  // ── Hydration / page-chunk preload health (Task #405) ─────────────────────
  // Fired when the per-page chunk preload `import()` rejects in the
  // browser before hydrateRoot() runs (chunk 404 from a stale build,
  // network blip, integrity mismatch). We hydrate anyway and the
  // Suspense fallback shows a recovery hint, but tracking these lets
  // us spot regressions in production.
  hydratePreloadFailed: ({ kind, path, message, name, auto_reload } = {}) => {
    // Preserve `auto_reload` so Task #407 auto-reload attempts can be
    // distinguished from manual-recovery failures in the admin
    // dashboard (Task #408). Drop undefined so the backend-mirror
    // payload doesn't force `null` on every event.
    const payload = { kind, path, message, error_name: name };
    if (auto_reload !== undefined) payload.auto_reload = auto_reload;
    track('hydrate_preload_failed', payload);
  },

  // Fired when hydration is still showing the Suspense fallback after
  // the recovery threshold (~5s) — i.e. the user is staring at a
  // loading state for an unusually long time.
  hydrateStalled: ({ kind, path, ms, preload_failed } = {}) => {
    const payload = { kind, path, elapsed_ms: ms };
    if (preload_failed !== undefined) payload.preload_failed = preload_failed;
    track('hydrate_stalled', payload);
  },

  // Fired when a Task #407 stale-chunk auto-reload was followed by a
  // healthy hydration on the next page load. Lets us measure
  // auto-reload effectiveness (recoveries / attempts) and detect
  // false-positive auto-reloads.
  hydrateRecovered: ({ kind, path, reload_at, ms_since_reload } = {}) => {
    track('hydrate_recovered', {
      kind,
      path,
      reload_at,
      ms_since_reload,
    });
  },
};

export default Analytics;
