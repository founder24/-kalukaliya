/**
 * adsConfig.js — single source of truth for the ad stack on Syrabit.ai.
 *
 * Each placement key is wired to exactly one network. Real publisher IDs
 * and script URLs are read from `import.meta.env.VITE_ADS_*` env vars.
 * If any required value for a placement is missing, `getAdConfig()`
 * returns `{ enabled: false }` and `<AdSlot />` renders nothing — no
 * layout shift, no script tag injected.
 *
 * Routes that intentionally have NO ads:
 *   - /chat       (ChatPage)
 *   - /library    (LibraryPage)
 *   - /browser    (LibraryPage alias)
 *   - /:board/... (ChapterPage and friends)
 *
 * Adding/removing a network or placement is a one-file change here.
 * See ADS.md for the full list of env vars per network.
 */

const env = (typeof import.meta !== 'undefined' && import.meta.env) || {};

// ── Per-network defaults ─────────────────────────────────────────────────────
// Reserved heights are chosen to match the IAB sizes the networks serve in
// practice. They are kept identical whether the slot is enabled or not so
// the layout is stable from first paint.
const NETWORKS = {
  adpushup: {
    scriptUrl: env.VITE_ADS_ADPUSHUP_SCRIPT_URL || '',
    publisherId: env.VITE_ADS_ADPUSHUP_PUBLISHER_ID || '',
  },
  adsterra: {
    scriptUrl: env.VITE_ADS_ADSTERRA_SCRIPT_URL || '',
  },
  propellerads: {
    scriptUrl: env.VITE_ADS_PROPELLERADS_SCRIPT_URL || '',
  },
  // Google AdSense (Task #550) — Auto Ads runs page-level via
  // `useAdsenseAutoAds`. Per-slot manual units are also supported and
  // stay disabled (no reserved space, no script tag) until per-slot
  // `data-ad-slot` env vars are provided. The page-level script URL is
  // the AdSense loader pinned to our publisher client; same URL is used
  // by both the auto-ads hook and any per-slot `<AdSlot />` units, so
  // the in-module dedupe Set in `<AdSlot />` keeps it loaded once.
  adsense: {
    scriptUrl: 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8958003374183515',
    publisherId: 'ca-pub-8958003374183515',
    crossorigin: 'anonymous',
  },
};

// ── Per-placement wiring ─────────────────────────────────────────────────────
// Notes (`/learn/...`) and PYQ (`/pyq/...`) are the *only* monetised
// surfaces on Syrabit.ai (Task #542). Both are intentionally ad-dense:
// top, mid and end slots on PYQ; top, mid, after-PYQs, after-flashcards,
// end and a desktop sidebar on Notes. All other routes (chat, library,
// browser, chapter) stay ad-free — see `scripts/verify-no-ads.mjs`.
const PLACEMENTS = {
  // ── PYQ pages — premium display demand (AdPushup / Magnite). ──────────────
  'pyq.topOfContent': {
    network: 'adpushup',
    slotId: env.VITE_ADS_ADPUSHUP_PYQ_TOP_ZONE || '',
    height: 250,
    label: 'Advertisement',
  },
  'pyq.inContent': {
    network: 'adpushup',
    slotId: env.VITE_ADS_ADPUSHUP_PYQ_INCONTENT_ZONE || '',
    height: 280,
    label: 'Advertisement',
  },
  'pyq.endOfContent': {
    network: 'adpushup',
    slotId: env.VITE_ADS_ADPUSHUP_PYQ_END_ZONE || '',
    height: 280,
    label: 'Advertisement',
  },

  // ── Notes / Learn pages — Adsterra (in-content) + PropellerAds (end). ─────
  'learn.topOfContent': {
    network: 'adsterra',
    slotId: env.VITE_ADS_ADSTERRA_LEARN_TOP_ZONE || '',
    height: 250,
    label: 'Advertisement',
  },
  'learn.inContent': {
    network: 'adsterra',
    slotId: env.VITE_ADS_ADSTERRA_LEARN_INCONTENT_ZONE || '',
    height: 250,
    label: 'Advertisement',
  },
  'learn.afterPyqs': {
    network: 'propellerads',
    slotId: env.VITE_ADS_PROPELLERADS_LEARN_AFTER_PYQS_ZONE || '',
    height: 250,
    label: 'Advertisement',
  },
  'learn.afterFlashcards': {
    network: 'adsterra',
    slotId: env.VITE_ADS_ADSTERRA_LEARN_AFTER_FLASHCARDS_ZONE || '',
    height: 250,
    label: 'Advertisement',
  },
  'learn.endOfContent': {
    network: 'propellerads',
    slotId: env.VITE_ADS_PROPELLERADS_LEARN_END_ZONE || '',
    height: 250,
    label: 'Advertisement',
  },
  // Desktop-only sidebar skyscraper. The page only mounts this slot at
  // `lg:` breakpoints, so mobile/tablet viewports never reserve the
  // 600px column.
  'learn.sidebar': {
    network: 'adsterra',
    slotId: env.VITE_ADS_ADSTERRA_LEARN_SIDEBAR_ZONE || '',
    height: 600,
    label: 'Advertisement',
  },

  // ── AdSense per-slot units (Task #550) ────────────────────────────────────
  // Optional manual AdSense placements. Stay disabled (no reserved
  // space, no script tag) until the per-slot `data-ad-slot` env var is
  // provided. AdSense Auto Ads runs unconditionally on the same routes
  // via `useAdsenseAutoAds`, so leaving these empty still nets full
  // AdSense coverage on Notes + PYQ — the per-slot keys are an
  // override for ad-ops to target specific positions if/when desired.
  // Display units — fixed-height banner areas (top, end, sidebar).
  // adFormat "auto" + full-width-responsive lets Google pick the best
  // IAB size (typically 320×50 mobile banner or 300×250 rectangle).
  'pyq.adsense.top': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_PYQ_TOP_SLOT || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  'pyq.adsense.end': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_PYQ_END_SLOT || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  'learn.adsense.top': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_LEARN_TOP_SLOT || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  'learn.adsense.end': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_LEARN_END_SLOT || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  // Desktop-only skyscraper — hidden on mobile via `hidden lg:flex` in LearnPage.
  'learn.adsense.sidebar': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_LEARN_SIDEBAR_SLOT || '',
    height: 600,
    label: 'Advertisement',
    adFormat: 'auto',
  },

  // In-article fluid units — mid-content slots served between paragraphs.
  // adFormat "fluid" + adLayout "in-article" is Google's recommended
  // format for mobile reading: the ad matches the surrounding text width,
  // Google picks the height, and there is NO reserved minHeight so the
  // page never holds blank space when Google decides not to fill the slot.
  // These placements account for the majority of impressions on mobile.
  'pyq.adsense.inContent': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_PYQ_INCONTENT_SLOT || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'learn.adsense.inContent': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_LEARN_INCONTENT_SLOT || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'learn.adsense.afterPyqs': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_LEARN_AFTER_PYQS_SLOT || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'learn.adsense.afterFlashcards': {
    network: 'adsense',
    slotId: env.VITE_ADS_ADSENSE_LEARN_AFTER_FLASHCARDS_SLOT || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
};

// ── Opt-out flag (Task #527) ─────────────────────────────────────────────────
// User-controlled localStorage flag. Read by `adsConsentGranted()` below and
// toggled from the Privacy section on the Profile page.
const ADS_OPT_OUT_KEY = 'syrabit_ads_optout';

export function getAdsOptOut() {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(ADS_OPT_OUT_KEY) === '1';
  } catch {
    return false;
  }
}

export function setAdsOptOut(optedOut) {
  if (typeof window === 'undefined') return;
  try {
    if (optedOut) {
      window.localStorage.setItem(ADS_OPT_OUT_KEY, '1');
    } else {
      window.localStorage.removeItem(ADS_OPT_OUT_KEY);
    }
    // Unified consent-change event so `<AdSlot />` and the page-level
    // hooks (`useAdsenseAutoAds`, `useQuge5Multitag`) can re-evaluate
    // `adsConsentGranted()` and tear down already-injected scripts
    // when the user toggles the privacy opt-out mid-session — Task #555.
    window.dispatchEvent(
      new CustomEvent('syrabit:ads-consent-changed', {
        detail: { reason: 'optout', optedOut },
      })
    );
  } catch {
    /* ignore storage failures */
  }
}

// Snapshot of the local opt-out value as it stood when the JS bundle
// first loaded — i.e. before any server hydration overwrites it. The
// one-time cross-device announcement (Task #532) needs to know the
// pre-sync state so legacy users with local-only opt-outs are still
// detected even after `hydrateAdsOptOutFromServer()` has clobbered the
// localStorage flag. Captured eagerly so route-load order can't change
// the answer, and only on the client (SSR safe).
const _initialLocalAdsOptOut = (() => {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(ADS_OPT_OUT_KEY) === '1';
  } catch {
    return false;
  }
})();

/**
 * The local opt-out value as it was at first JS bundle load, before
 * any server-side hydration ran. Stable for the lifetime of the page —
 * useful for the one-time cross-device announcement which must
 * remember the user's pre-sync local choice even after we've mirrored
 * the server value into localStorage.
 */
export function getInitialLocalAdsOptOut() {
  return _initialLocalAdsOptOut;
}

/**
 * Mirror a server-side `ads_opt_out` value into localStorage without
 * dispatching the change event (this is a rehydrate, not a user action).
 * Used after `/user/profile` loads so signed-in users see their cross-
 * device preference applied on the next page load. Pass `undefined` /
 * `null` (server didn't return the field) to no-op.
 */
export function hydrateAdsOptOutFromServer(serverValue) {
  if (typeof window === 'undefined') return;
  if (serverValue === undefined || serverValue === null) return;
  try {
    if (serverValue) {
      window.localStorage.setItem(ADS_OPT_OUT_KEY, '1');
    } else {
      window.localStorage.removeItem(ADS_OPT_OUT_KEY);
    }
  } catch {
    /* ignore storage failures */
  }
}

// ── Paid-plan ad-free gate (Task #552) ──────────────────────────────────────
// Paying subscribers (Starter / Pro) get an ad-free reading experience as
// a perk of upgrading. `AuthContext` is the single source of truth for the
// signed-in user's plan; it mirrors the plan into this module via
// `setAdsUserPlan()` whenever the user state changes (login, signup,
// /auth/me hydrate, profile refresh, logout). `adsConsentGranted()` then
// reads the mirrored value with zero extra network calls.
//
// The set of "paid" plan keys mirrors the rest of the codebase
// (Free / Starter / Pro — see ProfilePage / PricingSection / AdminPlans).
const PAID_PLAN_KEYS = new Set(['starter', 'pro']);

let _userPlan = null;
// Until AuthContext finishes its first /auth/me probe we don't yet
// know if the visitor is a paying subscriber. Fail closed so a paid
// user with a cookie-only session never sees an ad flash before their
// plan hydrates. AuthContext flips this to `true` via
// `setAdsAuthChecked(true)` as soon as `authChecked` is true.
let _authChecked = false;

export function setAdsAuthChecked(checked) {
  const next = !!checked;
  if (next === _authChecked) return;
  _authChecked = next;
  if (typeof window !== 'undefined') {
    try {
      window.dispatchEvent(
        new CustomEvent('syrabit:ads-consent-changed', {
          detail: { reason: 'auth-checked', authChecked: _authChecked },
        })
      );
    } catch {
      /* ignore */
    }
  }
}

/**
 * Mirror the signed-in user's plan into the ads module so
 * `adsConsentGranted()` can suppress every ad surface for paying
 * subscribers without a server round-trip. Pass `null` / `undefined`
 * for anonymous visitors and on logout.
 */
export function setAdsUserPlan(plan) {
  const next = typeof plan === 'string' ? plan.toLowerCase() : null;
  if (next === _userPlan) return;
  _userPlan = next;
  // Notify already-mounted ad surfaces so they can re-evaluate
  // `adsConsentGranted()` and tear down any scripts they injected
  // while the user was anonymous (or hadn't hydrated yet).
  if (typeof window !== 'undefined') {
    try {
      window.dispatchEvent(
        new CustomEvent('syrabit:ads-consent-changed', {
          detail: { reason: 'plan', plan: _userPlan },
        })
      );
    } catch {
      /* ignore */
    }
  }
}

/**
 * True when the mirrored user plan is one of the paid tiers
 * (Starter / Pro). Exported for tests and for any future caller that
 * needs to branch on the same gate that suppresses ads.
 */
function isPaidPlanActive() {
  return !!(_userPlan && PAID_PLAN_KEYS.has(_userPlan));
}

// One-time banner that explains the new cross-device sync behaviour to
// users who already had a local "opt out of ads" choice set before the
// account-synced version of the toggle shipped. Bump the version
// suffix if we ever want to re-prompt every user (e.g. policy change).
const ADS_BANNER_SEEN_KEY = 'syrabit:ads-cross-device-banner-seen-v1';

export function hasSeenAdsCrossDeviceBanner() {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(ADS_BANNER_SEEN_KEY) === '1';
  } catch {
    return true;
  }
}

export function markAdsCrossDeviceBannerSeen() {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(ADS_BANNER_SEEN_KEY, '1');
  } catch {
    /* ignore storage failures */
  }
}

/**
 * Resolve the config for a placement key. Always returns an object with at
 * least `{ enabled, height }`. `enabled` is false when:
 *   - the placement key is unknown,
 *   - the network has no `scriptUrl`,
 *   - or the placement has no `slotId`.
 *
 * `<AdSlot />` is responsible for the consent + production-build gates.
 */
// Networks that have been hard-disabled at the config layer regardless of
// whether their env vars are set. Any placement on a disabled network
// returns `{ enabled: false }` from `getAdConfig()` so `<AdSlot />`
// renders nothing — no script tag, no reserved height, no layout shift.
// To re-enable, remove the network name from this set.
//   - propellerads: disabled 2026-04-19. (NSFW push notifications.)
//   - adsterra:     disabled 2026-04-19. (Reputation for adult /
//                   popunder creatives slipping past category filters.)
//   - quge5:        disabled 2026-04-19. (Same — popunders + adult.)
//   - adpushup:     disabled 2026-04-19 per user request "keep only
//                   adsense". Premium SSP, brand-safe, but user wants
//                   single-network simplicity.
//
// Net result: only Google AdSense serves ads on the site. Auto Ads
// runs page-level on /learn + /pyq via `useAdsenseAutoAds`, plus the
// `*.adsense.*` per-slot placements stay available for ad-ops to
// fill specific positions if the per-slot env vars are populated.
const DISABLED_NETWORKS = new Set([
  'propellerads',
  'adsterra',
  'quge5',
  'adpushup',
]);

export function getAdConfig(placement) {
  const p = PLACEMENTS[placement];
  if (!p) return { enabled: false, height: 0 };
  if (DISABLED_NETWORKS.has(p.network)) return { enabled: false, height: 0 };
  const net = NETWORKS[p.network];
  const enabled = !!(net && net.scriptUrl && p.slotId);
  return {
    enabled,
    network: p.network,
    scriptUrl: net?.scriptUrl || '',
    publisherId: net?.publisherId || '',
    crossorigin: net?.crossorigin || '',
    slotId: p.slotId,
    height: p.height,
    label: p.label,
    adFormat: p.adFormat || 'auto',
    adLayout: p.adLayout || null,
  };
}

/**
 * Returns true when the visitor's consent state allows third-party
 * advertising. Syrabit.ai does not yet ship a consent-management
 * platform, so we default to "load only in production builds" per the
 * task spec. When a CMP is added, hook it in here — `<AdSlot />` is the
 * single caller.
 */
export function adsConsentGranted() {
  if (typeof window === 'undefined') return false;
  if (getAdsOptOut()) return false;
  // Fail closed until auth has been checked, so a returning paid
  // subscriber whose plan is still hydrating from `/auth/me` never
  // sees an ad flash on /learn or /pyq — Task #552.
  if (!_authChecked) return false;
  // Paid subscribers (Starter / Pro) get an ad-free experience — Task #552.
  if (isPaidPlanActive()) return false;
  return !!(env && env.PROD);
}
