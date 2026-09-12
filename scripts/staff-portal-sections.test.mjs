import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STAFF_PORTAL_SECTIONS,
  assertStaffSectionCoverage,
  assertStaffSectionMappings,
  assertStaffSectionReleaseChecks,
  isStaffReleaseReadPath,
} from '../apps/frontend/src/config/staffPortalSections.mjs';
import {
  CONTENT_HUB_TABS,
  assertContentHubTabReadEvidence,
  assertContentHubTabReleaseChecks,
} from '../apps/frontend/src/config/contentHubTabs.mjs';

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

test('staff release evidence excludes universal public API warm-ups', () => {
  assert.equal(isStaffReleaseReadPath('/health'), true);
  assert.equal(isStaffReleaseReadPath('/api/v1/staff/analytics/command-center'), true);
  assert.equal(isStaffReleaseReadPath('/api/v1/admin/content/assamese/coverage'), true);
  assert.equal(isStaffReleaseReadPath('/api/content/library-bundle'), false);
  assert.equal(isStaffReleaseReadPath('/api/analytics/events'), false);
  assert.equal(isStaffReleaseReadPath('/assets/app.js'), false);
});

test('Content Editor release checks reject an unclassified tab', () => {
  const tabs = [
    ...CONTENT_HUB_TABS,
    { id: 'new-tab', label: 'New Tab' },
  ];

  assert.throws(
    () => assertContentHubTabReleaseChecks(tabs),
    /tab "new-tab" must declare releaseCheck\.supported and releaseCheck\.requiredReads/,
  );
});

test('Content Editor release checks reject API reads on an unsupported tab', () => {
  const tabs = [{
    id: 'unsupported',
    label: 'Unsupported',
    releaseCheck: { supported: false, requiredReads: ['/api/v1/staff/example'] },
  }];

  assert.throws(
    () => assertContentHubTabReleaseChecks(tabs),
    /tab "unsupported" is unsupported but declares required API reads/,
  );
});

test('Content Editor release checks reject a supported tab without proof reads', () => {
  const tabs = [{
    id: 'unproven',
    label: 'Unproven',
    releaseCheck: { supported: true, requiredReads: [] },
  }];

  assert.throws(
    () => assertContentHubTabReleaseChecks(tabs),
    /tab "unproven" is supported but declares no required API reads/,
  );
});

test('Content Editor release checks accept every shared tab declaration', () => {
  assert.doesNotThrow(() => assertContentHubTabReleaseChecks(CONTENT_HUB_TABS));
});

test('Content Editor tab proof rejects identical reads completed by an earlier tab', () => {
  const blogTab = CONTENT_HUB_TABS.find(({ id }) => id === 'blog');
  const earlierTabReads = [...blogTab.releaseCheck.requiredReads];

  assert.doesNotThrow(
    () => assertContentHubTabReadEvidence(blogTab, earlierTabReads, earlierTabReads),
  );
  assert.throws(
    () => assertContentHubTabReadEvidence(blogTab, [], []),
    /Blog Publisher did not initiate required Worker read/,
  );
});

test('staff release checks wait for reads in the section that initiates them', () => {
  const releaseChecks = Object.fromEntries(
    STAFF_PORTAL_SECTIONS.map(({ id, releaseCheck }) => [id, releaseCheck.requiredReads]),
  );

  assert.deepEqual(releaseChecks.dashboard, ['/health']);
  assert.deepEqual(
    releaseChecks.analytics,
    ['/api/v1/staff/analytics/command-center'],
  );
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

test('staff section wiring rejects duplicate catalog entries with the affected section id', () => {
  const duplicatedSections = [
    ...STAFF_PORTAL_SECTIONS,
    STAFF_PORTAL_SECTIONS.find(({ id }) => id === 'analytics'),
  ];

  assert.throws(
    () => assertStaffSectionMappings(
      duplicatedSections,
      completeMappings,
      completeMappings,
    ),
    /section catalog contains duplicate section IDs: analytics/,
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
