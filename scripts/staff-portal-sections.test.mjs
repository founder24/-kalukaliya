import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STAFF_PORTAL_SECTIONS,
  assertStaffSectionCoverage,
  assertStaffSectionMappings,
  assertStaffSectionReleaseChecks,
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

test('staff release checks reject an unclassified section', () => {
  const sections = [
    ...STAFF_PORTAL_SECTIONS,
    { id: 'new-section', label: 'New Section', group: 'system' },
  ];

  assert.throws(
    () => assertStaffSectionReleaseChecks(sections),
    /section "new-section" must declare releaseCheck\.supported and releaseCheck\.requiredReads/,
  );
});

test('staff release checks reject API reads on an unsupported section', () => {
  const sections = [{
    id: 'unsupported',
    label: 'Unsupported',
    group: 'system',
    releaseCheck: { supported: false, requiredReads: ['/api/v1/staff/example'] },
  }];

  assert.throws(
    () => assertStaffSectionReleaseChecks(sections),
    /section "unsupported" is unsupported but declares required API reads/,
  );
});

test('staff release checks accept every shared section declaration', () => {
  assert.doesNotThrow(() => assertStaffSectionReleaseChecks(STAFF_PORTAL_SECTIONS));
});

const completeMappings = Object.fromEntries(
  STAFF_PORTAL_SECTIONS.map(({ id }) => [id, Symbol(id)]),
);

test('staff section wiring rejects missing mappings with the affected section id', () => {
  const { settings: _settings, ...missingSettings } = completeMappings;

  assert.throws(
    () => assertStaffSectionMappings(
      STAFF_PORTAL_SECTIONS,
      missingSettings,
      completeMappings,
    ),
    /icon mapping is incomplete \(missing: settings\)/,
  );
});

test('staff section wiring rejects extra mappings with the affected section id', () => {
  assert.throws(
    () => assertStaffSectionMappings(
      STAFF_PORTAL_SECTIONS,
      completeMappings,
      { ...completeMappings, orphaned: Symbol('orphaned') },
    ),
    /component mapping is incomplete \(extra: orphaned\)/,
  );
});

test('staff section wiring accepts complete icon and component mappings', () => {
  assert.doesNotThrow(
    () => assertStaffSectionMappings(
      STAFF_PORTAL_SECTIONS,
      completeMappings,
      completeMappings,
    ),
  );
});
