const WEB_SEARCH_LIMIT = 4;
// Retrieval starts in parallel with quota, embedding, history, and curriculum.
// Keep the external branch below one second so an upstream stall cannot consume
// the 3 s first-token budget.
export const WEB_SEARCH_TIMEOUT_MS = 1_100;
const WEB_SNIPPET_CHAR_CAP = 500;
const STRONG_RAG_SCORE = 0.80;
const MIN_STRONG_RAG_CHARS = 500;

export interface WebSearchResult {
  title: string;
  url: string;
  snippet: string;
  source: 'web_search';
}

export interface WebSearchResponse {
  results: WebSearchResult[];
  status: 'ok' | 'empty' | 'timeout' | 'error' | 'skipped';
  durationMs: number;
}

interface CrossrefWork {
  title?: string[];
  URL?: string;
  abstract?: string;
  'container-title'?: string[];
  published?: {
    'date-parts'?: number[][];
  };
}

interface CrossrefResponse {
  message?: {
    items?: CrossrefWork[];
  };
}

interface OfficialPageRecord {
  link?: string;
  title?: { rendered?: string };
  content?: { rendered?: string };
  modified?: string;
}

interface GeneralSearchItem {
  title: string;
  url: string;
  snippet: string;
  publisherUrl?: string;
}

interface WebSearchCache {
  get(key: string): Promise<string | null>;
  put(key: string, value: string, options: { expirationTtl: number }): Promise<void>;
}

interface ProviderSearchResult {
  results: WebSearchResult[];
  status: 'ok' | 'empty' | 'error';
}

interface SearchOptions {
  timeoutMs?: number;
  fetcher?: typeof fetch;
  cache?: WebSearchCache;
}

const OFFICIAL_PAGE_CACHE_KEY = 'web-search:official:asseb:v2';
const OFFICIAL_PAGE_CACHE_TTL_SECONDS = 15 * 60;

const REPUTABLE_HOSTS = new Set([
  'ahsec.assam.gov.in',
  'asseb.in',
  'education.gov.in',
  'ncert.nic.in',
  'cbse.gov.in',
  'pib.gov.in',
  'thehindu.com',
  'indianexpress.com',
  'timesofindia.indiatimes.com',
  'hindustantimes.com',
  'bbc.com',
  'reuters.com',
  'apnews.com',
  'britannica.com',
  'wikipedia.org',
  'northeastlivetv.com',
  'careers360.com',
  'livemint.com',
  'shiksha.com',
  'financialexpress.com',
  'ndtv.com',
  'indiatoday.in',
  'news18.com',
  'telegraphindia.com',
]);
const REPUTABLE_SUFFIXES = ['.gov.in', '.nic.in', '.ac.in', '.edu.in'];
const QUERY_STOP_WORDS = new Set([
  'about', 'answer', 'current', 'currently', 'find', 'latest', 'news', 'official',
  'online', 'recent', 'search', 'source', 'sources', 'status', 'today', 'update',
  'updates', 'use', 'using', 'web', 'what', 'when', 'where', 'which', 'with',
]);

const FRESHNESS_INTENT =
  /\b(latest|today|recent|recently|news|updated?|this (?:week|month|year)|20(?:2[6-9]|[3-9]\d))\b|শেহতীয়া|সাম্প্ৰতিক|বৰ্তমান|আজিৰ|খবৰ|আপডেট/i;
const WEB_INTENT =
  /\b(search (?:the )?web|web search|look online|online sources?|on the internet|news sources?)\b|ৱেব|ইণ্টাৰনেট|অনলাইন/i;
const OFFICIAL_BOARD_INTENT =
  /\b(?:ahsec|asseb|seba|assam education|assam higher secondary education council|assam (?:state )?(?:school education )?board)\b|অসম(?:ৰ)?\s+(?:ৰাজ্যিক\s+)?(?:বিদ্যালয়\s+শিক্ষা\s+)?(?:পৰিষদ|বোর্ড|ব['’]?ৰ্ড|বৰ্ড)/i;
const HARD_BOARD_TIME_SIGNAL =
  /\b(?:dates?|deadline|schedule)\b|তাৰিখ|সময়সীমা|সময়সূচী|সময়সূচী/i;
const CONDITIONAL_TIME_SIGNAL =
  /\bwhen\b|\bhow\s+(?:soon|long)\b|\b(?:at\s+)?what\s+time\b|\bstart\s+time\b|\btiming\b|কেতিয়া|কিমান\s+(?:সোনকালে|সময়|দিন|ঘণ্টা)|কেইটা\s+বজাত|কিমান\s+বজাত|কি\s+সময়ত/i;
const BOARD_ADMIN_CONTEXT =
  /\b(?:form\s*fill(?:-?up)?|merit\s*list|correction\s*window|results?|routine|time\s*table|exams?|examinations?|admissions?|registrations?|scholarships?|re-?check(?:ing)?|re-?evaluation|notifications?|notices?|announcements?|syllab(?:us|i)|curriculum|calendar|status)\b|ফলাফল|সময়সূচী|ৰুটিন|পৰীক্ষা|নামভৰ্তি|পঞ্জীয়ন|বৃত্তি|পুনৰীক্ষণ|জাননী|পাঠ্যক্ৰম|পঞ্জিকা|স্থিতি/i;
const CURRENT_BOARD_TOPIC =
  /\b(?:results?|routine|time\s*table|exams?|examinations?|admissions?|registrations?|scholarships?|re-?check(?:ing)?|re-?evaluation|notifications?|notices?|status)\b|ফলাফল|সময়সূচী|ৰুটিন|পৰীক্ষা|নামভৰ্তি|পঞ্জীয়ন|বৃত্তি|পুনৰীক্ষণ|জাননী|স্থিতি/i;
const INTRINSIC_CURRENT_BOARD_TOPIC =
  /\b(?:(?:exam(?:ination)?|board)\s+)?(?:routine|time\s*table|schedule)\b|\b(?:exam|examination)\s+dates?\b|\bresults?\s+(?:date|announcement)\b|\b(?:admission|registration)\s+(?:date|deadline|schedule)\b|\b(?:notification|notice)\s+(?:date|deadline)\b|সময়সূচী|ৰুটিন|ফলাফলৰ?\s+তাৰিখ|নামভৰ্তিৰ?\s+(?:তাৰিখ|সময়সীমা)|পঞ্জীয়নৰ?\s+(?:তাৰিখ|সময়সীমা)/i;
const SCIENTIFIC_CURRENT_CONTEXT =
  /\b(?:(?:electric(?:al)?|alternating|direct|ac|dc)\s+current|current\s+(?:electricity|flow|through|in\s+(?:an?\s+|the\s+)?(?:wire|circuit|conductor)))\b/gi;

function isCurrentOfficialBoardQuestion(question: string): boolean {
  return OFFICIAL_BOARD_INTENT.test(question)
    && (
      INTRINSIC_CURRENT_BOARD_TOPIC.test(question)
      || HARD_BOARD_TIME_SIGNAL.test(question)
      || (CONDITIONAL_TIME_SIGNAL.test(question) && BOARD_ADMIN_CONTEXT.test(question))
    );
}

function hasExplicitFreshnessIntent(question: string): boolean {
  const withoutScientificCurrent = question.replace(SCIENTIFIC_CURRENT_CONTEXT, ' ');
  return FRESHNESS_INTENT.test(question)
    || (
      OFFICIAL_BOARD_INTENT.test(question)
      && /\bcurrent(?:ly)?\b/i.test(withoutScientificCurrent)
    );
}

function isInstitutionStatusIntent(question: string): boolean {
  if (/\b(?:formed|formation|merged?|merger)\b|গঠন|একত্ৰীকৰণ/i.test(question)) return true;
  const normalized = canonicalizeMaterialText(question)
    .trim()
    .replace(/\s*(?:please\s+)?use\s+(?:the\s+)?web\s+context\s+if\s+needed[.!?]*\s*$/i, '')
    .trim();
  const board =
    '(?:ahsec|asseb|seba|assam higher secondary education council|assam state school education board)';
  return new RegExp(
    `^(?:(?:what(?:'s| is)?|tell me|show me)\\s+)?(?:the\\s+)?(?:current\\s+)?status\\s+of\\s+(?:the\\s+)?${board}(?:\\s+and\\s+${board})?\\s*[?.]*$`,
    'i',
  ).test(normalized) || new RegExp(
    `^(?:(?:what(?:'s| is)?|tell me|show me)\\s+)?(?:the\\s+)?${board}(?:\\s+and\\s+${board})?\\s+(?:current\\s+)?status\\s*[?.]*$`,
    'i',
  ).test(normalized)
    || /^(?:ahsec|asseb|seba)(?:ৰ)?\s+(?:বৰ্তমান\s+)?স্থিতি\s*(?:কি)?\s*[?।]*$/i.test(normalized);
}

/**
 * Textbook page context remains the fast authoritative path. Web search is
 * reserved for explicit freshness or web intent. Ordinary unscoped educational
 * questions should proceed immediately through curriculum retrieval and the LLM;
 * a generic scholarly search adds latency and often returns irrelevant papers.
 */
export function shouldUseWebSearch(opts: {
  question: string;
  chapterId?: string | undefined;
  subjectId?: string | undefined;
}): boolean {
  const question = opts.question.trim();
  if (!question) return false;
  return hasExplicitFreshnessIntent(question)
    || WEB_INTENT.test(question)
    || isCurrentOfficialBoardQuestion(question);
}

/**
 * Web lookup is prestarted beside embedding for every non-authoritative turn,
 * but only contributes evidence when explicitly requested or curriculum
 * retrieval is too weak to stand alone.
 */
export function shouldUseWebEvidence(opts: {
  explicitWebIntent: boolean;
  topScore: number;
  contextContents: string[];
}): boolean {
  if (opts.explicitWebIntent) return true;
  const hasSubstantialContext = opts.contextContents.some(
    content => content.replace(/\s+/g, ' ').trim().length >= MIN_STRONG_RAG_CHARS,
  );
  return opts.topScore < STRONG_RAG_SCORE || !hasSubstantialContext;
}

function canonicalWebUrl(rawUrl: string): string {
  try {
    const url = new URL(rawUrl);
    url.hash = '';
    for (const key of [...url.searchParams.keys()]) {
      if (/^(utm_|ref$|source$)/i.test(key)) url.searchParams.delete(key);
    }
    return url.toString().replace(/\/$/, '');
  } catch {
    return rawUrl.trim();
  }
}

export function dedupeWebResults(results: WebSearchResult[]): WebSearchResult[] {
  const seen = new Set<string>();
  const deduped: WebSearchResult[] = [];
  for (const result of results) {
    const url = canonicalWebUrl(result.url);
    const textKey = `${result.title} ${result.snippet}`
      .toLowerCase()
      .replace(/[^a-z0-9\u0980-\u09ff]+/g, ' ')
      .trim()
      .slice(0, 220);
    if (!url || seen.has(`url:${url}`) || (textKey && seen.has(`text:${textKey}`))) continue;
    seen.add(`url:${url}`);
    if (textKey) seen.add(`text:${textKey}`);
    deduped.push({ ...result, url });
    if (deduped.length >= WEB_SEARCH_LIMIT) break;
  }
  return deduped;
}

export function buildWebSearchQuery(question: string, lang: 'en' | 'as'): string {
  const normalized = question.replace(/\s+/g, ' ').trim().slice(0, 300);
  const scope = lang === 'as'
    ? 'অসম শিক্ষা'
    : 'Assam education';
  return `${normalized} ${scope}`;
}

function textOnly(value: string): string {
  return value
    .replace(/<[^>]*>/g, ' ')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/&lt;|&gt;/g, ' ')
    .replace(/[<>]/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/[\u0000-\u001F\u007F]/g, '')
    .trim();
}

function officialHtmlEvidenceUnits(value: string): string[] {
  const units: string[] = [];
  const stack: Array<{
    tag: string;
    contentStart: number;
    hasNestedEvent: boolean;
  }> = [];
  const structuralTag = /<\/?(p|li|tr|h[1-6])\b[^>]*>/gi;
  for (const match of value.matchAll(structuralTag)) {
    const rawTag = match[0] ?? '';
    const tag = (match[1] ?? '').toLowerCase();
    if (!rawTag.startsWith('</')) {
      if (tag !== 'tr' && stack.some(entry => entry.tag === 'tr')) {
        continue;
      }
      if (stack.length > 0) stack[stack.length - 1]!.hasNestedEvent = true;
      stack.push({
        tag,
        contentStart: (match.index ?? 0) + rawTag.length,
        hasNestedEvent: false,
      });
      continue;
    }
    let openIndex = -1;
    for (let index = stack.length - 1; index >= 0; index -= 1) {
      if (stack[index]?.tag === tag) {
        openIndex = index;
        break;
      }
    }
    if (openIndex < 0) continue;
    const [entry] = stack.splice(openIndex, 1);
    if (!entry || entry.hasNestedEvent) continue;
    const unit = textOnly(value.slice(entry.contentStart, match.index ?? value.length));
    if (unit) units.push(unit);
  }
  if (units.length > 0) return units;

  return value
    .replace(/<\/?(?:br|div|section|article|p|li|tr|h[1-6])\b[^>]*>/gi, '\n')
    .split(/\n+/)
    .map(textOnly)
    .filter(Boolean);
}

function decodeXml(value: string): string {
  return value
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&#(\d+);/g, (_match, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&#x([\da-f]+);/gi, (_match, code: string) => String.fromCodePoint(parseInt(code, 16)))
    .replace(/&quot;/g, '"')
    .replace(/&apos;|&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');
}

function isReputableHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^www\./, '');
  return REPUTABLE_HOSTS.has(host)
    || [...REPUTABLE_HOSTS].some(allowed => host.endsWith(`.${allowed}`))
    || REPUTABLE_SUFFIXES.some(suffix => host.endsWith(suffix));
}

function topicalQueryTokens(query: string): Set<string> {
  return new Set(query
    .toLowerCase()
    .match(/[a-z0-9\u0980-\u09ff]+/g)
    ?.filter(token => token.length >= 4 && !QUERY_STOP_WORDS.has(token)) ?? []);
}

function hasTopicalOverlap(item: GeneralSearchItem, queryTokens: Set<string>): boolean {
  if (queryTokens.size === 0) return false;
  const evidence = `${item.title} ${item.snippet} ${item.url}`.toLowerCase();
  return [...queryTokens].some(token => evidence.includes(token));
}

function boundedGeneralResult(
  item: GeneralSearchItem,
  queryTokens: Set<string>,
): WebSearchResult | null {
  if (!hasTopicalOverlap(item, queryTokens)) return null;
  const title = textOnly(decodeXml(item.title));
  const snippet = textOnly(decodeXml(item.snippet)).slice(0, WEB_SNIPPET_CHAR_CAP);
  let url: URL;
  let publisherUrl: URL | null = null;
  try {
    url = new URL(decodeXml(item.url).trim());
    publisherUrl = item.publisherUrl ? new URL(decodeXml(item.publisherUrl).trim()) : null;
  } catch {
    return null;
  }
  if (url.protocol === 'http:' && isReputableHost(url.hostname)) url.protocol = 'https:';
  const isGoogleNewsArticle = url.protocol === 'https:' && url.hostname === 'news.google.com';
  const trustedPublisher = publisherUrl?.protocol === 'https:'
    && isReputableHost(publisherUrl.hostname);
  if (!(isGoogleNewsArticle && trustedPublisher)
    && (url.protocol !== 'https:' || !isReputableHost(url.hostname))) return null;
  if (!title || snippet.length < 20) return null;
  return {
    title: title.slice(0, 180),
    url: url.toString(),
    snippet,
    source: 'web_search',
  };
}

function parseOfficialStatusPage(html: string, url: string): WebSearchResult[] {
  const title = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? '';
  const body = textOnly(decodeXml(html));
  const mergerIndex = body.toLowerCase().indexOf('formed under');
  if (mergerIndex < 0) return [];
  const snippet = body.slice(mergerIndex, mergerIndex + WEB_SNIPPET_CHAR_CAP);
  return [{
    title: textOnly(decodeXml(title)).slice(0, 180),
    url,
    snippet,
    source: 'web_search',
  }];
}

function officialSearchTopic(question: string): string | null {
  const topics: Array<[RegExp, string]> = [
    [/\b(?:re-?check(?:ing)?|re-?evaluation)\b|পুনৰীক্ষণ/i, 're-checking'],
    [/\b(?:admissions?|enrolments?)\b|নামভৰ্তি/i, 'admission'],
    [/\bregistrations?\b|পঞ্জীয়ন/i, 'registration'],
    [/\bscholarships?\b|বৃত্তি/i, 'scholarship'],
    [/\bcalendar\b|পঞ্জিকা/i, 'calendar'],
    [/\b(?:syllab(?:us|i)|curriculum)\b|পাঠ্যক্ৰম/i, 'syllabus'],
    [/\b(?:routine|time\s*table|schedule)\b|সময়সূচী|ৰুটিন/i, 'routine'],
    [/\b(?:results?|marks?|scorecard)\b|ফলাফল/i, 'result'],
    [/\b(?:exams?|examinations?)\b|পৰীক্ষা/i, 'examination'],
    [/\b(?:notifications?|notices?|announcements?)\b|জাননী|বিজ্ঞপ্তি/i, 'notification'],
    [/\bupdates?\b|শেহতীয়া/i, 'notification'],
  ];
  return topics.find(([pattern]) => pattern.test(question))?.[1] ?? null;
}

function canonicalizeMaterialText(value: string): string {
  const assameseDigits = '০১২৩৪৫৬৭৮৯';
  let normalized = [...value].map(char => {
    const digit = assameseDigits.indexOf(char);
    return digit >= 0 ? String(digit) : char;
  }).join('').toLowerCase();
  const replacements: Array<[RegExp, string]> = [
    [/(?:দ্বাদশ\s+শ্ৰেণী(?:ৰ)?|শ্ৰেণী(?:ৰ)?\s+দ্বাদশ)/g, 'class 12'],
    [/(?:একাদশ\s+শ্ৰেণী(?:ৰ)?|শ্ৰেণী(?:ৰ)?\s+একাদশ)/g, 'class 11'],
    [/(?:দশম\s+শ্ৰেণী(?:ৰ)?|শ্ৰেণী(?:ৰ)?\s+দশম)/g, 'class 10'],
    [/অসমৰ/g, 'অসম'],
    [/নামভৰ্তিৰ/g, 'নামভৰ্তি'],
    [/পৰীক্ষাৰ/g, 'পৰীক্ষা'],
    [/পঞ্জীয়নৰ/g, 'পঞ্জীয়ন'],
    [/বৃত্তিৰ/g, 'বৃত্তি'],
    [/পুনৰীক্ষণৰ/g, 'পুনৰীক্ষণ'],
    [/(?:দ্বিতীয়\s+বৰ্ষ|দ্বিতীয়\s+বৰ্ষ)/g, 'hs 2nd year'],
    [/(?:প্ৰথম\s+বৰ্ষ|প্রথম\s+বৰ্ষ)/g, 'hs 1st year'],
    [/(?:দ্বিতীয়\s+বিভাগ|দ্বিতীয়\s+বিভাগ|বিভাগ\s+দ্বিতীয়|বিভাগ\s+দ্বিতীয়)/g, 'division 2'],
    [/(?:প্ৰথম\s+বিভাগ|প্রথম\s+বিভাগ|বিভাগ\s+প্ৰথম|বিভাগ\s+প্রথম)/g, 'division 1'],
    [/জানুৱাৰী/g, 'january'],
    [/ফেব্ৰুৱাৰী/g, 'february'],
    [/মাৰ্চ/g, 'march'],
    [/এপ্ৰিল/g, 'april'],
    [/মে/g, 'may'],
    [/জুন/g, 'june'],
    [/জুলাই/g, 'july'],
    [/আগষ্ট/g, 'august'],
    [/ছেপ্টেম্বৰ/g, 'september'],
    [/অক্টোবৰ/g, 'october'],
    [/নৱেম্বৰ/g, 'november'],
    [/ডিচেম্বৰ/g, 'december'],
  ];
  for (const [pattern, replacement] of replacements) {
    normalized = normalized.replace(pattern, replacement);
  }
  return normalized;
}

function calendarDateKeys(value: string): string[] {
  const normalized = canonicalizeMaterialText(value);
  const monthNumbers: Record<string, number> = {
    jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3,
    apr: 4, april: 4, may: 5, jun: 6, june: 6, jul: 7, july: 7,
    aug: 8, august: 8, sep: 9, september: 9, oct: 10, october: 10,
    nov: 11, november: 11, dec: 12, december: 12,
  };
  const keys = new Set<string>();
  const add = (dayRaw: string, monthRaw: string, yearRaw: string) => {
    const day = Number(dayRaw);
    const month = Number(monthRaw);
    const year = Number(yearRaw.length === 2 ? `20${yearRaw}` : yearRaw);
    const date = new Date(Date.UTC(year, month - 1, day));
    if (
      date.getUTCFullYear() === year
      && date.getUTCMonth() === month - 1
      && date.getUTCDate() === day
    ) {
      keys.add(`${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`);
    }
  };
  const dayFirst =
    /\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+((?:19|20)\d{2})\b/gi;
  const monthFirst =
    /\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)((?:19|20)\d{2})\b/gi;
  const numeric = /\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)?\d{2})\b/g;
  for (const match of normalized.matchAll(dayFirst)) {
    add(match[1]!, String(monthNumbers[match[2]!.toLowerCase()]), match[3]!);
  }
  for (const match of normalized.matchAll(monthFirst)) {
    add(match[2]!, String(monthNumbers[match[1]!.toLowerCase()]), match[3]!);
  }
  for (const match of normalized.matchAll(numeric)) add(match[1]!, match[2]!, match[3]!);
  return [...keys];
}

function evidenceMatchesMaterialQualifiers(question: string, evidence: string): boolean {
  question = canonicalizeMaterialText(question);
  evidence = canonicalizeMaterialText(evidence);
  const checks: RegExp[] = [];
  if (/\b(?:class\s*(?:12|xii)|hs\s*(?:2nd|second)\s*-?\s*year)\b/i.test(question)) {
    checks.push(/\b(?:class\s*(?:12|xii)|hs\s*(?:2nd|second)\s*-?\s*year)\b/i);
  }
  if (/\b(?:class\s*(?:11|xi)|hs\s*(?:1st|first)\s*-?\s*year)\b/i.test(question)) {
    checks.push(/\b(?:class\s*(?:11|xi)|hs\s*(?:1st|first)\s*-?\s*year)\b/i);
  }
  if (/\bdivision\s*(?:i|1)\b/i.test(question)) checks.push(/\bdivision\s*(?:i|1)\b/i);
  if (/\bdivision\s*(?:ii|2)\b/i.test(question)) checks.push(/\bdivision\s*(?:ii|2)\b/i);
  if (!checks.every(pattern => pattern.test(evidence))) return false;

  const requestedSessions = question.match(/\b20\d{2}-\d{2,4}\b/g) ?? [];
  if (!requestedSessions.every(session =>
    evidence.toLowerCase().includes(session.toLowerCase()))) return false;

  const requestedYears = question.match(/\b20\d{2}\b/g) ?? [];
  if (!requestedYears.every(year => new RegExp(`\\b${year}\\b`).test(evidence))) return false;

  const requestedDates = calendarDateKeys(question);
  const evidenceDates = new Set(calendarDateKeys(evidence));
  return requestedDates.every(date => evidenceDates.has(date));
}

function evidenceMatchesSubstantiveClaim(
  question: string,
  evidence: string,
  topic: string,
): boolean {
  const stopWords = new Set([
    ...QUERY_STOP_WORDS,
    'a', 'after', 'allow', 'allowed', 'allows', 'an', 'and', 'announced',
    'announcement', 'are', 'as', 'assam',
    'asseb', 'ahsec', 'academic', 'admission', 'admissions', 'board', 'by',
    'calendar', 'can', 'class', 'contain', 'contains', 'could', 'council',
    'covered', 'current', 'currently', 'date', 'dates', 'deadline', 'did', 'division',
    'do', 'does', 'education', 'exam', 'exams', 'examination', 'examinations',
    'for', 'from', 'had',
    'has', 'have', 'higher', 'how', 'if', 'in', 'include', 'included', 'includes',
    'including', 'is', 'it', 'me', 'needed', 'of', 'on', 'or', 'part',
    'please', 'registration', 'registrations', 'release', 'released', 'result',
    'results', 'routine',
    'schedule', 'scholarship', 'scholarships', 'school', 'seba', 'secondary',
    'session', 'should', 'show', 'state', 'status', 'syllabus', 'tell', 'the',
    'this', 'timetable', 'to', 'under', 'update', 'updates', 'was', 'were',
    'will', 'would', 'year', 'hs', 'long', 'soon', 'start', 'starts', 'time',
    'timing', 'january', 'february', 'march', 'april',
    'may', 'june', 'july', 'august', 'september', 'october', 'november',
    'december',
    'অসম', 'বৰ্তমান', 'ব’ৰ্ড', 'বৰ্ড', 'বৰ্ডৰ', 'বোর্ড', 'বোর্ডৰ', 'ৰ্ডৰ',
    'পৰিষদ', 'শিক্ষা', 'শেহতীয়া', 'শ্ৰেণী', 'দেখুৱাওক', 'নে', 'নেকি', 'কি',
    'কেতিয়া', 'কিমান', 'কেইটা', 'বজাত', 'আৰম্ভ', 'হয়', 'সময়', 'সময়',
    'তাৰিখ', 'সময়সীমা', 'পাঠ্যক্ৰম', 'পাঠ্যক্ৰমত', 'পঞ্জিকা', 'সময়সূচী',
    'ৰুটিন', 'পৰীক্ষা', 'ফলাফল', 'নামভৰ্তি', 'পঞ্জীয়ন', 'বৃত্তি',
    'পুনৰীক্ষণ', 'জাননী', 'স্থিতি', 'আপডেট',
  ]);
  for (const token of canonicalizeMaterialText(topic).match(/[\p{L}\p{N}]+/gu) ?? []) {
    stopWords.add(token);
  }
  const month =
    '(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)';
  const framingStripped = canonicalizeMaterialText(question)
    .replace(new RegExp(`\\b\\d{1,2}(?:st|nd|rd|th)?\\s+${month}\\s+(?:19|20)\\d{2}\\b`, 'gi'), ' ')
    .replace(new RegExp(`\\b${month}\\s+\\d{1,2}(?:st|nd|rd|th)?(?:,\\s*|\\s+)(?:19|20)\\d{2}\\b`, 'gi'), ' ')
    .replace(/\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)?\d{2}\b/g, ' ')
    .replace(/\bclass\s*(?:10|11|12|x|xi|xii)\b/gi, ' ')
    .replace(/\bhs\s*(?:1st|2nd|first|second)[-\s]*year\b/gi, ' ')
    .replace(/\bdivision\s*(?:i|ii|1|2)\b/gi, ' ')
    .replace(/\b20\d{2}(?:-\d{2,4})?\b/g, ' ');
  const claimTokens = (framingStripped.match(/[\p{L}\p{M}\p{N}]+/gu) ?? [])
    .filter(token => token.length >= 3)
    .filter(token => !/^\d+$/.test(token))
    .filter(token => !stopWords.has(token));
  if (claimTokens.length === 0) return true;
  const evidenceTokens = new Set(
    canonicalizeMaterialText(evidence).match(/[\p{L}\p{M}\p{N}]+/gu) ?? [],
  );
  return claimTokens.every(token => evidenceTokens.has(token));
}

type TemporalEvidenceDimension = 'date' | 'clock' | 'duration';

function requestedTemporalDimension(question: string): TemporalEvidenceDimension | null {
  if (/\b(?:at\s+)?what\s+time\b|\bstart\s+time\b|\btiming\b|কেইটা\s+বজাত|কিমান\s+বজাত|কি\s+সময়ত/i
    .test(question)) {
    return 'clock';
  }
  if (/\bhow\s+(?:soon|long)\b|কিমান\s+(?:সোনকালে|সময়|দিন|ঘণ্টা)/i.test(question)) {
    return 'duration';
  }
  if (/\b(?:dates?|when|deadline)\b|তাৰিখ|কেতিয়া|সময়সীমা/i.test(question)) {
    return 'date';
  }
  return null;
}

function boundTemporalEvidenceUnits(
  passage: string,
  topic: string,
  dimension: TemporalEvidenceDimension,
): string[] {
  const deferral =
    /\b(?:will be|to be)\s+(?:notified|announced|published|released)\s+(?:later|separately)|\bnot\s+yet\s+(?:notified|announced|published|released)\b/i;
  const clockTime = /\b\d{1,2}:\d{2}\s*(?:am|pm)?\b|\b\d{1,2}\s*(?:am|pm)\b/i;
  const duration = /\b\d+\s+(?:days?|weeks?|months?|hours?)\b/i;
  return passage
    .split(/(?<=[.!?;])\s+|\n+/)
    .filter(clause =>
      clause.toLowerCase().includes(topic.toLowerCase())
      && !deferral.test(clause)
      && (
        (dimension === 'date' && calendarDateKeys(clause).length > 0)
        || (dimension === 'clock' && clockTime.test(clause))
        || (dimension === 'duration' && duration.test(clause))
      ));
}

function boundedEvidenceWindows(value: string, topic: string): string[] {
  if (value.length <= WEB_SNIPPET_CHAR_CAP) return [value];
  const starts = new Set<number>([0, value.length - WEB_SNIPPET_CHAR_CAP]);
  const lowerValue = value.toLowerCase();
  const lowerTopic = topic.toLowerCase();
  let topicIndex = lowerValue.indexOf(lowerTopic);
  while (topicIndex >= 0) {
    for (const offset of [0, 100, 250, WEB_SNIPPET_CHAR_CAP - lowerTopic.length]) {
      starts.add(Math.max(0, Math.min(value.length - WEB_SNIPPET_CHAR_CAP, topicIndex - offset)));
    }
    topicIndex = lowerValue.indexOf(lowerTopic, topicIndex + lowerTopic.length);
  }
  return [...starts]
    .sort((a, b) => a - b)
    .map(start => value.slice(start, start + WEB_SNIPPET_CHAR_CAP));
}

function evidenceIsCurrent(
  passage: string,
  question: string,
): boolean {
  const requiresFreshness = hasExplicitFreshnessIntent(question)
    || isCurrentOfficialBoardQuestion(question);
  if (!requiresFreshness) return true;

  const currentYear = new Date().getUTCFullYear();
  const years = canonicalizeMaterialText(passage)
    .match(/\b20\d{2}\b/g)
    ?.map(Number) ?? [];
  return years.length > 0
    && years.every(year => year >= currentYear && year <= currentYear + 2);
}

function parseOfficialSearchPages(
  payload: OfficialPageRecord[],
  topic: string,
  question: string,
): WebSearchResult[] {
  const temporalDimension = requestedTemporalDimension(question);
  if (CONDITIONAL_TIME_SIGNAL.test(question) && !temporalDimension) return [];
  const results = payload.flatMap((page): WebSearchResult[] => {
    const title = textOnly(decodeXml(page.title?.rendered ?? ''));
    const contentUnits = officialHtmlEvidenceUnits(decodeXml(page.content?.rendered ?? ''));
    const content = contentUnits.join(' ');
    const combined = `${title}. ${content}`;
    const lowerCombined = combined.toLowerCase();
    const lowerTopic = topic.toLowerCase();
    if (!lowerCombined.includes(lowerTopic) || !page.link) return [];
    let url: URL;
    try {
      url = new URL(page.link);
    } catch {
      return [];
    }
    if (url.protocol !== 'https:' || url.hostname !== 'ahsec.assam.gov.in') return [];
    const passages: string[] = [];
    let searchFrom = 0;
    while (searchFrom < lowerCombined.length) {
      const topicIndex = lowerCombined.indexOf(lowerTopic, searchFrom);
      if (topicIndex < 0) break;
      const start = Math.max(0, topicIndex - 100);
      passages.push(combined.slice(start, start + WEB_SNIPPET_CHAR_CAP));
      searchFrom = topicIndex + lowerTopic.length;
    }
    const temporalEvidenceUnit = temporalDimension
      ? contentUnits
        .flatMap(unit => boundTemporalEvidenceUnits(unit, topic, temporalDimension))
        .flatMap(unit => boundedEvidenceWindows(unit, topic))
        .find(window =>
          boundTemporalEvidenceUnits(window, topic, temporalDimension).length > 0
          && evidenceMatchesMaterialQualifiers(question, window)
          && evidenceMatchesSubstantiveClaim(question, window, topic)
          && evidenceIsCurrent(window, question))
      : undefined;
    const snippet = temporalDimension
      ? temporalEvidenceUnit?.slice(0, WEB_SNIPPET_CHAR_CAP)
      : passages.find(passage =>
        evidenceMatchesMaterialQualifiers(question, passage)
        && evidenceMatchesSubstantiveClaim(question, passage, topic)
        && evidenceIsCurrent(passage, question));
    if (!snippet) return [];
    return [{
      title: title.slice(0, 180),
      url: url.toString(),
      snippet,
      source: 'web_search',
    }];
  });
  return dedupeWebResults(results);
}

function boundedCrossrefResult(work: CrossrefWork): WebSearchResult | null {
  const title = textOnly(work.title?.[0] ?? '');
  const container = textOnly(work['container-title']?.[0] ?? '');
  const year = work.published?.['date-parts']?.[0]?.[0];
  const fallbackSnippet = [
    container ? `Published in ${container}.` : '',
    year ? `Publication year: ${year}.` : '',
    `Scholarly source titled "${title}".`,
  ].filter(Boolean).join(' ');
  const snippet = textOnly(work.abstract ?? fallbackSnippet).slice(0, WEB_SNIPPET_CHAR_CAP);
  const rawUrl = (work.URL ?? '').trim();
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || !['doi.org', 'dx.doi.org'].includes(url.hostname)) return null;
  if (!title || snippet.length < 20) return null;
  return {
    title: title.slice(0, 180),
    url: url.toString(),
    snippet,
    source: 'web_search',
  };
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new Error('web-search-timeout'));
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new Error('web-search-timeout'));
    signal.addEventListener('abort', onAbort, { once: true });
    promise.then(
      value => {
        signal.removeEventListener('abort', onAbort);
        resolve(value);
      },
      error => {
        signal.removeEventListener('abort', onAbort);
        reject(error);
      },
    );
  });
}

async function fetchCrossrefFallback(
  fetcher: typeof fetch,
  query: string,
  signal: AbortSignal,
): Promise<ProviderSearchResult> {
  const endpoint = new URL('https://api.crossref.org/works');
  endpoint.searchParams.set('query', query);
  endpoint.searchParams.set('rows', String(WEB_SEARCH_LIMIT));
  endpoint.searchParams.set('select', 'title,URL,abstract,container-title,published');
  endpoint.searchParams.set('sort', 'relevance');
  const response = await fetcher(endpoint.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      'User-Agent': 'SyrabitAI/1.0 (https://syrabit.ai/about)',
    },
    signal,
  });
  if (!response.ok) return { results: [], status: 'error' };
  const payload = await response.json<CrossrefResponse>();
  const results = dedupeWebResults((payload.message?.items ?? [])
    .map(work => boundedCrossrefResult(work))
    .filter((item): item is WebSearchResult => item !== null));
  return { results, status: results.length > 0 ? 'ok' : 'empty' };
}

/**
 * Bounded, no-secret web lookup. The official ASSEB page supplies current
 * non-scholarly board information; Crossref remains the educational fallback.
 * Both share one
 * strict deadline, and all failures remain non-fatal to textbook RAG.
 */
export async function searchWeb(
  question: string,
  lang: 'en' | 'as',
  options: SearchOptions = {},
): Promise<WebSearchResponse> {
  const started = Date.now();
  const timeoutMs = Math.max(100, Math.min(options.timeoutMs ?? WEB_SEARCH_TIMEOUT_MS, 1_500));
  const fetcher = options.fetcher ?? fetch;
  const query = buildWebSearchQuery(question, lang);
  const officialBoardIntent = OFFICIAL_BOARD_INTENT.test(question);
  if (!officialBoardIntent) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort('web-search-timeout'), timeoutMs);
    try {
      const result = await fetchCrossrefFallback(fetcher, query, controller.signal);
      return {
        results: result.results,
        status: result.status,
        durationMs: Date.now() - started,
      };
    } catch {
      return {
        results: [],
        status: controller.signal.aborted ? 'timeout' : 'error',
        durationMs: Date.now() - started,
      };
    } finally {
      clearTimeout(timer);
    }
  }
  const canonicalQuestion = canonicalizeMaterialText(question);
  const unsupportedDivisionOneIntent =
    /\b(?:seba|hslc)\b|\bclass\s*(?:10|x)\b|\bdivision\s*(?:i|1)\b/i
      .test(canonicalQuestion);
  if (unsupportedDivisionOneIntent) {
    return { results: [], status: 'empty', durationMs: Date.now() - started };
  }
  const topic = officialSearchTopic(question);
  const statusIntent = isInstitutionStatusIntent(question);
  if (!topic && !statusIntent) {
    return { results: [], status: 'empty', durationMs: Date.now() - started };
  }
  const endpoint = topic
    ? new URL('https://ahsec.assam.gov.in/index.php/wp-json/wp/v2/pages')
    : new URL('https://asseb.in/');
  if (topic) {
    endpoint.searchParams.set('search', topic);
    endpoint.searchParams.set('per_page', String(WEB_SEARCH_LIMIT));
    endpoint.searchParams.set('orderby', 'modified');
    endpoint.searchParams.set('order', 'desc');
    endpoint.searchParams.set('_fields', 'link,title,content,modified');
  }
  const cacheKey = topic
    ? `${OFFICIAL_PAGE_CACHE_KEY}:division-ii:${topic}`
    : `${OFFICIAL_PAGE_CACHE_KEY}:status`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort('web-search-timeout'), timeoutMs);
  try {
    if (options.cache) {
      const cachedPayload = await abortable(
        options.cache.get(cacheKey).catch(() => null),
        controller.signal,
      );
      if (cachedPayload) {
        try {
          const parsed = topic ? JSON.parse(cachedPayload) as unknown : cachedPayload;
          const results = topic
            ? Array.isArray(parsed)
              ? parseOfficialSearchPages(parsed as OfficialPageRecord[], topic, question)
              : []
            : parseOfficialStatusPage(cachedPayload, endpoint.toString());
          if (results.length > 0) {
            return { results, status: 'ok', durationMs: Date.now() - started };
          }
        } catch {
          // Optional cache corruption must not fail the chat request. Continue to
          // the same bounded, verified official origin request.
        }
      }
    }
    const response = await fetcher(endpoint.toString(), {
      method: 'GET',
      headers: {
        Accept: topic ? 'application/json' : 'text/html',
        'User-Agent': 'SyrabitAI/1.0 (https://syrabit.ai/about)',
      },
      signal: controller.signal,
    });
    if (!response.ok) {
      return { results: [], status: 'error', durationMs: Date.now() - started };
    }
    const rawPayload = await response.text();
    const results = topic
      ? parseOfficialSearchPages(JSON.parse(rawPayload) as OfficialPageRecord[], topic, question)
      : parseOfficialStatusPage(rawPayload, endpoint.toString());
    if (results.length > 0 && options.cache) {
      void options.cache.put(cacheKey, rawPayload, {
        expirationTtl: OFFICIAL_PAGE_CACHE_TTL_SECONDS,
      }).catch(() => {});
    }
    return {
      results,
      status: results.length > 0 ? 'ok' : 'empty',
      durationMs: Date.now() - started,
    };
  } catch (error) {
    const status = controller.signal.aborted ? 'timeout' : 'error';
    return { results: [], status, durationMs: Date.now() - started };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Starts every eligible retrieval branch before awaiting any of them.
 * Exported so the concurrency contract is deterministic in tests.
 */
export function startRetrievalFanout<TEmbedding>(factories: {
  embed: () => Promise<TEmbedding>;
  history: () => Promise<string>;
  web: () => Promise<WebSearchResponse>;
}): Promise<[
  PromiseSettledResult<TEmbedding>,
  PromiseSettledResult<string>,
  PromiseSettledResult<WebSearchResponse>,
]> {
  return Promise.allSettled([
    factories.embed(),
    factories.history(),
    factories.web(),
  ]);
}

export function skippedWebSearch(): WebSearchResponse {
  return { results: [], status: 'skipped', durationMs: 0 };
}