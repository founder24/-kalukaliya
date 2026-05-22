import { describe, it, expect } from 'vitest';
import {
  captionLine,
  joinCaptionParts,
  formatAlertStateCaption,
} from './cronCaptionHelpers';

// Task #842 — pre-existing helpers (`captionLine`, `joinCaptionParts`)
// were previously only tested through their consumers' rendered HTML.
// Lock down their primitive contract here so future refactors that
// add a fourth cron pill don't accidentally regress the empty-state
// fallbacks, and so the Task #902 alerter-state caption is also
// covered in isolation.

describe('captionLine', () => {
  it('renders "<prefix> <ageLabel> ago" when ageLabel is set', () => {
    expect(captionLine('Last heartbeat', '2h', 'No heartbeat'))
      .toBe('Last heartbeat 2h ago');
  });

  it('returns the fallback verbatim when ageLabel is empty/null', () => {
    expect(captionLine('Last heartbeat', null, 'No heartbeat'))
      .toBe('No heartbeat');
    expect(captionLine('Last heartbeat', '', 'No heartbeat'))
      .toBe('No heartbeat');
  });
});

describe('joinCaptionParts', () => {
  it('joins non-empty parts with " · "', () => {
    expect(joinCaptionParts(['a', 'b', 'c'])).toBe('a · b · c');
  });

  it('drops nullish/empty parts so an absent suffix never trails', () => {
    // Specifically guards against the trustpilot wrapper's "recorded · "
    // bug from before the helper was extracted.
    expect(joinCaptionParts(['recorded', null, ''])).toBe('recorded');
    expect(joinCaptionParts(['', null, undefined])).toBe('');
  });

  it('treats nullish input as empty', () => {
    expect(joinCaptionParts(null)).toBe('');
    expect(joinCaptionParts(undefined)).toBe('');
  });
});

// Task #902 — alerter-state caption. Pins the "show / don't show"
// rule against the lock-doc shape returned by
// /admin/health/<pill>/cron/alert-state so we can't accidentally
// render a misleading "last paged" line on a fresh deploy.
describe('formatAlertStateCaption', () => {
  it('returns null when no alertState is provided', () => {
    expect(formatAlertStateCaption(null)).toBeNull();
    expect(formatAlertStateCaption(undefined)).toBeNull();
  });

  it('returns null when the lock doc is absent (present=false)', () => {
    // Brand-new deployment, alerter has never fired — we must NOT
    // render a "last paged: never" line because the dashboard
    // already carries the pill colour for that state.
    expect(formatAlertStateCaption({
      present: false,
      lastAlertAt: null,
      lastAlertAgeSeconds: null,
      inDebounce: false,
      debounceRemainingSeconds: null,
    })).toBeNull();
  });

  it('returns null when present=true but no lastAlertAt was recorded', () => {
    // Defensive: shouldn't happen in practice (the alerter writes
    // both fields atomically) but the helper short-circuits anyway
    // so we don't render "last paged: just now" against a doc that
    // never actually paged.
    expect(formatAlertStateCaption({
      present: true,
      lastAlertAt: null,
      lastAlertAgeSeconds: null,
      inDebounce: false,
    })).toBeNull();
  });

  it('renders just the "last paged Xh ago" line outside the debounce window', () => {
    // Past the 24h debounce: still informative ("we did page on
    // this") but no "in debounce" suffix because the next poll
    // can re-page if the pill stays red.
    expect(formatAlertStateCaption({
      present: true,
      lastAlertAt: '2026-04-24T00:00:00+00:00',
      lastAlertAgeSeconds: 30 * 3600,
      inDebounce: false,
      debounceRemainingSeconds: null,
    })).toBe('last paged 1d ago');
  });

  it('appends "in debounce ~Yh remaining" while the realert window is active', () => {
    expect(formatAlertStateCaption({
      present: true,
      lastAlertAt: '2026-04-25T00:00:00+00:00',
      lastAlertAgeSeconds: 2 * 3600,
      inDebounce: true,
      debounceRemainingSeconds: 22 * 3600,
    })).toBe('last paged 2h ago · in debounce ~22h remaining');
  });

  it('formats sub-minute ages as seconds', () => {
    // Catches a mid-poll race where the alerter just fired and the
    // dashboard's poll lapped it before the 60s tick.
    expect(formatAlertStateCaption({
      present: true,
      lastAlertAt: '2026-04-25T00:00:00+00:00',
      lastAlertAgeSeconds: 5,
      inDebounce: true,
      debounceRemainingSeconds: 24 * 3600 - 5,
    })).toBe('last paged 5s ago · in debounce ~23h remaining');
  });

  it('falls back to "just now" when lastAlertAgeSeconds is null but lastAlertAt is set', () => {
    // The backend currently always sends the age, but be defensive
    // — a future refactor could send only the ISO string and the
    // helper should still render something sensible.
    expect(formatAlertStateCaption({
      present: true,
      lastAlertAt: '2026-04-25T00:00:00+00:00',
      lastAlertAgeSeconds: null,
      inDebounce: false,
    })).toBe('last paged: just now');
  });

  it('omits the debounce suffix when inDebounce=true but remaining is null', () => {
    // Shape-defensive: don't render "in debounce ~null remaining".
    expect(formatAlertStateCaption({
      present: true,
      lastAlertAt: '2026-04-25T00:00:00+00:00',
      lastAlertAgeSeconds: 60,
      inDebounce: true,
      debounceRemainingSeconds: null,
    })).toBe('last paged 1m ago');
  });
});
