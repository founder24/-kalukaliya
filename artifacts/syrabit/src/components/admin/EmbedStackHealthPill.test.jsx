import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';

// Task #469 — pin the per-leg "N/threshold consecutive failures" badge
// rendering on EmbedStackHealthPill so the Task #436 colour ladder
// (clean=emerald, warm-up=amber, firing=red) cannot silently drift.

vi.mock('axios', () => {
  const fn = vi.fn();
  const interceptors = {
    request:  { use: vi.fn(), eject: vi.fn() },
    response: { use: vi.fn(), eject: vi.fn() },
  };
  const inst = { get: fn, post: vi.fn(), interceptors };
  return { default: inst, ...inst };
});

import axios from 'axios';
import { render, cleanup, waitFor } from '@testing-library/react';
import EmbedStackHealthPill from './EmbedStackHealthPill';

const baseHealthy = (overrides = {}) => ({
  ok: true,
  embed:  { ok: true, consecutive_failures: 0, firing: false,
            alert_threshold: 3 },
  rerank: { ok: true, consecutive_failures: 0, firing: false,
            alert_threshold: 3 },
  memory: { ok: true, consecutive_failures: 0, firing: false,
            alert_threshold: 3 },
  embed_environments: [],
  alert_state: { threshold: 3, legs: {
    embed:        { consecutive_failures: 0, firing: false },
    rerank:       { consecutive_failures: 0, firing: false },
    memory_brain: { consecutive_failures: 0, firing: false },
  }},
  ...overrides,
});

async function renderWith(payload) {
  axios.get.mockResolvedValueOnce({ data: payload });
  const utils = render(<EmbedStackHealthPill adminToken="t" />);
  await waitFor(() => {
    expect(utils.queryByTestId('embed-stack-leg-embed')).not.toBeNull();
  });
  return utils;
}

describe('EmbedStackHealthPill — per-leg counter badge (Task #469)', () => {
  afterEach(() => {
    cleanup();
    axios.get.mockReset();
  });

  it('renders the badge in emerald (clean) when consecutive_failures=0', async () => {
    const { getByTestId } = await renderWith(baseHealthy());
    const badge = getByTestId('embed-stack-leg-embed-failures');
    expect(badge.textContent).toMatch(/0\s*\/\s*3 consecutive failures/);
    expect(badge.className).toContain('bg-emerald-100');
    expect(badge.className).toContain('text-emerald-700');
    expect(badge.className).not.toContain('bg-amber-100');
    expect(badge.className).not.toContain('bg-rose-100');
  });

  it('renders the badge in AMBER during the warm-up window (1..threshold-1)', async () => {
    for (const failures of [1, 2]) {
      cleanup();
      axios.get.mockReset();
      const payload = baseHealthy({
        embed: { ok: false, consecutive_failures: failures, firing: false,
                 alert_threshold: 3, reason: 'workers_embed: HTTP 503' },
      });
      const { getByTestId } = await renderWith(payload);
      const badge = getByTestId('embed-stack-leg-embed-failures');
      expect(badge.textContent).toMatch(
        new RegExp(`${failures}\\s*\\/\\s*3 consecutive failures`),
      );
      expect(badge.className).toContain('bg-amber-100');
      expect(badge.className).toContain('text-amber-700');
      expect(badge.className).not.toContain('bg-rose-100');
      expect(badge.className).not.toContain('bg-emerald-100');
    }
  });

  it('renders the badge in RED when firing=true (watchdog has paged)', async () => {
    const payload = baseHealthy({
      ok: false,
      rerank: { ok: false, consecutive_failures: 3, firing: true,
                alert_threshold: 3, reason: 'pinecone /rerank 5xx' },
    });
    const { getByTestId } = await renderWith(payload);
    const badge = getByTestId('embed-stack-leg-rerank-failures');
    expect(badge.textContent).toMatch(/3\s*\/\s*3 consecutive failures/);
    expect(badge.className).toContain('bg-rose-100');
    expect(badge.className).toContain('text-rose-700');
    expect(badge.className).not.toContain('bg-amber-100');
    expect(badge.className).not.toContain('bg-emerald-100');
  });

  it('uses the per-leg alert_threshold for the badge denominator', async () => {
    const payload = baseHealthy({
      memory: { ok: false, consecutive_failures: 2, firing: false,
                alert_threshold: 5 },
    });
    const { getByTestId } = await renderWith(payload);
    const badge = getByTestId('embed-stack-leg-memory-failures');
    expect(badge.textContent).toMatch(/2\s*\/\s*5 consecutive failures/);
  });
});
