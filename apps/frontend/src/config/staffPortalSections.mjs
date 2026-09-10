export const STAFF_PORTAL_SECTIONS = Object.freeze([
  { id: 'dashboard',     label: 'Dashboard',         group: 'main' },
  { id: 'contenthub',    label: 'Content Editor',    group: 'main' },
  { id: 'seomanager',    label: 'SEO Manager',       group: 'main' },
  { id: 'users',         label: 'Users',             group: 'audience' },
  { id: 'conversations', label: 'Conversations',     group: 'audience' },
  { id: 'notifications', label: 'Notifications',     group: 'audience' },
  { id: 'ai',            label: 'AI & Automation',   group: 'operations' },
  { id: 'analytics',     label: 'Analytics',         group: 'operations' },
  { id: 'security',      label: 'Access & Security', group: 'system' },
  { id: 'logs',          label: 'Logs',              group: 'system' },
  { id: 'health',        label: 'Health / Uptime',   group: 'system' },
  { id: 'ops',           label: 'Ops Console',       group: 'system' },
  { id: 'settings',      label: 'Site Settings',     group: 'system' },
]);

export function assertStaffSectionCoverage(expectedSections, coveredSections) {
  const serialize = sections => sections.map(({ id, label }) => `${id}:${label}`);
  const expected = serialize(expectedSections);
  const covered = serialize(coveredSections);

  if (
    expected.length !== covered.length
    || expected.some((section, index) => section !== covered[index])
  ) {
    throw new Error(
      `Staff portal section coverage drifted.\nExpected: ${expected.join(', ')}\nCovered: ${covered.join(', ')}`,
    );
  }
}

function assertExactSectionMapping(mappingName, expectedIds, mapping) {
  const actualIds = Object.keys(mapping);
  const missing = expectedIds.filter(id => !actualIds.includes(id));
  const extra = actualIds.filter(id => !expectedIds.includes(id));

  if (missing.length || extra.length) {
    const details = [
      missing.length ? `missing: ${missing.join(', ')}` : null,
      extra.length ? `extra: ${extra.join(', ')}` : null,
    ].filter(Boolean).join('; ');

    throw new Error(`Staff portal ${mappingName} mapping is incomplete (${details}).`);
  }
}

export function assertStaffSectionMappings(
  sections,
  iconMappings,
  componentMappings,
  additionalComponentIds = [],
) {
  const sectionIds = sections.map(({ id }) => id);
  assertExactSectionMapping('icon', sectionIds, iconMappings);
  assertExactSectionMapping(
    'component',
    [...sectionIds, ...additionalComponentIds],
    componentMappings,
  );
}
