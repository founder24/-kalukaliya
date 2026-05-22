/**
 * SEO Phase D / Task #336 — Per-page-type JSON-LD schema builders.
 *
 * Pure functions, no side effects. Each returns a schema.org @graph
 * array suitable for embedding in a single <script type="application/ld+json">
 * tag (or to be passed to PageMeta's `jsonLd` prop).
 *
 * Builders:
 *   chapterSchema(data, url, basePath)      — Article + LearningResource + WebPage + BreadcrumbList (+ FAQPage, HowTo)
 *   subjectHubSchema(subject, url)          — EducationalOrganization + CollectionPage + BreadcrumbList (+ Course, FAQPage)
 *   libraryLandingSchema(subjects, url)     — Course + ItemList + WebPage + BreadcrumbList
 *   homeSchema(url)                         — WebSite + BreadcrumbList (Org/LocalBusiness ship globally)
 *   globalSiteSchema(url)                   — Organization + LocalBusiness (Guwahati) for the global head
 *   learnArticleSchema(doc, url)            — Article + LearningResource + BreadcrumbList (+ HowTo when applicable)
 *   pyqSchema(doc, url)                     — Quiz + LearningResource + BreadcrumbList (legacy, slim)
 *   pyqDatasetSchema(meta, url)             — Dataset + Quiz + BreadcrumbList (canonical PYQ shape)
 *   howToSchema({ name, steps, ... })       — HowTo node (standalone or merged into a chapter graph)
 *   howToFromContent({ title, content, image, totalTime }) — HowTo if numbered steps detected
 *   extractHowToSteps(content)              — HowToStep[] from numbered/markdown content
 *
 * Helper: dedupeGraphTypes(typedGraph, externalGraphs) drops duplicate
 *   schema.org @types so the same type is never emitted twice.
 */

const SITE_ORIGIN = 'https://syrabit.ai';
const SITE_LOGO = `${SITE_ORIGIN}/icons/icon-192x192.png`;
const ORG_NODE = {
  '@type': 'Organization',
  name: 'Syrabit.ai',
  url: SITE_ORIGIN,
  logo: { '@type': 'ImageObject', url: SITE_LOGO },
};

const ORG_REF = { '@id': `${SITE_ORIGIN}/#organization` };

/**
 * Task #940 (closes #558) — verified off-site identities for the
 * Syrabit.ai entity + its founder. These are the same lists the
 * backend `entity_seo_health` worker probes weekly to detect drift,
 * so any future profile addition gets the JSON-LD `sameAs` for free
 * once it's added here.
 *
 * Only include profiles you actually control + want crawlers to map
 * back to the canonical Syrabit.ai entity — a stale or 404 entry
 * weakens the Knowledge Panel signal.
 */
export const ORG_SAMEAS = [
  'https://www.linkedin.com/company/syrabit-ai/',
  'https://twitter.com/SyrabitAI',
  'https://github.com/syrabit',
  'https://www.youtube.com/@syrabit',
];

export const FOUNDER = {
  name: 'Dipak Rai',
  jobTitle: 'Founder & CEO',
  sameAs: [
    'https://www.linkedin.com/in/dipakrai/',
    'https://github.com/dipakrai',
    'https://twitter.com/dipakraix',
  ],
};

const FOUNDER_NODE_ID = `${SITE_ORIGIN}/#founder`;

// Single source of truth for the publisher's address/geo. Reused by the
// chapter Article publisher node, the global LocalBusiness emission, and
// any future PostalAddress consumers so the AI crawlers see the exact
// same locality string everywhere ("Guwahati, Assam").
const SYRABIT_ADDRESS = {
  '@type': 'PostalAddress',
  addressLocality: 'Guwahati',
  addressRegion: 'Assam',
  addressCountry: 'IN',
};
const SYRABIT_GEO = {
  '@type': 'GeoCoordinates',
  latitude: 26.1445,
  longitude: 91.7362,
};

/**
 * Detect numbered / stepwise content in a markdown-ish body and return
 * a HowToStep[] array. Returns [] when no procedural pattern is found
 * so callers can short-circuit. Patterns recognized:
 *   • lines starting with "1.", "2.", … "n."
 *   • lines starting with "Step 1:", "Step 2 —", etc.
 * Steps are capped at 12 to keep the schema lean for SERP cards.
 */
export function extractHowToSteps(content) {
  if (!content || typeof content !== 'string') return [];
  const lines = content.split(/\r?\n/);
  const steps = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    const m = line.match(/^(?:\*\*)?(?:Step\s+)?(\d{1,2})[.):\-—]\s+(.{8,300})$/i);
    if (m) {
      const text = m[2].replace(/\*\*/g, '').trim();
      if (text) steps.push({ position: parseInt(m[1], 10), text });
      if (steps.length >= 12) break;
    }
  }
  // Require at least 2 distinct steps to avoid emitting HowTo for a
  // single bullet that happens to start with "1." in prose.
  if (steps.length < 2) return [];
  steps.sort((a, b) => a.position - b.position);
  return steps.map((s, i) => ({
    '@type': 'HowToStep',
    position: i + 1,
    name: s.text.split(/[.!?:]/, 1)[0].slice(0, 110),
    text: s.text,
  }));
}

/**
 * Site-wide JSON-LD: Organization + LocalBusiness (Guwahati). Mounted
 * once globally via <GlobalSeo />. Centralizes the publisher entity so
 * AI crawlers consistently identify Syrabit.ai across every page.
 */
export function globalSiteSchema(url) {
  const homeUrl = url || `${SITE_ORIGIN}/`;
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': ['Organization', 'EducationalOrganization'],
        '@id': `${SITE_ORIGIN}/#organization`,
        name: 'Syrabit.ai',
        alternateName: 'Syrabit',
        url: SITE_ORIGIN,
        logo: { '@type': 'ImageObject', url: SITE_LOGO, width: 192, height: 192 },
        // Task #940 / closes #558 — emit verified social profiles so
        // the Knowledge Graph + AI crawlers can map every off-site
        // mention back to the canonical Syrabit.ai entity. SITE_ORIGIN
        // stays first so existing test snapshots don't shift.
        sameAs: [SITE_ORIGIN, ...ORG_SAMEAS],
        address: SYRABIT_ADDRESS,
        areaServed: { '@type': 'AdministrativeArea', name: 'Assam, India' },
        knowsLanguage: ['en', 'as'],
        founder: { '@id': FOUNDER_NODE_ID },
      },
      {
        '@type': 'Person',
        '@id': FOUNDER_NODE_ID,
        name: FOUNDER.name,
        jobTitle: FOUNDER.jobTitle,
        worksFor: { '@id': `${SITE_ORIGIN}/#organization` },
        sameAs: FOUNDER.sameAs,
      },
      {
        '@type': 'LocalBusiness',
        '@id': `${SITE_ORIGIN}/#localbusiness`,
        name: 'Syrabit.ai',
        url: SITE_ORIGIN,
        image: SITE_LOGO,
        priceRange: '₹₹',
        address: SYRABIT_ADDRESS,
        geo: SYRABIT_GEO,
        areaServed: { '@type': 'AdministrativeArea', name: 'Assam, India' },
        parentOrganization: { '@id': `${SITE_ORIGIN}/#organization` },
      },
      {
        '@type': 'WebPage',
        '@id': homeUrl,
        url: homeUrl,
        isPartOf: { '@type': 'WebSite', '@id': SITE_ORIGIN, name: 'Syrabit.ai' },
        publisher: { '@id': `${SITE_ORIGIN}/#organization` },
      },
    ],
  };
}

/** Standalone HowTo builder. Pass `steps` (string[] | HowToStep[]). */
export function howToSchema({ name, description, steps, totalTime, inLanguage = 'en-IN', url }) {
  const stepNodes = (steps || []).map((s, i) => {
    if (s && typeof s === 'object' && s['@type'] === 'HowToStep') {
      return { ...s, position: s.position || i + 1 };
    }
    const text = String(s || '').trim();
    if (!text) return null;
    return {
      '@type': 'HowToStep',
      position: i + 1,
      name: text.split(/[.!?:]/, 1)[0].slice(0, 110),
      text,
    };
  }).filter(Boolean);
  if (stepNodes.length < 2) return null;
  const node = {
    '@type': 'HowTo',
    name: name || 'How To',
    description: description || `${name || 'Step-by-step guide'} — ${stepNodes.length} steps.`,
    inLanguage,
    step: stepNodes,
  };
  if (totalTime) node.totalTime = totalTime;
  if (url) node.url = url;
  return { '@context': 'https://schema.org', '@graph': [node] };
}

function _kw(chapterTitle, subjectName, boardName, className) {
  const words = (chapterTitle || '').split(/[\s,\-–—/&]+/).filter(w => w.length > 2);
  const kws = [
    chapterTitle, subjectName, boardName, ...words,
    `${chapterTitle} notes`, `${chapterTitle} definition`, `${chapterTitle} MCQ`,
    `${chapterTitle} ${subjectName}`, `${chapterTitle} ${boardName} ${className}`,
  ].filter(Boolean);
  return [...new Set(kws.map(k => String(k).toLowerCase()))].join(', ');
}

/**
 * Return the first candidate that parses as a valid date, in ISO-8601.
 * Returns `null` (NEVER `Date.now()`) when no real metadata is available so
 * the schema builder can omit the date field instead of fabricating one —
 * AI crawlers use these timestamps to gauge freshness.
 */
function _iso(...candidates) {
  for (const c of candidates) {
    if (!c) continue;
    const d = new Date(c);
    if (!isNaN(d.getTime())) return d.toISOString();
  }
  return null;
}

function _langFromLocale(loc) {
  if (!loc) return 'en-IN';
  const s = String(loc).toLowerCase();
  if (s === 'as' || s.startsWith('as-')) return 'as-IN';
  return 'en-IN';
}

/**
 * Detect numbered steps in markdown / HTML content and emit HowTo schema.
 * Returns null if fewer than 2 steps look like genuine procedure steps.
 */
export function howToFromContent({ title, content, image, totalTime, inLanguage = 'en-IN', tools = [], supplies = [] } = {}) {
  if (!title || !content) return null;
  const text = String(content);
  const steps = [];

  // Generic numbered-step detector. Splits content on "1. ", "2. ", etc.
  // boundaries (line-start OR after a sentence terminator) so both block-style
  // ordered lists ("1.\n2.\n3.") and inline procedural prose
  // ("1. Do X. 2. Do Y.") are recognised.
  const splitRe = /(?:^|[\n.!?]\s+)(\d{1,2})\.\s+/g;
  const stepBoundaries = [...text.matchAll(splitRe)].filter((m) => {
    const idx = parseInt(m[1], 10);
    return idx >= 1 && idx <= 25;
  });
  if (stepBoundaries.length >= 2) {
    let expected = 1;
    for (let i = 0; i < stepBoundaries.length; i++) {
      const m = stepBoundaries[i];
      const idx = parseInt(m[1], 10);
      if (idx !== expected) {
        if (idx === 1 && expected > 1) break;
        continue;
      }
      const start = m.index + m[0].length;
      const end = i + 1 < stepBoundaries.length ? stepBoundaries[i + 1].index : text.length;
      const stepText = text.slice(start, end).replace(/[*_`<>]/g, ' ').replace(/\s+/g, ' ').trim().replace(/[.!?]\s*$/, '');
      if (stepText.length >= 10) {
        steps.push(stepText);
        expected += 1;
      }
    }
  }

  // Fallback: explicit "Step N:" pattern in plain text or HTML.
  if (steps.length < 2) {
    steps.length = 0;
    const stepMatches = [...text.matchAll(/(?:^|>|\n)\s*Step\s+(\d{1,2})\s*[:.\-]\s*([^\n<]{10,300})/gi)];
    for (const m of stepMatches) {
      steps.push(m[2].replace(/[*_`]/g, '').trim());
    }
  }

  if (steps.length < 2) return null;

  const node = {
    '@type': 'HowTo',
    name: title,
    inLanguage,
    step: steps.slice(0, 25).map((s, i) => ({
      '@type': 'HowToStep',
      position: i + 1,
      name: s.length > 80 ? `${s.slice(0, 77)}…` : s,
      text: s,
    })),
  };
  if (image) node.image = image;
  if (totalTime) node.totalTime = totalTime;
  if (Array.isArray(tools) && tools.length) {
    node.tool = tools.map((t) => ({ '@type': 'HowToTool', name: String(t) }));
  }
  if (Array.isArray(supplies) && supplies.length) {
    node.supply = supplies.map((s) => ({ '@type': 'HowToSupply', name: String(s) }));
  }
  return node;
}

/**
 * If the document looks like a tutorial / how-to, return the seed payload for
 * `howToFromContent`. Heuristics: explicit `type` flag, "how to" in title,
 * or `tutorial`/`guide`/`step-by-step` in seo_tags.
 */
export function detectHowToFromDoc(doc) {
  if (!doc) return null;
  const title = String(doc.title || '');
  const tags = String(doc.seo_tags || '').toLowerCase();
  const type = String(doc.type || '').toLowerCase();
  const content = [doc.content, doc.content_html].filter(Boolean).join('\n');
  const hint =
    type === 'tutorial' || type === 'how-to' || type === 'howto' || type === 'guide' ||
    /\bhow\s+to\b/i.test(title) ||
    /\b(tutorial|how[-\s]?to|step[-\s]?by[-\s]?step|guide)\b/.test(tags);
  if (!hint && !/(?:^|\n)\s*(\d{1,2}\.|Step\s+\d+)/i.test(String(content))) return null;
  return {
    title,
    content,
    image: doc.thumbnail_url || undefined,
    inLanguage: _langFromLocale(doc.language || doc.lang || 'en'),
  };
}

export function chapterSchema(data, url, basePath = '') {
  if (!data || !url) return null;
  const subjectName = data.subject_name || '';
  const boardName = data.board_name || '';
  const className = data.class_name || '';
  const chapterTitle = data.topic_title || data.chapter_title || '';
  const subjectUrl = basePath ? `${SITE_ORIGIN}${basePath}` : SITE_ORIGIN;
  const inLanguage = _langFromLocale(data.language || data.lang || 'en');

  const aboutThings = [{ '@type': 'Thing', name: chapterTitle }];
  const words = chapterTitle.split(/[\s,\-–—/&]+/).filter(w => w.length > 2);
  words.slice(0, 5).forEach(w => aboutThings.push({ '@type': 'Thing', name: w }));
  if (data.chapter_title && data.chapter_title !== chapterTitle) {
    aboutThings.push({ '@type': 'Thing', name: data.chapter_title });
  }

  // Real timestamps only — never bake build-time dates into Article
  // metadata, since AI crawlers use dateModified to decide whether the
  // page is fresh enough to cite. If we have neither generated_at nor
  // updated_at, drop the field rather than fabricate one.
  const datePublished = _iso(data.generated_at, data.created_at, data.published_at);
  const dateModified = _iso(data.updated_at, data.modified_at, data.generated_at, data.created_at, data.published_at);

  const authorName = data.author_name || 'Syrabit.ai Editorial Team';
  const authorNode = data.author_name
    ? { '@type': 'Person', name: authorName, affiliation: { '@type': 'Organization', name: 'Syrabit.ai', url: SITE_ORIGIN } }
    : { '@type': ['Organization', 'EducationalOrganization'], name: 'Syrabit.ai', url: SITE_ORIGIN };

  const articleNode = {
    '@type': 'Article',
    headline: data.title,
    description: data.meta_description,
    url,
    author: authorNode,
    publisher: { ...ORG_NODE, address: SYRABIT_ADDRESS },
    educationalLevel: `${className} ${boardName}`.trim(),
    about: aboutThings.length > 1 ? aboutThings : aboutThings[0],
    keywords: _kw(chapterTitle, subjectName, boardName, className),
    wordCount: data.word_count || 0,
    inLanguage: data.has_assamese ? ['en-IN', 'as-IN'] : inLanguage,
    mainEntityOfPage: { '@type': 'WebPage', '@id': url },
    image: `${SITE_ORIGIN}/opengraph.jpg`,
  };
  // Topical-authority pack — give the Article node a STABLE @id so the
  // per-topic LearningResource entries can `isPartOf` it without
  // colliding with the WebPage node (which already owns @id == url).
  // Convention used everywhere else in this file: `${url}#article`.
  const articleNodeId = `${url}#article`;
  articleNode['@id'] = articleNodeId;
  if (datePublished) articleNode.datePublished = datePublished;
  if (dateModified) articleNode.dateModified = dateModified;

  // Topical-authority pack (Task: topical mapping + topical authority).
  // Per-topic LearningResource entries — one structured datum per
  // citable topic on this chapter, each pointing at its anchor on the
  // chapter URL (which is the canonical location per the Task #914
  // contract; the deep-link route just rewrites canonical back here).
  // We also add Article.mentions[] referencing the same topic anchors,
  // which gives crawlers an explicit topic-graph signal that this
  // chapter "covers" each named subtopic.
  const publishedTopics = Array.isArray(data.published_topics) ? data.published_topics : [];
  const topicLearningResources = [];
  const topicMentions = [];
  // Dedupe by topic_slug — upstream data shouldn't surface duplicates,
  // but if it ever does we'd otherwise emit two LearningResource nodes
  // sharing the same `@id` (the topic anchor URL), which is invalid
  // JSON-LD and confuses Schema validators / Google Rich Results.
  const seenTopicSlugs = new Set();
  for (const t of publishedTopics) {
    if (!t || !t.topic_slug || !t.title) continue;
    if (seenTopicSlugs.has(t.topic_slug)) continue;
    seenTopicSlugs.add(t.topic_slug);
    const topicAnchorUrl = `${url}#topic-${t.topic_slug}`;
    const definition = (t.definition || '').trim();
    topicLearningResources.push({
      '@type': 'LearningResource',
      '@id': topicAnchorUrl,
      name: t.title,
      description: definition || `${t.title} — definition and study notes from ${chapterTitle}.`,
      learningResourceType: 'Definition',
      educationalLevel: `${className} ${boardName}`.trim(),
      teaches: t.title,
      // Use the Article node's stable `#article` @id (set above) so the
      // graph reference resolves cleanly. Pointing at `url` instead
      // would silently bind to the WebPage node, which already owns
      // that @id — making the topical-authority signal incorrect.
      isPartOf: { '@type': 'Article', '@id': articleNodeId },
      inLanguage,
      isAccessibleForFree: true,
      url: topicAnchorUrl,
      provider: { '@type': 'Organization', name: 'Syrabit.ai', url: SITE_ORIGIN },
    });
    topicMentions.push({ '@type': 'Thing', name: t.title, url: topicAnchorUrl });
  }
  if (topicMentions.length > 0) {
    articleNode.mentions = topicMentions;
  }

  const graph = [
    articleNode,
    {
      '@type': 'LearningResource',
      name: chapterTitle,
      description: data.meta_description,
      educationalLevel: `${className} ${boardName}`.trim(),
      learningResourceType: 'Study Notes',
      teaches: chapterTitle,
      provider: { '@type': 'Organization', name: 'Syrabit.ai', url: SITE_ORIGIN },
      inLanguage,
      isAccessibleForFree: true,
      url,
      about: { '@type': 'Thing', name: chapterTitle },
    },
    ...topicLearningResources,
    {
      '@type': 'WebPage',
      '@id': url,
      name: data.title,
      inLanguage,
      isPartOf: { '@type': 'WebSite', '@id': SITE_ORIGIN, name: 'Syrabit.ai' },
      speakable: {
        '@type': 'SpeakableSpecification',
        cssSelector: ['article h1', 'article > p:first-of-type', 'article h2'],
      },
    },
    {
      /* Task #246 step 5 — BreadcrumbList using full board › class › subject › chapter hierarchy */
      '@type': 'BreadcrumbList',
      itemListElement: (() => {
        const items = [
          { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_ORIGIN}/` },
        ];
        let pos = 2;
        // Derive board/class URL from basePath (/board/class/subject) when available
        if (basePath) {
          const segs = basePath.split('/').filter(Boolean);
          if (segs.length >= 2 && (boardName || className)) {
            const classLabel = [boardName, className].filter(Boolean).join(' ');
            items.push({ '@type': 'ListItem', position: pos++, name: classLabel, item: `${SITE_ORIGIN}/${segs[0]}/${segs[1]}` });
          }
        }
        items.push({ '@type': 'ListItem', position: pos++, name: subjectName, item: subjectUrl });
        items.push({ '@type': 'ListItem', position: pos, name: chapterTitle, item: url });
        return items;
      })(),
    },
  ];

  const faq = Array.isArray(data.faq_entries) ? data.faq_entries : [];
  const cleanedFaq = faq
    .map(q => ({
      question: (q.question || q.name || '').trim(),
      answer: (q.answer || q.text || '').trim(),
    }))
    .filter(q => q.question.length > 5 && q.answer.length > 10);
  if (cleanedFaq.length >= 2) {
    graph.push({
      '@type': 'FAQPage',
      mainEntity: cleanedFaq.slice(0, 10).map(q => ({
        '@type': 'Question',
        name: q.question,
        acceptedAnswer: { '@type': 'Answer', text: q.answer },
      })),
    });
  }

  // Task #336: detect numbered/stepwise content in chapter body and
  // emit a HowTo node so AI assistants can surface step-by-step
  // procedural answers. Prefer the smarter heuristic (detectHowToFromDoc
  // + howToFromContent) which gates on title/type signals, then fall
  // back to the simpler line-based extractor when that returns nothing.
  const howToSeed = detectHowToFromDoc({
    title: chapterTitle || data.title,
    content: data.content || data.content_html || '',
    type: data.type,
    seo_tags: data.seo_tags,
    thumbnail_url: data.thumbnail_url,
    language: data.language || data.lang,
  });
  let howToEmitted = false;
  if (howToSeed) {
    const howTo = howToFromContent(howToSeed);
    if (howTo) {
      // howToFromContent may return either a single node or a {@graph} wrapper.
      if (Array.isArray(howTo['@graph'])) graph.push(...howTo['@graph']);
      else graph.push(howTo);
      howToEmitted = true;
    }
  }
  if (!howToEmitted) {
    const howToSteps = extractHowToSteps(data.content || '');
    if (howToSteps.length >= 2) {
      graph.push({
        '@type': 'HowTo',
        name: chapterTitle,
        description: data.meta_description || `${chapterTitle} — step-by-step.`,
        inLanguage: 'en-IN',
        step: howToSteps,
      });
    }
  }

  return { '@context': 'https://schema.org', '@graph': graph };
}

/**
 * Task #336: schema for PYQ (Previous Year Question paper) replicas.
 * Emits Quiz + LearningResource + BreadcrumbList. Doc shape mirrors
 * what the worker returns from `/pyq/{slug}`:
 *   { slug, title, description?, board?, year?, subject?, class?, exam? }
 */
export function pyqSchema(doc, url) {
  if (!doc || !url) return null;
  const slug = doc.slug || '';
  const title = doc.title || `Previous Year Question Paper — ${slug}`;
  const subject = doc.subject || doc.subject_name || '';
  const board = doc.board || doc.board_name || '';
  const className = doc.class || doc.class_name || '';
  const year = doc.year || doc.exam_year || '';
  const description = doc.description
    || `${board ? board + ' ' : ''}${subject ? subject + ' ' : ''}${year ? year + ' ' : ''}previous year question paper for ${className || 'students'}.`;
  const eduLevel = `${className} ${board}`.trim() || 'Higher Secondary';

  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Quiz',
        '@id': url,
        name: title,
        description,
        url,
        about: subject ? { '@type': 'Thing', name: subject } : undefined,
        educationalLevel: eduLevel,
        educationalAlignment: board
          ? {
              '@type': 'AlignmentObject',
              alignmentType: 'educationalSubject',
              educationalFramework: board,
              targetName: subject || 'General',
            }
          : undefined,
        learningResourceType: 'Question Paper',
        inLanguage: 'en-IN',
        author: { '@type': 'Organization', name: board || 'Syrabit.ai' },
        publisher: ORG_NODE,
      },
      {
        '@type': 'LearningResource',
        name: title,
        description,
        url,
        learningResourceType: 'Past Examination Paper',
        educationalLevel: eduLevel,
        inLanguage: 'en-IN',
        isAccessibleForFree: true,
        provider: ORG_NODE,
        license: 'https://syrabit.ai/terms',
      },
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_ORIGIN}/` },
          { '@type': 'ListItem', position: 2, name: 'Library', item: `${SITE_ORIGIN}/library` },
          { '@type': 'ListItem', position: 3, name: title, item: url },
        ],
      },
    ],
  };
}

export function subjectHubSchema(subject, url) {
  if (!subject || !url) return null;
  const eduLevel = ((subject.class_name || '') + ' ' + (subject.board_name || '') + ' ' + (subject.stream_name || '')).replace(/\s+/g, ' ').trim() || 'FYUGP';
  const description = subject.description || `Complete ${subject.name} notes and study material for ${eduLevel} students.`;
  const chapters = Array.isArray(subject.chapters) ? subject.chapters : [];
  const inLanguage = _langFromLocale(subject.language || 'en');

  const graph = [
    {
      '@type': 'EducationalOrganization',
      '@id': `${SITE_ORIGIN}/#organization`,
      name: 'Syrabit.ai',
      url: SITE_ORIGIN,
      logo: `${SITE_ORIGIN}/icons/icon-192x192.png`,
      sameAs: [SITE_ORIGIN],
    },
    {
      '@type': 'CollectionPage',
      '@id': url,
      name: `${subject.name} — ${eduLevel}`,
      description,
      url,
      isPartOf: { '@type': 'WebSite', '@id': SITE_ORIGIN, name: 'Syrabit.ai' },
      inLanguage,
      hasPart: chapters.slice(0, 50).map(ch => ({
        '@type': 'LearningResource',
        name: ch.title,
        url: subject.board_slug && subject.class_slug && subject.slug && ch.slug
          ? `${SITE_ORIGIN}/${subject.board_slug}/${subject.class_slug}/${subject.slug}/${ch.slug}`
          : undefined,
        learningResourceType: 'Study Notes',
        inLanguage,
      })),
    },
    {
      '@type': 'Course',
      name: `${subject.name} — ${eduLevel}`,
      description,
      provider: { '@type': 'Organization', name: 'Syrabit.ai', sameAs: SITE_ORIGIN },
      educationalLevel: eduLevel,
      url,
      inLanguage,
    },
    {
      /* Task #246 step 5 — BreadcrumbList using full board › class › subject hierarchy */
      '@type': 'BreadcrumbList',
      itemListElement: (() => {
        const items = [
          { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_ORIGIN}/` },
        ];
        let pos = 2;
        // Use board+class slugs to build an intermediate breadcrumb level
        if (subject.board_slug && subject.class_slug) {
          const classLabel = [subject.board_name || subject.board_slug, subject.class_name || subject.class_slug].filter(Boolean).join(' ');
          items.push({ '@type': 'ListItem', position: pos++, name: classLabel, item: `${SITE_ORIGIN}/${subject.board_slug}/${subject.class_slug}` });
        }
        items.push({ '@type': 'ListItem', position: pos, name: subject.name, item: url });
        return items;
      })(),
    },
  ];

  // Auto-FAQ from chapter descriptions only when caller hasn't supplied
  // their own FAQ via PageMeta.jsonLd. Callers can opt-out with
  // subject.skipAutoFaq = true.
  if (!subject.skipAutoFaq) {
    const faqEntries = [];
    for (const ch of chapters) {
      if (ch.title && ch.description && ch.description.length > 20) {
        faqEntries.push({
          '@type': 'Question',
          name: `What is ${ch.title}?`,
          acceptedAnswer: { '@type': 'Answer', text: ch.description },
        });
      }
      if (faqEntries.length >= 10) break;
    }
    if (faqEntries.length >= 2) {
      graph.push({ '@type': 'FAQPage', mainEntity: faqEntries });
    }
  }

  return { '@context': 'https://schema.org', '@graph': graph };
}

export function libraryLandingSchema(subjects, url) {
  if (!Array.isArray(subjects) || subjects.length === 0 || !url) return null;
  const items = subjects.map((s, i) => ({
    '@type': 'ListItem',
    position: i + 1,
    item: {
      '@type': 'LearningResource',
      name: s.name,
      description: s.description || `Study ${s.name} — ${s.boardName || ''} ${s.className || ''}`.trim(),
      url: s.boardSlug && s.classSlug && s.slug
        ? `${SITE_ORIGIN}/${s.boardSlug}/${s.classSlug}/${s.slug}`
        : `${SITE_ORIGIN}/subject/${s.id}`,
      provider: { '@type': 'Organization', name: 'Syrabit.ai', url: SITE_ORIGIN },
      educationalLevel: `${s.className || ''} ${s.boardName || ''}`.trim(),
      inLanguage: 'en-IN',
      isAccessibleForFree: true,
    },
  }));

  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Course',
        name: 'Syrabit.ai — Assam Board Study Library',
        description: 'Course catalogue covering AHSEC, SEBA and FYUGP subjects with AI-generated notes, MCQs, definitions and exam prep.',
        provider: { '@type': 'Organization', name: 'Syrabit.ai', sameAs: SITE_ORIGIN },
        educationalLevel: 'Higher Secondary, Undergraduate',
        url,
        inLanguage: 'en-IN',
        hasCourseInstance: {
          '@type': 'CourseInstance',
          courseMode: 'online',
          courseWorkload: 'PT1H',
          inLanguage: 'en-IN',
        },
      },
      {
        '@type': 'ItemList',
        name: 'Assamboard Subject Library',
        description: 'Complete study material library for Assam Board (AHSEC/SEBA/FYUGP) students.',
        numberOfItems: items.length,
        itemListElement: items,
      },
      {
        '@type': 'WebPage',
        '@id': url,
        name: 'Assamboard Subject Library — Study Notes, MCQs & Exam Prep',
        url,
        isPartOf: { '@type': 'WebSite', '@id': SITE_ORIGIN, name: 'Syrabit.ai' },
        inLanguage: 'en-IN',
      },
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: SITE_ORIGIN },
          { '@type': 'ListItem', position: 2, name: 'Library', item: url },
        ],
      },
    ],
  };
}

export function homeSchema(url) {
  const homeUrl = url || `${SITE_ORIGIN}/`;
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization',
        '@id': `${SITE_ORIGIN}/#organization`,
        name: 'Syrabit.ai',
        url: SITE_ORIGIN,
        logo: { '@type': 'ImageObject', url: `${SITE_ORIGIN}/icons/icon-192x192.png` },
        sameAs: [SITE_ORIGIN, 'https://twitter.com/SyrabitAI'],
        address: {
          '@type': 'PostalAddress',
          addressLocality: 'Guwahati',
          addressRegion: 'Assam',
          postalCode: '781001',
          addressCountry: 'IN',
        },
        areaServed: [
          { '@type': 'State', name: 'Assam', containedInPlace: { '@type': 'Country', name: 'India' } },
          { '@type': 'City', name: 'Guwahati' },
        ],
      },
      {
        '@type': 'LocalBusiness',
        '@id': `${SITE_ORIGIN}/#localbusiness`,
        name: 'Syrabit.ai',
        url: SITE_ORIGIN,
        image: `${SITE_ORIGIN}/icons/icon-512x512.png`,
        address: {
          '@type': 'PostalAddress',
          addressLocality: 'Guwahati',
          addressRegion: 'Assam',
          postalCode: '781001',
          addressCountry: 'IN',
        },
        geo: { '@type': 'GeoCoordinates', latitude: 26.1445, longitude: 91.7362 },
        areaServed: [
          { '@type': 'State', name: 'Assam' },
          { '@type': 'City', name: 'Guwahati' },
        ],
        priceRange: '₹0–₹999',
      },
      {
        '@type': 'WebSite',
        '@id': SITE_ORIGIN,
        url: SITE_ORIGIN,
        name: 'Syrabit.ai',
        publisher: { '@id': `${SITE_ORIGIN}/#organization` },
        inLanguage: 'en-IN',
        /* Task #246 step 5 — SiteLinksSearchBox: per schema.org, SearchAction
           in potentialAction of a WebSite node is the canonical signal Google
           uses to render the sitelinks search box in SERPs. */
        potentialAction: {
          '@type': 'SearchAction',
          target: {
            '@type': 'EntryPoint',
            urlTemplate: `${SITE_ORIGIN}/library?q={search_term_string}`,
          },
          'query-input': 'required name=search_term_string',
        },
      },
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: homeUrl },
        ],
      },
    ],
  };
}

/**
 * LearnPage CMS document → Article + LearningResource (+ HowTo + Breadcrumb).
 * Pulls dates and language from the document metadata, never from build time.
 */
export function learnArticleSchema(doc, url) {
  if (!doc || !url) return null;
  const eduLevel = doc.class_name || doc.board_name || doc.geo_tags || 'Assam Board';
  const description = doc.meta_description || doc.description || '';
  const inLanguage = _langFromLocale(doc.language || doc.lang || 'en');
  const datePublished = _iso(doc.published_at, doc.created_at, doc.generated_at);
  const dateModified = _iso(doc.updated_at, doc.modified_at, doc.published_at, doc.created_at, doc.generated_at);
  const authorName = doc.author_name || 'Syrabit.ai Editorial Team';
  const authorNode = doc.author_name
    ? { '@type': 'Person', name: authorName, affiliation: { '@type': 'Organization', name: 'Syrabit.ai', url: SITE_ORIGIN } }
    : { '@type': ['Organization', 'EducationalOrganization'], name: 'Syrabit.ai', url: SITE_ORIGIN };

  const aboutName = doc.primary_keyword || doc.title;

  const learnArticleNode = {
    '@type': 'Article',
    headline: doc.title,
    description,
    author: authorNode,
    publisher: {
      '@type': ['Organization', 'EducationalOrganization'],
      name: 'Syrabit.ai',
      url: SITE_ORIGIN,
      logo: { '@type': 'ImageObject', url: `${SITE_ORIGIN}/icons/icon-192x192.png` },
    },
    keywords: doc.seo_tags || '',
    inLanguage,
    educationalLevel: eduLevel,
    about: { '@type': 'Thing', name: aboutName },
    mainEntityOfPage: { '@type': 'WebPage', '@id': url },
    isPartOf: { '@type': 'WebSite', '@id': SITE_ORIGIN, name: 'Syrabit.ai' },
    image: doc.thumbnail_url || `${SITE_ORIGIN}/opengraph.jpg`,
    url,
  };
  if (datePublished) learnArticleNode.datePublished = datePublished;
  if (dateModified) learnArticleNode.dateModified = dateModified;

  const graph = [
    learnArticleNode,
    {
      '@type': 'BreadcrumbList',
      itemListElement: [
        { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_ORIGIN}/` },
        { '@type': 'ListItem', position: 2, name: 'Library', item: `${SITE_ORIGIN}/library` },
        { '@type': 'ListItem', position: 3, name: doc.title, item: url },
      ],
    },
    {
      '@type': 'LearningResource',
      name: `${doc.title} — ${eduLevel}`,
      description: description || `Study material for ${doc.title}`,
      provider: { '@type': ['Organization', 'EducationalOrganization'], name: 'Syrabit.ai', sameAs: SITE_ORIGIN },
      educationalLevel: eduLevel,
      url,
      inLanguage,
      learningResourceType: doc.type || 'Study Material',
      isAccessibleForFree: true,
      about: { '@type': 'Thing', name: aboutName },
    },
  ];

  const howToSeed = detectHowToFromDoc(doc);
  if (howToSeed) {
    const howTo = howToFromContent(howToSeed);
    if (howTo) graph.push(howTo);
  }

  return { '@context': 'https://schema.org', '@graph': graph };
}

/**
 * PYQ (previous year question paper) page → Dataset + Quiz + Breadcrumb.
 * `meta` is best-effort: { slug, title, description, board, subject, year,
 * educationalLevel, inLanguage, license, totalQuestions, author,
 * published_at, updated_at, paper_type, dateCreated }.
 *
 * When the worker backfills real metadata (Task #338) we surface
 * `numberOfQuestions`, a per-paper `author` (typically the board), and
 * `dateModified` so Google can render richer Dataset / Quiz snippets.
 */
export function pyqDatasetSchema(meta, url) {
  if (!meta || !url) return null;
  const title = meta.title || `Previous Year Question Paper${meta.year ? ` ${meta.year}` : ''}`;
  const description = meta.description || `Previous year question paper${meta.subject ? ` for ${meta.subject}` : ''}${meta.board ? ` (${meta.board})` : ''}${meta.year ? `, ${meta.year}` : ''}.`;
  const inLanguage = _langFromLocale(meta.inLanguage || meta.language || 'en');
  const license = meta.license || 'https://creativecommons.org/licenses/by-nc/4.0/';
  const educationalLevel = meta.educationalLevel || meta.class_name || meta.board || 'Higher Secondary';
  const datePublished = _iso(meta.published_at, meta.created_at, meta.dateCreated, meta.year ? `${meta.year}-01-01` : null);
  const dateModified = _iso(meta.updated_at, meta.modified_at, meta.dateModified);
  const totalQuestionsRaw = meta.totalQuestions ?? meta.total_questions ?? meta.question_count;
  const totalQuestions = Number.isFinite(Number(totalQuestionsRaw)) && Number(totalQuestionsRaw) > 0
    ? Number(totalQuestionsRaw) : null;
  const authorName = meta.author || meta.board || (meta.subject ? `${meta.subject} Examiners` : '');
  const authorNode = authorName
    ? { '@type': 'Organization', name: authorName }
    : undefined;

  const datasetNode = {
    '@type': 'Dataset',
    name: title,
    description,
    url,
    identifier: meta.slug || url,
    inLanguage,
    license,
    creator: authorNode || ORG_NODE,
    publisher: ORG_NODE,
    keywords: [meta.subject, meta.board, meta.year ? `${meta.year}` : '', 'previous year question paper', 'PYQ']
      .filter(Boolean).join(', '),
    about: meta.subject ? { '@type': 'Thing', name: meta.subject } : undefined,
    educationalLevel,
    isAccessibleForFree: true,
  };
  if (datePublished) datasetNode.datePublished = datePublished;
  if (dateModified) datasetNode.dateModified = dateModified;
  if (totalQuestions) {
    datasetNode.variableMeasured = {
      '@type': 'PropertyValue',
      name: 'Number of questions',
      value: totalQuestions,
    };
  }

  const quizNode = {
    '@type': 'Quiz',
    name: title,
    about: meta.subject ? { '@type': 'Thing', name: meta.subject } : undefined,
    educationalLevel,
    inLanguage,
    learningResourceType: 'Question Paper',
    url,
    provider: ORG_NODE,
    license,
  };
  if (authorNode) quizNode.author = authorNode;
  if (totalQuestions) quizNode.numberOfQuestions = totalQuestions;
  if (datePublished) quizNode.dateCreated = datePublished;

  return {
    '@context': 'https://schema.org',
    '@graph': [
      datasetNode,
      quizNode,
      {
        '@type': 'BreadcrumbList',
        itemListElement: [
          { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_ORIGIN}/` },
          { '@type': 'ListItem', position: 2, name: 'Library', item: `${SITE_ORIGIN}/library` },
          { '@type': 'ListItem', position: 3, name: title, item: url },
        ],
      },
    ],
  };
}

/**
 * Drop schema.org @graph nodes from `typedGraph` whose @type already appears
 * in any of the `externalGraphs` (or in plain `externalNodes`). Prevents
 * double-emitting FAQPage / Article / etc. when a page supplies its own
 * jsonLd alongside the per-page-type builder output.
 */
export function dedupeGraphTypes(typedGraph, externalGraphs = []) {
  if (!typedGraph || !Array.isArray(typedGraph['@graph'])) return typedGraph;
  const seen = new Set();
  const externals = Array.isArray(externalGraphs) ? externalGraphs : [externalGraphs];
  for (const ext of externals) {
    if (!ext) continue;
    if (Array.isArray(ext['@graph'])) {
      for (const n of ext['@graph']) {
        if (n && n['@type']) (Array.isArray(n['@type']) ? n['@type'] : [n['@type']]).forEach(t => seen.add(t));
      }
    } else if (ext['@type']) {
      (Array.isArray(ext['@type']) ? ext['@type'] : [ext['@type']]).forEach(t => seen.add(t));
    }
  }
  // Always allow these to appear multiple times — they carry per-page detail.
  const ALWAYS_KEEP = new Set(['BreadcrumbList', 'WebPage', 'ListItem']);
  const filtered = typedGraph['@graph'].filter((n) => {
    if (!n || !n['@type']) return true;
    const types = Array.isArray(n['@type']) ? n['@type'] : [n['@type']];
    if (types.some(t => ALWAYS_KEEP.has(t))) return true;
    return !types.some(t => seen.has(t));
  });
  return { ...typedGraph, '@graph': filtered };
}

export function buildSchemaForPageType(pageType, payload) {
  switch (pageType) {
    case 'chapter':
      return chapterSchema(payload?.data, payload?.url, payload?.basePath);
    case 'subject':
      return subjectHubSchema(payload?.subject, payload?.url);
    case 'library':
      return libraryLandingSchema(payload?.subjects, payload?.url);
    case 'home':
      return homeSchema(payload?.url);
    case 'learn':
      return learnArticleSchema(payload?.doc, payload?.url);
    case 'pyq':
      // Accept either {meta} (canonical) or {doc} (legacy). Prefer the
      // richer Dataset+Quiz emission; fall back to the slim Quiz-only
      // pyqSchema if pyqDatasetSchema declines (returns null).
      return pyqDatasetSchema(payload?.meta || payload?.doc, payload?.url)
        ?? pyqSchema(payload?.doc || payload?.meta, payload?.url);
    default:
      return null;
  }
}
