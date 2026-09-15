/** @vitest-environment node */

import { describe, expect, it } from 'vitest';
import { renderRoute } from '../entry-server.jsx';
import { injectShell } from '../../scripts/prerender-routes.mjs';
import { injectPrerenderPath } from '../../scripts/_prerender-marker.mjs';
import {
  ASSAMESE_PAGE_ONE_URL,
  ASSAMESE_PAGE_TWO_URL,
  assameseChapter,
  fixtureChapter,
  PAGE_ONE_URL,
  PAGE_TWO_URL,
  PUBLIC_ROUTE,
} from './fixtures/public-image-chapter';

describe('public chapter server rendering', () => {
  it('includes both saved Markdown image URLs in the SSR response', async () => {
    const result = await renderRoute({
      url: PUBLIC_ROUTE,
      seed: {
        chapterPreload: {
          board: 'ahsec',
          classSlug: 'class-12',
          subjectSlug: 'physics',
          chapterSlug: 'public-image-fixture',
          data: fixtureChapter,
        },
      },
    });

    expect(result.errors).toEqual([]);
    expect(result.html).toContain(`src="${PAGE_ONE_URL}"`);
    expect(result.html).toContain(`src="${PAGE_TWO_URL}"`);
  });

  it('includes both saved localized Markdown image URLs in the Assamese SSR response', async () => {
    const result = await renderRoute({
      url: '/as/ahsec/hs-1st-year/physics/goti',
      seed: {
        chapterPreload: {
          board: 'ahsec',
          classSlug: 'hs-1st-year',
          subjectSlug: 'physics',
          chapterSlug: 'goti',
          data: assameseChapter,
        },
      },
    });

    expect(result.errors).toEqual([]);
    expect(result.html).toContain(`src="${ASSAMESE_PAGE_ONE_URL}"`);
    expect(result.html).toContain(`src="${ASSAMESE_PAGE_TWO_URL}"`);
  });

  it('keeps saved image markup and the chapter preload after route HTML injection', async () => {
    const result = await renderRoute({
      url: PUBLIC_ROUTE,
      seed: {
        chapterPreload: {
          board: 'ahsec',
          classSlug: 'class-12',
          subjectSlug: 'physics',
          chapterSlug: 'public-image-fixture',
          data: fixtureChapter,
        },
      },
    });

    const preload = {
      board: 'ahsec',
      classSlug: 'class-12',
      subjectSlug: 'physics',
      chapterSlug: 'public-image-fixture',
      data: fixtureChapter,
    };
    const shell = [
      '<!doctype html>',
      '<html><head></head><body>',
      '<noscript><style>#__shell{display:none!important}</style></noscript>',
      '<div id="root"></div>',
      '<script type="module" src="/assets/index.js"></script>',
      '</body></html>',
    ].join('');

    const routeDocument = injectPrerenderPath(
      injectShell(shell, {
        ssrHtml: result.html,
        hydrateKind: 'chapter',
        inlineScripts: [
          `<script>window.__CHAPTER_PRELOAD__=${JSON.stringify(preload).replace(/</g, '\\u003c')};</script>`,
        ],
      }),
      PUBLIC_ROUTE,
    );

    expect(routeDocument).toContain('<div id="root" data-hydrate="chapter">');
    expect(routeDocument).toContain(`src="${PAGE_ONE_URL}"`);
    expect(routeDocument).toContain(`src="${PAGE_TWO_URL}"`);
    expect(routeDocument).toContain(
      `<meta name="syrabit-prerender-path" content="${PUBLIC_ROUTE}" />`,
    );

    const preloadMatch = routeDocument.match(
      /<script>window\.__CHAPTER_PRELOAD__=(.*);<\/script>/,
    );
    expect(preloadMatch).not.toBeNull();
    expect(JSON.parse(preloadMatch?.[1] ?? '')).toEqual(preload);
  });
});
