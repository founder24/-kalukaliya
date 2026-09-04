import { beforeEach, describe, expect, it, vi } from 'vitest';

const cronWorkers = vi.hoisted(() => ({
  resumeSeedRuns: vi.fn(async () => undefined),
  resumePublishJobs: vi.fn(async () => undefined),
}));

vi.mock('./routes/admin-content', async importOriginal => ({
  ...(await importOriginal<typeof import('./routes/admin-content')>()),
  ...cronWorkers,
}));

import { handleScheduled } from './index';
import type { Env } from './types';

type Write = { query: string; bindings: unknown[] };

function cronEnv(writes: Write[]): Env {
  const database = {
    prepare(query: string) {
      let bindings: unknown[] = [];
      const statement = {
        bind(...values: unknown[]) {
          bindings = values;
          return statement;
        },
        async run() {
          writes.push({ query, bindings });
          return { meta: { changes: 1 } };
        },
      };
      return statement;
    },
  };
  return { DB: database } as unknown as Env;
}

describe('scheduled D1 operational persistence', () => {
  beforeEach(() => {
    cronWorkers.resumeSeedRuns.mockReset().mockResolvedValue(undefined);
    cronWorkers.resumePublishJobs.mockReset().mockResolvedValue(undefined);
  });

  it('records a successful invocation and clears the durable failure state', async () => {
    const writes: Write[] = [];
    await handleScheduled(
      { cron: '*/5 * * * *', scheduledTime: 1_735_689_600_000 } as ScheduledController,
      cronEnv(writes),
    );

    const start = writes[0]!;
    expect(start.query).toContain('INSERT INTO cron_runs');
    expect(start.bindings.slice(1, 4)).toEqual(['*/5 * * * *', 1_735_689_600, expect.any(Number)]);
    const completion = writes.find(write => write.query.includes('UPDATE cron_runs'));
    expect(completion?.bindings.slice(1, 4)).toEqual(['succeeded', 0, null]);
    const state = writes.find(write => write.query.includes('INSERT INTO cron_alert_state'));
    expect(state?.bindings.slice(0, 2)).toEqual([0, 0]);
  });

  it('records task failures and activates durable cron alert state', async () => {
    cronWorkers.resumeSeedRuns.mockRejectedValueOnce(new Error('seed database unavailable'));
    const writes: Write[] = [];

    await handleScheduled(
      { cron: '*/5 * * * *', scheduledTime: 1_735_689_600_000 } as ScheduledController,
      cronEnv(writes),
    );

    const completion = writes.find(write => write.query.includes('UPDATE cron_runs'));
    expect(completion?.bindings[1]).toBe('failed');
    expect(completion?.bindings[2]).toBe(1);
    expect(completion?.bindings[3]).toContain('seed resume: seed database unavailable');
    const state = writes.find(write => write.query.includes('INSERT INTO cron_alert_state'));
    expect(state?.bindings.slice(0, 2)).toEqual([1, 1]);
  });
});