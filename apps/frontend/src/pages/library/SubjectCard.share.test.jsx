import { describe, expect, it } from 'vitest';

import { getSubjectLandingPath } from './SubjectCard';

describe('SubjectCard share destination', () => {
  it('uses the canonical subject page represented by normalized card data', () => {
    expect(getSubjectLandingPath({
      id: 'physics-id',
      boardSlug: 'ahsec',
      classSlug: 'hs-1st-year',
      slug: 'physics',
    })).toBe('/ahsec/hs-1st-year/physics');
  });

  it('uses the same canonical page for raw API snake_case data', () => {
    expect(getSubjectLandingPath({
      id: 'physics-id',
      board_slug: 'ahsec',
      class_slug: 'hs-1st-year',
      subject_slug: 'physics',
    })).toBe('/ahsec/hs-1st-year/physics');
  });
});