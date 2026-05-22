/**
 * SEO Phase D — JSON-LD schema validator (no test runner needed).
 *
 * Asserts that each `pageType` builder emits a well-formed @graph containing
 * the schema.org node types Google's Rich Results validator expects. Run via:
 *
 *     node artifacts/syrabit/scripts/validate-jsonld.mjs
 *
 * Exits 0 on success, 1 on the first failure (so CI / `pnpm verify` can gate
 * on it without a separate test framework).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  chapterSchema,
  subjectHubSchema,
  libraryLandingSchema,
  homeSchema,
  pyqSchema,
  howToSchema,
  globalSiteSchema,
  extractHowToSteps,
  learnArticleSchema,
  pyqDatasetSchema,
  howToFromContent,
  detectHowToFromDoc,
  dedupeGraphTypes,
  buildSchemaForPageType,
  ORG_SAMEAS,
  FOUNDER,
} from '../src/lib/jsonld.js';

const types = (g) => g['@graph'].map((n) => n['@type']);

test('chapterSchema → Article + LearningResource + WebPage + Breadcrumb (+ FAQ)', () => {
  const data = {
    title: 'Newton’s Third Law — Physics Notes',
    meta_description: 'Notes on Newton’s third law of motion.',
    topic_title: 'Newton’s Third Law',
    chapter_title: 'Laws of Motion',
    subject_name: 'Physics',
    board_name: 'AHSEC',
    class_name: 'Class 12',
    word_count: 1234,
    generated_at: '2026-04-01T00:00:00Z',
    faq_entries: [
      { question: 'What is Newton’s third law?', answer: 'Every action has an equal and opposite reaction.' },
      { question: 'Give an example.', answer: 'Rocket propulsion.' },
    ],
  };
  const url = 'https://syrabit.ai/ahsec/class-12/physics/newtons-third-law';
  const g = chapterSchema(data, url, '/ahsec/class-12/physics');
  assert.equal(g['@context'], 'https://schema.org');
  const t = types(g);
  for (const expected of ['Article', 'LearningResource', 'WebPage', 'BreadcrumbList', 'FAQPage']) {
    assert.ok(t.includes(expected), `chapter graph missing ${expected} (got: ${t.join(', ')})`);
  }
  const bc = g['@graph'].find((n) => n['@type'] === 'BreadcrumbList');
  assert.equal(bc.itemListElement.length, 4);
  assert.equal(bc.itemListElement[3].item, url);
});

test('chapterSchema returns null for missing inputs', () => {
  assert.equal(chapterSchema(null, 'x'), null);
  assert.equal(chapterSchema({ title: 'x' }, ''), null);
});

test('subjectHubSchema → EducationalOrganization + CollectionPage + Course + Breadcrumb', () => {
  const subject = {
    name: 'Physics', slug: 'physics', board_slug: 'ahsec', class_slug: 'class-12',
    board_name: 'AHSEC', class_name: 'Class 12', stream_name: 'Science',
    description: 'Physics notes for AHSEC Class 12 students.',
    chapters: [
      { title: 'Laws of Motion', slug: 'laws-of-motion', description: 'Newton’s laws of motion explained.' },
      { title: 'Work, Energy & Power', slug: 'work-energy-power', description: 'Mechanical energy concepts.' },
    ],
  };
  const url = 'https://syrabit.ai/ahsec/class-12/physics';
  const g = subjectHubSchema(subject, url);
  const t = types(g);
  for (const expected of ['EducationalOrganization', 'CollectionPage', 'Course', 'BreadcrumbList']) {
    assert.ok(t.includes(expected), `subject graph missing ${expected} (got: ${t.join(', ')})`);
  }
  // FAQPage emitted when ≥2 chapters carry descriptions
  assert.ok(t.includes('FAQPage'), 'expected FAQPage when chapters have descriptions');
  const collection = g['@graph'].find((n) => n['@type'] === 'CollectionPage');
  assert.equal(collection.hasPart.length, 2);
  assert.equal(
    collection.hasPart[0].url,
    'https://syrabit.ai/ahsec/class-12/physics/laws-of-motion',
  );
});

test('libraryLandingSchema → Course + ItemList + WebPage + Breadcrumb', () => {
  const subjects = [
    { id: 's1', name: 'Physics', slug: 'physics', boardSlug: 'ahsec', classSlug: 'class-12', boardName: 'AHSEC', className: 'Class 12' },
    { id: 's2', name: 'Chemistry', slug: 'chemistry', boardSlug: 'ahsec', classSlug: 'class-12', boardName: 'AHSEC', className: 'Class 12' },
  ];
  const url = 'https://syrabit.ai/library';
  const g = libraryLandingSchema(subjects, url);
  const t = types(g);
  for (const expected of ['Course', 'ItemList', 'WebPage', 'BreadcrumbList']) {
    assert.ok(t.includes(expected), `library graph missing ${expected} (got: ${t.join(', ')})`);
  }
  const list = g['@graph'].find((n) => n['@type'] === 'ItemList');
  assert.equal(list.numberOfItems, 2);
  assert.equal(
    list.itemListElement[0].item.url,
    'https://syrabit.ai/ahsec/class-12/physics',
  );
});

test('homeSchema → Organization + LocalBusiness (Guwahati) + WebSite + Breadcrumb with SearchAction', () => {
  const g = homeSchema('https://syrabit.ai/');
  const t = types(g);
  for (const expected of ['Organization', 'LocalBusiness', 'WebSite', 'BreadcrumbList']) {
    assert.ok(t.includes(expected), `home graph missing ${expected} (got: ${t.join(', ')})`);
  }
  const site = g['@graph'].find((n) => n['@type'] === 'WebSite');
  assert.equal(site.potentialAction['@type'], 'SearchAction');
  const lb = g['@graph'].find((n) => n['@type'] === 'LocalBusiness');
  assert.equal(lb.address.addressLocality, 'Guwahati');
  assert.equal(lb.address.addressRegion, 'Assam');
  assert.equal(lb.address.addressCountry, 'IN');
  assert.equal(lb.geo['@type'], 'GeoCoordinates');
  assert.ok(typeof lb.geo.latitude === 'number' && typeof lb.geo.longitude === 'number');
});

test('howToFromContent → emits HowTo with ordered HowToStep entries', () => {
  const node = howToFromContent({
    title: 'How to balance a chemical equation',
    content: [
      '1. Write the unbalanced equation with reactants and products.',
      '2. Count the atoms of each element on both sides.',
      '3. Adjust coefficients to equalise the atom counts.',
      '4. Verify charge and total mass are conserved.',
    ].join('\n'),
    inLanguage: 'en-IN',
  });
  assert.ok(node, 'expected HowTo node');
  assert.equal(node['@type'], 'HowTo');
  assert.equal(node.inLanguage, 'en-IN');
  assert.equal(node.step.length, 4);
  assert.equal(node.step[0]['@type'], 'HowToStep');
  assert.equal(node.step[0].position, 1);
});

test('howToFromContent returns null when no steps detected', () => {
  assert.equal(howToFromContent({ title: 'Plain article', content: 'Just a paragraph of prose.' }), null);
  assert.equal(howToFromContent({}), null);
});

test('detectHowToFromDoc gates on title / type / content heuristics', () => {
  assert.ok(detectHowToFromDoc({
    title: 'How to derive Newton\u2019s second law',
    content: '1. Start from F. 2. Apply Newton.',
  }));
  assert.equal(detectHowToFromDoc({ title: 'Newton biography', content: 'No steps here.' }), null);
});

test('learnArticleSchema → Article + LearningResource + Breadcrumb (+ HowTo when applicable)', () => {
  const doc = {
    seo_slug: 'how-to-balance-equation',
    title: 'How to balance a chemical equation',
    meta_description: 'Step-by-step guide to balancing equations.',
    type: 'tutorial',
    content_html: '<ol><li>Write equation</li><li>Count atoms</li></ol>',
    content: '1. Write the unbalanced equation. 2. Count atoms on both sides. 3. Balance coefficients.',
    primary_keyword: 'balance chemical equation',
    seo_tags: 'chemistry, tutorial',
    updated_at: '2026-04-10T12:00:00Z',
    created_at: '2026-03-01T08:00:00Z',
    language: 'en',
  };
  const url = 'https://syrabit.ai/learn/how-to-balance-equation';
  const g = learnArticleSchema(doc, url);
  const t = types(g);
  for (const expected of ['Article', 'LearningResource', 'BreadcrumbList', 'HowTo']) {
    assert.ok(t.includes(expected), `learn graph missing ${expected} (got: ${t.join(', ')})`);
  }
  const article = g['@graph'].find((n) => n['@type'] === 'Article');
  assert.equal(article.dateModified, '2026-04-10T12:00:00.000Z');
  assert.equal(article.datePublished, '2026-03-01T08:00:00.000Z');
  assert.equal(article.inLanguage, 'en-IN');
});

test('pyqDatasetSchema → Dataset + Quiz + Breadcrumb with license + inLanguage', () => {
  const meta = {
    slug: 'ahsec-class-12-physics-2024',
    title: 'AHSEC Class 12 Physics 2024 Question Paper',
    description: 'AHSEC Class 12 Physics previous year question paper, 2024.',
    board: 'AHSEC',
    subject: 'Physics',
    year: '2024',
    educationalLevel: 'Class 12',
    inLanguage: 'en-IN',
  };
  const url = 'https://syrabit.ai/pyq/ahsec-class-12-physics-2024';
  const g = pyqDatasetSchema(meta, url);
  const t = types(g);
  for (const expected of ['Dataset', 'Quiz', 'BreadcrumbList']) {
    assert.ok(t.includes(expected), `pyq graph missing ${expected} (got: ${t.join(', ')})`);
  }
  const ds = g['@graph'].find((n) => n['@type'] === 'Dataset');
  assert.equal(ds.inLanguage, 'en-IN');
  assert.ok(ds.license.startsWith('https://'), 'expected license URL');
  assert.equal(ds.educationalLevel, 'Class 12');
  assert.equal(ds.about.name, 'Physics');
  assert.equal(ds.identifier, 'ahsec-class-12-physics-2024');
});

test('dedupeGraphTypes drops FAQPage when caller already supplies it (Breadcrumb kept)', () => {
  const subject = {
    name: 'Physics', slug: 'physics', board_slug: 'ahsec', class_slug: 'class-12',
    board_name: 'AHSEC', class_name: 'Class 12', stream_name: 'Science',
    description: 'Physics notes for AHSEC Class 12 students.',
    chapters: [
      { title: 'Laws of Motion', slug: 'laws-of-motion', description: 'Newton\u2019s laws of motion explained in detail.' },
      { title: 'Work, Energy & Power', slug: 'work-energy-power', description: 'Mechanical energy concepts explained in detail.' },
    ],
  };
  const url = 'https://syrabit.ai/ahsec/class-12/physics';
  const typed = subjectHubSchema(subject, url);
  const external = { '@type': 'FAQPage', mainEntity: [] };
  const deduped = dedupeGraphTypes(typed, [external]);
  const t = deduped['@graph'].map((n) => n['@type']);
  assert.ok(!t.includes('FAQPage'), 'FAQPage should have been removed');
  assert.ok(t.includes('BreadcrumbList'), 'BreadcrumbList should always remain');
});

test('buildSchemaForPageType dispatches all known page types', () => {
  assert.equal(buildSchemaForPageType('unknown', {}), null);
  assert.ok(buildSchemaForPageType('home', { url: 'https://syrabit.ai/' }));
  assert.ok(buildSchemaForPageType('library', {
    url: 'https://syrabit.ai/library',
    subjects: [{ id: 's1', name: 'X', slug: 'x', boardSlug: 'b', classSlug: 'c' }],
  }));
  assert.ok(buildSchemaForPageType('learn', {
    url: 'https://syrabit.ai/learn/x',
    doc: { seo_slug: 'x', title: 'X', updated_at: '2026-01-01' },
  }));
  // dispatcher accepts both {meta} (canonical) and {doc} (legacy) for `pyq`.
  assert.ok(buildSchemaForPageType('pyq', {
    url: 'https://syrabit.ai/pyq/x',
    meta: { slug: 'x', subject: 'Physics' },
  }));
  assert.ok(buildSchemaForPageType('pyq', {
    url: 'https://syrabit.ai/pyq/ahsec-2024-physics',
    doc: { slug: 'ahsec-2024-physics', board: 'AHSEC', year: '2024', subject: 'Physics' },
  }));
});

// ── Task #336 additions ────────────────────────────────────────────────

test('globalSiteSchema → Organization + LocalBusiness with Guwahati address', () => {
  const g = globalSiteSchema('https://syrabit.ai/');
  const t = types(g).map(x => Array.isArray(x) ? x[0] : x);
  for (const expected of ['Organization', 'LocalBusiness', 'WebPage']) {
    assert.ok(t.includes(expected), `global graph missing ${expected} (got: ${JSON.stringify(t)})`);
  }
  const lb = g['@graph'].find((n) => n['@type'] === 'LocalBusiness');
  assert.equal(lb.address.addressLocality, 'Guwahati');
  assert.equal(lb.address.addressRegion, 'Assam');
  assert.equal(lb.geo['@type'], 'GeoCoordinates');
  // Stable @id keeps the org node deduplicable across the page.
  const org = g['@graph'].find((n) => Array.isArray(n['@type']) && n['@type'].includes('Organization'));
  assert.equal(org['@id'], 'https://syrabit.ai/#organization');
  assert.deepEqual(org.knowsLanguage, ['en', 'as']);

  // Task #940 / closes #558 — Org.sameAs must include every verified
  // social profile so AI crawlers map off-site mentions back to the
  // canonical Syrabit.ai entity. SITE_ORIGIN stays first; the rest
  // are exactly the entries the backend probes weekly.
  assert.ok(Array.isArray(org.sameAs) && org.sameAs.length === 1 + ORG_SAMEAS.length,
    `Org.sameAs should be SITE_ORIGIN + every verified profile (got ${JSON.stringify(org.sameAs)})`);
  for (const url of ORG_SAMEAS) {
    assert.ok(org.sameAs.includes(url), `Org.sameAs missing ${url}`);
  }

  // Task #940 / closes #558 — founder Person node + worksFor link.
  assert.equal(org.founder?.['@id'], 'https://syrabit.ai/#founder',
    'Org.founder should reference the founder Person node by @id');
  const founder = g['@graph'].find((n) => n['@type'] === 'Person'
    && n['@id'] === 'https://syrabit.ai/#founder');
  assert.ok(founder, 'globalSiteSchema should emit a founder Person node');
  assert.equal(founder.name, FOUNDER.name);
  assert.equal(founder.jobTitle, FOUNDER.jobTitle);
  assert.deepEqual(founder.sameAs, FOUNDER.sameAs);
  assert.equal(founder.worksFor['@id'], 'https://syrabit.ai/#organization');
});

test('extractHowToSteps detects numbered steps and returns >=2 step nodes', () => {
  const md = `
Some intro text.

1. Open the chapter notes
2. Scan the syllabus block for keywords
3. Practice the MCQs at the end
4. Review past papers from the library

Some closing prose.
  `;
  const steps = extractHowToSteps(md);
  assert.equal(steps.length, 4);
  assert.equal(steps[0]['@type'], 'HowToStep');
  assert.equal(steps[0].position, 1);
  assert.match(steps[0].text, /Open the chapter/);
});

test('extractHowToSteps returns [] for prose with a single numbered line', () => {
  assert.equal(extractHowToSteps('Note 1. only one item here').length, 0);
  assert.equal(extractHowToSteps('').length, 0);
  assert.equal(extractHowToSteps(null).length, 0);
});

test('howToSchema builds a HowTo node from string steps', () => {
  const g = howToSchema({
    name: 'Solve a quadratic equation',
    steps: ['Identify a, b, c', 'Apply the discriminant formula', 'Compute the two roots'],
    totalTime: 'PT5M',
    url: 'https://syrabit.ai/learn/quadratics',
  });
  assert.equal(g['@graph'][0]['@type'], 'HowTo');
  assert.equal(g['@graph'][0].step.length, 3);
  assert.equal(g['@graph'][0].totalTime, 'PT5M');
});

test('howToSchema returns null when fewer than 2 steps', () => {
  assert.equal(howToSchema({ name: 'x', steps: ['only one'] }), null);
  assert.equal(howToSchema({ name: 'x', steps: [] }), null);
});

test('chapterSchema appends HowTo when content has numbered steps', () => {
  const data = {
    title: 'Plant Lab Procedure',
    meta_description: 'Step-by-step plant tissue lab.',
    topic_title: 'Plant Lab', chapter_title: 'Lab Manual',
    subject_name: 'Biology', board_name: 'AHSEC', class_name: 'Class 12',
    word_count: 500,
    content: '1. Sterilize the tools\n2. Cut the leaf into 1cm sections\n3. Place on agar plate\n4. Incubate at 25C',
  };
  const g = chapterSchema(data, 'https://syrabit.ai/ahsec/class-12/biology/plant-lab', '/ahsec/class-12/biology');
  const t = types(g);
  assert.ok(t.includes('HowTo'), `expected HowTo in chapter graph (got: ${t.join(', ')})`);
  const howto = g['@graph'].find((n) => n['@type'] === 'HowTo');
  assert.equal(howto.step.length, 4);
});

test('chapterSchema omits dateModified when no real timestamps are provided', () => {
  // Critical: AI crawlers use dateModified for freshness — never bake
  // build-time `new Date()` values into Article schema.
  const g = chapterSchema(
    { title: 'X', meta_description: 'd', topic_title: 'X', subject_name: 'S', board_name: 'B', class_name: 'C' },
    'https://syrabit.ai/x',
    '/s',
  );
  const article = g['@graph'].find((n) => n['@type'] === 'Article');
  assert.equal(article.datePublished, undefined);
  assert.equal(article.dateModified, undefined);
});

test('chapterSchema preserves real timestamps from chapter metadata', () => {
  const g = chapterSchema(
    {
      title: 'X', meta_description: 'd', topic_title: 'X', subject_name: 'S',
      board_name: 'B', class_name: 'C',
      generated_at: '2026-01-02T03:04:05Z',
      updated_at: '2026-04-17T10:20:30Z',
    },
    'https://syrabit.ai/x',
    '/s',
  );
  const article = g['@graph'].find((n) => n['@type'] === 'Article');
  assert.equal(article.datePublished, '2026-01-02T03:04:05.000Z');
  assert.equal(article.dateModified, '2026-04-17T10:20:30.000Z');
});

test('pyqSchema → Quiz + LearningResource + Breadcrumb with educational alignment', () => {
  const g = pyqSchema(
    { slug: 'ahsec-2024-physics', board: 'AHSEC', year: '2024', subject: 'Physics', class: 'Class 12' },
    'https://syrabit.ai/pyq/ahsec-2024-physics',
  );
  const t = types(g);
  for (const expected of ['Quiz', 'LearningResource', 'BreadcrumbList']) {
    assert.ok(t.includes(expected), `pyq graph missing ${expected} (got: ${t.join(', ')})`);
  }
  const quiz = g['@graph'].find((n) => n['@type'] === 'Quiz');
  assert.equal(quiz.educationalAlignment.educationalFramework, 'AHSEC');
  assert.equal(quiz.educationalAlignment.targetName, 'Physics');
  assert.equal(quiz.inLanguage, 'en-IN');
});

test('pyqSchema falls back gracefully when only a slug is known', () => {
  const g = pyqSchema({ slug: 'mystery-paper' }, 'https://syrabit.ai/pyq/mystery-paper');
  assert.ok(g);
  const quiz = g['@graph'].find((n) => n['@type'] === 'Quiz');
  assert.match(quiz.name, /mystery-paper/);
});

// ── Task #338 — metadata-driven PYQ Dataset/Quiz ───────────────────────

test('pyqDatasetSchema emits totalQuestions, author, license, and dateModified when worker backfills meta', () => {
  // Mirrors the JSON shape returned by the new
  // /api/pyq/{slug}/meta worker endpoint so SERPs get richer
  // Dataset/Quiz cards (numberOfQuestions, dateCreated, license).
  const meta = {
    slug: 'ahsec-2024-physics',
    title: 'AHSEC Physics Major Question Paper 2024',
    description: 'AHSEC Physics 2024 (MAJOR) — full 70-mark paper.',
    subject: 'Physics',
    board: 'AHSEC',
    class_name: 'Class 12',
    year: 2024,
    paper_type: 'major',
    educationalLevel: 'Class 12',
    totalQuestions: 32,
    author: 'AHSEC',
    license: 'https://syrabit.ai/terms',
    published_at: '2025-03-12T08:00:00Z',
    updated_at: '2026-01-04T09:30:00Z',
    inLanguage: 'en-IN',
  };
  const g = pyqDatasetSchema(meta, 'https://syrabit.ai/pyq/ahsec-2024-physics');
  assert.ok(g, 'pyqDatasetSchema returned null with full meta');
  const t = g['@graph'].map((n) => n['@type']);
  for (const expected of ['Dataset', 'Quiz', 'BreadcrumbList']) {
    assert.ok(t.includes(expected), `expected ${expected} (got: ${t.join(', ')})`);
  }
  const ds = g['@graph'].find((n) => n['@type'] === 'Dataset');
  const quiz = g['@graph'].find((n) => n['@type'] === 'Quiz');
  assert.equal(ds.license, 'https://syrabit.ai/terms');
  assert.equal(ds.dateModified, '2026-01-04T09:30:00.000Z');
  assert.equal(ds.datePublished, '2025-03-12T08:00:00.000Z');
  assert.equal(ds.creator.name, 'AHSEC');
  assert.equal(ds.variableMeasured.name, 'Number of questions');
  assert.equal(ds.variableMeasured.value, 32);
  assert.equal(quiz.numberOfQuestions, 32);
  assert.equal(quiz.author.name, 'AHSEC');
  assert.equal(quiz.dateCreated, '2025-03-12T08:00:00.000Z');
  assert.equal(quiz.license, 'https://syrabit.ai/terms');
});

test('pyqDatasetSchema omits per-meta fields when worker hasn\u2019t backfilled them yet', () => {
  // Slug-only fallback path — mustn't fabricate a question count or
  // a dateModified out of thin air, since AI crawlers treat both as
  // freshness signals.
  const g = pyqDatasetSchema({ slug: 'mystery-paper' }, 'https://syrabit.ai/pyq/mystery-paper');
  const ds = g['@graph'].find((n) => n['@type'] === 'Dataset');
  const quiz = g['@graph'].find((n) => n['@type'] === 'Quiz');
  assert.equal(ds.variableMeasured, undefined);
  assert.equal(ds.dateModified, undefined);
  assert.equal(quiz.numberOfQuestions, undefined);
});
