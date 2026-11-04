import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { TRAINING_ONLY_USER_AGENTS } from '../src/tollbit/policy';

const root = new URL('../../frontend/public/', import.meta.url);

describe('Tollbit discovery deployment contract', () => {
  it('keeps robots and AI manifest training rules aligned with enforcement', () => {
    const robots = readFileSync(new URL('robots.txt', root), 'utf8').toLowerCase();
    const manifest = readFileSync(new URL('ai.txt', root), 'utf8');
    const denied = manifest.split('[disallow]')[1].split('[contact]')[0].toLowerCase().split('\n');
    for (const ua of TRAINING_ONLY_USER_AGENTS) {
      expect(robots).toContain(`user-agent: ${ua.toLowerCase()}\ndisallow: /`);
      expect(denied).toContain(ua.toLowerCase());
    }
  });

  it('ships ai.txt directly as a static discovery file rather than bot-render HTML', () => {
    const routes = JSON.parse(readFileSync(new URL('_routes.json', root), 'utf8'));
    const manifest = readFileSync(new URL('ai.txt', root), 'utf8');
    const headers = readFileSync(new URL('_headers', root), 'utf8');
    expect(routes.exclude).toContain('/ai.txt');
    expect(headers).toContain('/ai.txt\n  Content-Type: text/plain; charset=utf-8');
    expect(manifest).toContain('https://tollbit.syrabit.ai');
    expect(manifest).toContain('[disallow]');
    expect(manifest).not.toContain('<html');
  });
});
