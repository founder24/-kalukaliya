import { describe, it, expect } from 'vitest';
import { pushChannelTone, PUSH_CHANNEL_STALE_MS } from './pushChannelTone';

const NOW = Date.UTC(2026, 3, 17, 12, 0, 0); // 2026-04-17T12:00:00Z
const isoMinus = (ms) => new Date(NOW - ms).toISOString();

describe('pushChannelTone', () => {
  it('returns idle when there are no attempts at all', () => {
    const r = pushChannelTone({ now: NOW });
    expect(r).toEqual({
      tone: 'idle',
      degraded: false,
      isStale: false,
      successAgeMs: null,
    });
  });

  it('returns idle when last_success_at is null and no attempts', () => {
    const r = pushChannelTone({
      last_success_at: null,
      last_error: null,
      last_attempt_at: null,
      now: NOW,
    });
    expect(r.tone).toBe('idle');
    expect(r.degraded).toBe(false);
  });

  it('returns healthy on a recent success with no error', () => {
    const r = pushChannelTone({
      last_success_at: isoMinus(60_000), // 1 min ago
      last_error: null,
      last_attempt_at: isoMinus(60_000),
      now: NOW,
    });
    expect(r.tone).toBe('healthy');
    expect(r.degraded).toBe(false);
    expect(r.isStale).toBe(false);
  });

  it('returns degraded when last_error is present (even with recent success)', () => {
    const r = pushChannelTone({
      last_success_at: isoMinus(60_000),
      last_error: 'FCM 502 bad gateway',
      last_attempt_at: isoMinus(30_000),
      now: NOW,
    });
    expect(r.tone).toBe('degraded');
    expect(r.degraded).toBe(true);
  });

  it('returns degraded when last_attempt exists but last success >24h old', () => {
    const r = pushChannelTone({
      last_success_at: isoMinus(PUSH_CHANNEL_STALE_MS + 60_000), // 24h + 1min
      last_error: null,
      last_attempt_at: isoMinus(60_000),
      now: NOW,
    });
    expect(r.tone).toBe('degraded');
    expect(r.degraded).toBe(true);
    expect(r.isStale).toBe(true);
  });

  it('returns degraded when last_attempt exists but no success ever', () => {
    const r = pushChannelTone({
      last_success_at: null,
      last_error: null,
      last_attempt_at: isoMinus(5_000),
      now: NOW,
    });
    expect(r.tone).toBe('degraded');
    expect(r.degraded).toBe(true);
    expect(r.isStale).toBe(true);
  });

  it('treats a success exactly at the 24h boundary as still healthy', () => {
    const r = pushChannelTone({
      last_success_at: isoMinus(PUSH_CHANNEL_STALE_MS), // exactly 24h
      last_error: null,
      last_attempt_at: isoMinus(PUSH_CHANNEL_STALE_MS),
      now: NOW,
    });
    expect(r.isStale).toBe(false);
    expect(r.tone).toBe('healthy');
  });

  it('falls back to Date.now() when no `now` is supplied', () => {
    const r = pushChannelTone({
      last_success_at: new Date().toISOString(),
      last_error: null,
      last_attempt_at: new Date().toISOString(),
    });
    expect(r.tone).toBe('healthy');
  });

  it('handles a malformed last_success_at gracefully (treated as missing)', () => {
    const r = pushChannelTone({
      last_success_at: 'not-a-date',
      last_error: null,
      last_attempt_at: isoMinus(1000),
      now: NOW,
    });
    // NaN time -> successAge null -> stale because attempted but no usable success
    expect(r.degraded).toBe(true);
    expect(r.tone).toBe('degraded');
  });
});
