export const CONTENT_HUB_TABS = Object.freeze([
  {
    id: 'editor',
    label: 'Content Editor',
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
  {
    id: 'cms',
    label: 'CMS / Docs',
    releaseCheck: { supported: false, requiredReads: [] },
  },
  {
    id: 'blog',
    label: 'Blog Publisher',
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
  {
    id: 'translation',
    label: 'Assamese',
    releaseCheck: {
      supported: true,
      requiredReads: [
        '/api/v1/admin/content/assamese/coverage',
        '/api/v1/admin/content/assamese/progress',
      ],
    },
  },
  {
    id: 'progress',
    label: 'Translation Progress',
    releaseCheck: {
      supported: true,
      requiredReads: ['/api/v1/admin/content/assamese/coverage'],
    },
  },
  {
    id: 'seeder',
    label: 'Seeder History',
    releaseCheck: {
      supported: true,
      requiredReads: ['/api/v1/admin/content/seed-notes/history'],
    },
  },
  {
    id: 'rag-mirror',
    label: 'RAG Mirror',
    releaseCheck: {
      supported: true,
      requiredReads: ['/api/v1/admin/content/rag/reindex/status'],
    },
  },
]);

export function assertContentHubTabReleaseChecks(tabs) {
  for (const tab of tabs) {
    const releaseCheck = tab.releaseCheck;
    if (
      !releaseCheck
      || typeof releaseCheck.supported !== 'boolean'
      || !Array.isArray(releaseCheck.requiredReads)
    ) {
      throw new Error(
        `Content Editor tab "${tab.id}" must declare releaseCheck.supported and releaseCheck.requiredReads.`,
      );
    }
    if (!releaseCheck.supported && releaseCheck.requiredReads.length) {
      throw new Error(
        `Content Editor tab "${tab.id}" is unsupported but declares required API reads.`,
      );
    }
    if (releaseCheck.supported && !releaseCheck.requiredReads.length) {
      throw new Error(
        `Content Editor tab "${tab.id}" is supported but declares no required API reads.`,
      );
    }
  }
}

export function assertContentHubTabReadEvidence(tab, startedReads, successfulReads) {
  for (const path of tab.releaseCheck.requiredReads) {
    if (!startedReads.includes(path)) {
      throw new Error(`${tab.label} did not initiate required Worker read ${path}`);
    }
    if (!successfulReads.includes(path)) {
      throw new Error(`${tab.label} did not complete required Worker read ${path}`);
    }
  }
}