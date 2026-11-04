import { describe, expect, it, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  injectMonetagZones,
  isMonetagManagedPath,
  MONETAG_ZONES,
  MONETAG_REPLACED_AD_PLACEMENTS,
  shouldForceMonetagDocumentNavigation,
} from './monetagAds';

describe('Monetag route and tag configuration', () => {
  it('includes exact Monetag runtime hosts in CSP without wildcarding them', () => {
    const headers = readFileSync(resolve(process.cwd(), 'public/_headers'), 'utf8');
    const csp = headers.match(/Content-Security-Policy:\s*([^\n]+)/)?.[1] ?? '';
    const connectSrc = csp.match(/(?:^|;\s*)connect-src\s+([^;]+)/)?.[1] ?? '';

    expect(connectSrc.split(/\s+/)).toEqual(expect.arrayContaining([
      'https://my.rtmark.net',
      'https://255md.com',
      'https://jhnwr.com',
    ]));
    expect(connectSrc).not.toContain('https://*.rtmark.net');
    expect(connectSrc).not.toContain('https://*.255md.com');
    expect(connectSrc).not.toContain('https://*.jhnwr.com');
  });

  it.each([
    '/learn/modern-assamese-language',
    '/pyq/ahsec-class-12-physics-2025',
    '/ahsec/class-12/physics',
    '/ahsec/class-12/physics/laws-of-motion',
    '/ahsec/class-12/science/physics/laws-of-motion',
    '/ahsec/class-12/physics/laws-of-motion/topic/inertia',
    '/as/ahsec/class-12/physics/laws-of-motion',
    '/as/ahsec/class-12/science/physics/laws-of-motion/topic/inertia',
  ])('marks learning content for Monetag isolation: %s', (path) => {
    expect(isMonetagManagedPath(path)).toBe(true);
  });

  it.each([
    '/library',
    '/browser',
    '/chat',
    '/profile',
    '/staff',
    '/admin/content',
    '/not-a-course/path',
  ])('leaves unrelated route outside Monetag scope: %s', (path) => {
    expect(isMonetagManagedPath(path)).toBe(false);
  });

  it('forces a document navigation only when leaving the Monetag route group', () => {
    expect(shouldForceMonetagDocumentNavigation(
      '/ahsec/class-12/physics/laws-of-motion',
      '/chat',
    )).toBe(true);
    expect(shouldForceMonetagDocumentNavigation(
      '/learn/modern-assamese-language',
      '/library',
    )).toBe(true);
    expect(shouldForceMonetagDocumentNavigation(
      '/learn/one',
      '/pyq/two',
    )).toBe(false);
    expect(shouldForceMonetagDocumentNavigation('/chat', '/library')).toBe(false);
  });

  it('injects each exact zone only once', () => {
    const doc = document.implementation.createHTMLDocument('Monetag test');

    injectMonetagZones({ doc });
    injectMonetagZones({ doc });

    expect([...doc.scripts].map((script) => ({
      zone: script.dataset.zone,
      src: script.src,
      rocketLoaderOptOut: script.getAttribute('data-cfasync'),
    }))).toEqual([
      {
        zone: MONETAG_ZONES.onclick.zoneId,
        src: MONETAG_ZONES.onclick.scriptUrl,
        rocketLoaderOptOut: 'false',
      },
      {
        zone: MONETAG_ZONES.inPagePush.zoneId,
        src: MONETAG_ZONES.inPagePush.scriptUrl,
        rocketLoaderOptOut: 'false',
      },
    ]);
  });

  it('supports loading one zone without duplicating the other', () => {
    const doc = document.implementation.createHTMLDocument('Monetag test');

    injectMonetagZones({ onclick: false, doc });

    expect([...doc.scripts].map((script) => script.dataset.zone))
      .toEqual([MONETAG_ZONES.inPagePush.zoneId]);
  });

  it('replaces only learning-page AdSense placements', () => {
    expect(MONETAG_REPLACED_AD_PLACEMENTS.has('chapter.notes.top')).toBe(true);
    expect(MONETAG_REPLACED_AD_PLACEMENTS.has('learn.afterQuestion')).toBe(true);
    expect(MONETAG_REPLACED_AD_PLACEMENTS.has('pyq.betweenImages')).toBe(true);
    expect(MONETAG_REPLACED_AD_PLACEMENTS.has('chat.afterAssistant')).toBe(false);
  });
});
