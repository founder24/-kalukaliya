import { describe, expect, it } from 'vitest';

import {
  STAFF_PORTAL_SECTIONS,
  assertStaffSectionMappings,
} from '@/config/staffPortalSections.mjs';
import {
  SECTION_COMPONENTS,
  SECTION_ICONS,
} from './AdminPage';

describe('AdminPage staff section wiring', () => {
  it('maps every shared section to exactly one icon and component', () => {
    expect(() => assertStaffSectionMappings(
      STAFF_PORTAL_SECTIONS,
      SECTION_ICONS,
      SECTION_COMPONENTS,
      ['roadmap'],
    )).not.toThrow();
  });

  it('rejects a newly cataloged section until its production wiring is complete', () => {
    const incompleteSections = [
      ...STAFF_PORTAL_SECTIONS,
      { id: 'unwired', label: 'Unwired', group: 'system' },
    ];

    expect(() => assertStaffSectionMappings(
      incompleteSections,
      SECTION_ICONS,
      SECTION_COMPONENTS,
      ['roadmap'],
    )).toThrow(/icon mapping is incomplete \(missing: unwired\)/);
  });
});