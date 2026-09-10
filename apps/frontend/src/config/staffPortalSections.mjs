export const STAFF_PORTAL_SECTIONS = Object.freeze([
  {
    id: 'dashboard',
    label: 'Dashboard',
    group: 'main',
    releaseCheck: {
      supported: true,
      requiredReads: ['/health', '/api/v1/staff/analytics/command-center'],
    },
  },
  {
    id: 'contenthub',
    label: 'Content Editor',
    group: 'main',
    releaseCheck: {
      supported: true,
      requiredReads: [
        '/api/v1/staff/content/boards',
        '/api/v1/staff/content/classes',
        '/api/v1/staff/content/streams',
        '/api/v1/staff/content/subjects',
      ],
    },
  },
  { id: 'seomanager', label: 'SEO Manager', group: 'main', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'users', label: 'Users', group: 'audience', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'conversations', label: 'Conversations', group: 'audience', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'notifications', label: 'Notifications', group: 'audience', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'ai', label: 'AI & Automation', group: 'operations', releaseCheck: { supported: false, requiredReads: [] } },
  {
    id: 'analytics',
    label: 'Analytics',
    group: 'operations',
    releaseCheck: {
      supported: true,
      requiredReads: ['/api/v1/staff/analytics/command-center'],
    },
  },
  { id: 'security', label: 'Access & Security', group: 'system', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'logs', label: 'Logs', group: 'system', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'health', label: 'Health / Uptime', group: 'system', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'ops', label: 'Ops Console', group: 'system', releaseCheck: { supported: false, requiredReads: [] } },
  { id: 'settings', label: 'Site Settings', group: 'system', releaseCheck: { supported: false, requiredReads: [] } },
]);

export function assertStaffSectionReleaseChecks(sections) {
  for (const section of sections) {
    const behavior = section.releaseCheck;
    if (
      !behavior
      || typeof behavior.supported !== 'boolean'
      || !Array.isArray(behavior.requiredReads)
    ) {
      throw new Error(
        `Staff portal section "${section.id}" must declare releaseCheck.supported and releaseCheck.requiredReads.`,
      );
    }
    if (!behavior.supported && behavior.requiredReads.length) {
      throw new Error(
        `Staff portal section "${section.id}" is unsupported but declares required API reads.`,
      );
    }
  }
}

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
