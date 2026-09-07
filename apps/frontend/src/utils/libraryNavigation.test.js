import { describe, expect, it } from 'vitest';
import { isChapterRoute, isLibraryNavigationActive } from './libraryNavigation';

describe('library navigation route matching', () => {
  it.each([
    '/ahsec/hs-2nd-year/economics/forms-of-market',
    '/ahsec/hs-2nd-year/arts/economics/forms-of-market',
    '/as/ahsec/hs-2nd-year/economics/forms-of-market',
    '/as/ahsec/hs-2nd-year/arts/economics/forms-of-market/topic/demand',
    '/ahsec/hs-2nd-year/economics/forms-of-market/topic/demand',
    '/ahsec/hs-2nd-year/economics/forms-of-market/notes',
  ])('recognizes chapter route %s', (pathname) => {
    expect(isChapterRoute(pathname)).toBe(true);
    expect(isLibraryNavigationActive(pathname)).toBe(true);
  });

  it.each(['/library', '/library/ahsec', '/home', '/ahsec/hs-2nd-year/economics'])(
    'does not mistake %s for a chapter while retaining library descendants',
    (pathname) => {
      expect(isLibraryNavigationActive(pathname)).toBe(
        pathname === '/library' || pathname.startsWith('/library/'),
      );
    },
  );
});