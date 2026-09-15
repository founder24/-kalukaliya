/** @vitest-environment node */

import { describe, expect, it } from 'vitest';
import { renderRoute } from '../entry-server.jsx';
import {
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
});