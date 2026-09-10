import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STAFF_PORTAL_SECTIONS,
  assertStaffSectionCoverage,
} from '../apps/frontend/src/config/staffPortalSections.mjs';

test('staff release coverage rejects section-list drift', () => {
  const missingLastSection = STAFF_PORTAL_SECTIONS.slice(0, -1);

  assert.throws(
    () => assertStaffSectionCoverage(STAFF_PORTAL_SECTIONS, missingLastSection),
    /Staff portal section coverage drifted/,
  );
});

test('staff release coverage accepts the shared section list', () => {
  assert.doesNotThrow(
    () => assertStaffSectionCoverage(STAFF_PORTAL_SECTIONS, STAFF_PORTAL_SECTIONS),
  );
});