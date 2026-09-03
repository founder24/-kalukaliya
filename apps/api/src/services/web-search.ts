const WEB_SEARCH_LIMIT = 4;
export const WEB_SEARCH_TIMEOUT_MS = 1_100;
const WEB_SNIPPET_CHAR_CAP = 500;

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

interface SearchOptions {
  timeoutMs?: number;
  fetcher?: typeof fetch;
}

const FRESHNESS_INTENT =
  /\b(latest|current|currently|today|recent|recently|new|news|updated?|change[ds]?|this (?:week|month|year)|20(?:2[6-9]|[3-9]\d))\b/i;
const WEB_INTENT =
  /\b(search (?:the )?web|web search|look online|online sources?|on the internet|news sources?)\b/i;

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
  return FRESHNESS_INTENT.test(question) || WEB_INTENT.test(question);
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

function boundedResult(work: CrossrefWork): WebSearchResult | null {
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

/**
 * Bounded, no-secret educational web lookup using Crossref's public JSON API.
 * Failures and timeouts are deliberately non-fatal so textbook RAG can proceed.
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
  const endpoint = new URL('https://api.crossref.org/works');
  endpoint.searchParams.set('query', query);
  endpoint.searchParams.set('rows', String(WEB_SEARCH_LIMIT));
  endpoint.searchParams.set('select', 'title,URL,abstract,container-title,published');
  endpoint.searchParams.set('sort', 'relevance');

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort('web-search-timeout'), timeoutMs);
  try {
    const response = await fetcher(endpoint.toString(), {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        'User-Agent': 'SyrabitAI/1.0 (https://syrabit.ai/about)',
      },
      signal: controller.signal,
    });
    if (!response.ok) {
      return { results: [], status: 'error', durationMs: Date.now() - started };
    }
    const payload = await response.json<CrossrefResponse>();
    const results = (payload.message?.items ?? [])
      .map(work => boundedResult(work))
      .filter((item): item is WebSearchResult => item !== null)
      .slice(0, WEB_SEARCH_LIMIT);
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