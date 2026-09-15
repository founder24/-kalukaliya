/**
 * adsConfig.js — single source of truth for the ad stack on Syrabit.ai.
 *
 * Each placement key is wired to exactly one network. Real publisher IDs
 * and script URLs are read from `import.meta.env.VITE_ADS_*` env vars.
 * If any required value for a placement is missing, `getAdConfig()`
 * returns `{ enabled: false }` and `<AdSlot />` renders nothing — no
 * layout shift, no script tag injected.
 *
 * Sponsored surfaces:
 *   - /chat       (between completed assistant turns only)
 *   - /library    (LibraryPage)
 *   - /browser    (LibraryPage alias)
 *
 * Sponsored content routes (manual per-slot units only, no auto-ads):
 *   - /learn/:slug   (LearnPage — Notes standalone view)
 *   - /pyq/:slug     (PYQReplicaPage — Question Paper standalone view)
 *   - /:board/...    (ChapterPage — Notes, Q&A, and Question Paper tabs)
 *
 * Adding/removing a network or placement is a one-file change here.
 * See ADS.md for the full list of env vars per network.
 */

import {
  ADSENSE_PLACEMENT_ENV_KEYS,
  ADSENSE_SLOT_ID_PATTERN,
} from './adsenseSlotConfig';

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
  // the shared ad script registry keeps it loaded once.
  adsense: {
    scriptUrl: 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-8958003374183515',
    publisherId: 'ca-pub-8958003374183515',
    crossorigin: 'anonymous',
  },
};

// ── Per-placement wiring ─────────────────────────────────────────────────────
// Sponsored surfaces include chat, Learn, PYQ, and chapter content.
//
// ALL placements are wired to Google AdSense (the only active network).
// adpushup, adsterra, and propellerads are hard-disabled in DISABLED_NETWORKS,
// so those three networks are kept for future reference only.
//
// Placement key taxonomy mirrors the JSX callsites in LearnPage / PYQReplicaPage:
//   learn.topOfContent / learn.inContent / learn.afterPyqs /
//   learn.afterFlashcards / learn.endOfContent / learn.sidebar
//   pyq.topOfContent  / pyq.inContent  / pyq.endOfContent
//
// Display slots (top, end, sidebar):
//   adFormat="auto" + data-full-width-responsive → Google picks IAB size
//   (320×50 banner or 300×250 rectangle on mobile). Reserved minHeight
//   prevents CLS during the first-paint window.
//
// In-article fluid slots (inContent, afterPyqs, afterFlashcards):
//   adFormat="fluid" + adLayout="in-article" → Google controls height
//   entirely. No minHeight reserved — the slot collapses to nothing when
//   Google decides not to fill it, which is the correct mobile behaviour.
const PLACEMENTS = {
  'chat.afterAssistant': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chat.afterAssistant']] || '',
    height: 120,
    label: 'Sponsored learning content',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  // ── PYQ pages ─────────────────────────────────────────────────────────────
  'pyq.topOfContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['pyq.topOfContent']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  'pyq.inContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['pyq.inContent']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  // DOM-injected between paper image pages. Activated by useEffect in
  // PYQReplicaPage after every 2nd <img> inside the server HTML body.
  // Max 3 injections per paper — students spend 15-30 min here, highest RPM.
  'pyq.betweenImages': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['pyq.betweenImages']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'pyq.endOfContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['pyq.endOfContent']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },

  // ── Notes / Learn pages ────────────────────────────────────────────────────
  'learn.topOfContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['learn.topOfContent']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  'learn.inContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['learn.inContent']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'learn.afterPyqs': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['learn.afterPyqs']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'learn.afterFlashcards': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['learn.afterFlashcards']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'learn.endOfContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['learn.endOfContent']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  // Desktop-only sidebar skyscraper — hidden on mobile via `hidden lg:flex`
  // in LearnPage so mobile/tablet viewports never reserve the 600px column.
  'learn.sidebar': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['learn.sidebar']] || '',
    height: 600,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  // Injected after every 3rd question in the Important Questions / Q&A section.
  // Blueprint "reward zone" — appears after a full Q+A pair as a natural break.
  'learn.afterQuestion': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['learn.afterQuestion']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },

  // ── ChapterPage tabs ───────────────────────────────────────────────────────
  // Notes tab — three slots: top display, in-article fluid, end display.
  'chapter.notes.top': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.notes.top']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  'chapter.notes.inContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.notes.inContent']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'chapter.notes.end': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.notes.end']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  // Q&A tab — in-article fluid inserted after every 3rd topic card, plus end display.
  'chapter.qa.inContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.qa.inContent']] || '',
    height: 0,
    label: 'Advertisement',
    adFormat: 'fluid',
    adLayout: 'in-article',
  },
  'chapter.qa.end': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.qa.end']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  // Desktop-only sidebar skyscraper — hidden on mobile via `hidden lg:flex`
  // on the aside in ChapterPage. Mirrors the learn.sidebar placement so
  // desktop ad density matches LearnPage.
  'chapter.sidebar': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.sidebar']] || '',
    height: 600,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  // Question Paper tab — top display and post-viewer fluid.
  'chapter.pyq.top': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.pyq.top']] || '',
    height: 250,
    label: 'Advertisement',
    adFormat: 'auto',
  },
  'chapter.pyq.inContent': {
    network: 'adsense',
    slotId: env[ADSENSE_PLACEMENT_ENV_KEYS['chapter.pyq.inContent']] || '',
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
    // hooks (`useAdsenseAutoAds`) can re-evaluate
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

// Wait for the auth probe before loading third-party scripts. This avoids
// consent/config decisions changing during the first client render.
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
const AD_FREE_PLANS = new Set(['starter', 'pro', 'premium']);
let _adFreePlan = false;

export function setAdsPlan(plan) {
  const normalizedPlan = typeof plan === 'string' ? plan.trim().toLowerCase() : '';
  const next = AD_FREE_PLANS.has(normalizedPlan);
  if (next === _adFreePlan) return;

  _adFreePlan = next;
  if (typeof window !== 'undefined') {
    try {
      window.dispatchEvent(
        new CustomEvent('syrabit:ads-consent-changed', {
          detail: { reason: 'plan', adFree: _adFreePlan },
        })
      );
    } catch {
      /* ignore */
    }
  }
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
//                   adsense". Brand-safe, with one network for simplicity.
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
  const slotId = typeof p.slotId === 'string' ? p.slotId.trim() : '';
  const validSlotId = p.network !== 'adsense' || ADSENSE_SLOT_ID_PATTERN.test(slotId);
  const enabled = !!(net && net.scriptUrl && validSlotId && slotId);
  return {
    enabled,
    network: p.network,
    scriptUrl: net?.scriptUrl || '',
    publisherId: net?.publisherId || '',
    crossorigin: net?.crossorigin || '',
    slotId,
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
  if (_adFreePlan) return false;
  if (getAdsOptOut()) return false;
  // Fail closed until the initial auth probe has settled.
  if (!_authChecked) return false;
  return !!(env && env.PROD);
}
