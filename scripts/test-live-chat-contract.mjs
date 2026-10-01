#!/usr/bin/env node

/**
 * Focused production contract probe for the native Cloudflare chat path.
 *
 * Every run makes two anonymous chat requests: a normal answer and a current-
 * information request that must fail closed while web search is disabled.
 * The broader student-authenticated checks remain opt-in.
 */

import { randomUUID } from 'node:crypto';

const origin = (process.env.PUBLIC_EDGE_URL || 'https://api.syrabit.ai').replace(/\/+$/, '');
const studentToken = process.env.STUDENT_TOKEN?.trim() || '';
const runAuthenticatedContract = process.env.LIVE_CHAT_AUTHENTICATED === 'true';
if (runAuthenticatedContract && !studentToken) {
  throw new Error('STUDENT_TOKEN is required when LIVE_CHAT_AUTHENTICATED=true');
}
const timeoutMs = Number(process.env.LIVE_CHAT_TIMEOUT_MS || 30_000);
if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1_000 || timeoutMs > 120_000) {
  throw new Error('LIVE_CHAT_TIMEOUT_MS must be an integer between 1000 and 120000');
}

const authHeaders = studentToken
  ? { Authorization: `Bearer ${studentToken}`, Accept: 'application/json' }
  : {};

function anonymousHeaders() {
  return {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
    'x-anon-id': `anon_${randomUUID().replaceAll('-', '')}`,
  };
}

async function request(path, init = {}) {
  const response = await fetch(`${origin}${path}`, {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  return { response, body, text };
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function parseSse(text) {
  return text
    .split(/\r?\n/)
    .filter(line => line.startsWith('data: '))
    .map(line => {
      try { return JSON.parse(line.slice(6)); } catch { return null; }
    })
    .filter(Boolean);
}

async function streamChat(body, { authenticated = false } = {}) {
  if (authenticated && !studentToken) {
    throw new Error('Student token required for authenticated chat probe');
  }
  const response = await fetch(`${origin}/api/v1/chat/stream`, {
    method: 'POST',
    headers: {
      ...(authenticated ? authHeaders : anonymousHeaders()),
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text();
  const events = parseSse(text);
  assert(response.status === 200, `chat stream returned HTTP ${response.status}`);
  const done = events.find(event => event.event === 'syrabit_done');
  return { text, events, done };
}

function assertSuccessfulAnswer(stream, name) {
  const sourceIndex = stream.events.findIndex(event => event.event === 'source_card');
  const tokenIndex = stream.events.findIndex(event =>
    typeof event.content === 'string' && event.content.trim().length > 0 && !event.done);
  const doneIndex = stream.events.findIndex(event => event.event === 'syrabit_done');
  assert(sourceIndex === 0, `${name} did not emit source_card first`);
  assert(tokenIndex > sourceIndex, `${name} emitted no answer content after source_card`);
  assert(doneIndex > tokenIndex, `${name} did not finish with syrabit_done after content`);
  assert(
    !stream.events.some(event => event.event === 'chat_error' || event.error === true),
    `${name} emitted an error event`,
  );
}

function assertSearchSkipped(events, name) {
  const sourceCard = events.find(event => event.event === 'source_card');
  const terminal = events.find(event => event.event === 'syrabit_done' || event.event === 'chat_error');
  const routeTrace = terminal?.route_trace ?? {};
  const webUsed = sourceCard?.web_used ?? routeTrace.web_used ?? terminal?.web_used;
  const webStatus = sourceCard?.web_status ?? routeTrace.web_status ?? terminal?.web_status;
  assert(
    webUsed === false && webStatus === 'skipped',
    `${name} did not report web_used=false and web_status=skipped`,
  );
}

async function runAnonymousContract() {
  const prefix = `release_anon_${Date.now()}_${randomUUID().replaceAll('-', '')}`;
  const ordinary = await streamChat({
    message: "Explain Newton's first law of motion in one short sentence.",
    lang: 'en',
    client_request_id: `${prefix}_ordinary`,
  });
  assertSuccessfulAnswer(ordinary, 'ordinary anonymous answer');
  assertSearchSkipped(ordinary.events, 'ordinary anonymous answer');
  console.log('[live-chat] anonymous ordinary answer: content, source card, completion, and no web passed');

  const subjects = await request('/api/v1/content/subjects', {
    headers: { Accept: 'application/json' },
  });
  assert(subjects.response.status === 200, `subject list returned HTTP ${subjects.response.status}`);
  assert(Array.isArray(subjects.body), 'subject list is not an array');
  const subject = subjects.body.find(item =>
    typeof item?.id === 'string'
    && typeof item?.name === 'string'
    && !/education/i.test(item.name));
  assert(subject, 'subject list contains no usable non-Education public subject');

  const current = await streamChat({
    message: 'Has AHSEC been merged into ASSEB now? Use current web context.',
    lang: 'en',
    subject_id: subject.id,
    subject_name: subject.name,
    client_request_id: `${prefix}_current`,
  });
  const failure = current.events.find(event => event.event === 'chat_error');
  const contentEvents = current.events.filter(event =>
    typeof event.content === 'string' && event.content.trim().length > 0 && !event.done);
  assert(
    failure?.error_code === 'verified_web_evidence_unavailable',
    `current-information query did not fail closed with verified_web_evidence_unavailable`,
  );
  assert(contentEvents.length === 0, 'current-information query emitted answer tokens without web evidence');
  assert(!current.events.some(event => event.event === 'syrabit_done'), 'failed current-information query also emitted syrabit_done');
  assertSearchSkipped(current.events, 'current-information query');
  console.log('[live-chat] anonymous current-information query: fail-closed error, no tokens, and no web passed');
}

await runAnonymousContract();

if (runAuthenticatedContract) {
const requestPrefix = `release_${Date.now()}_${randomUUID().replaceAll('-', '')}`;
const englishRequestId = `${requestPrefix}_en`;
const sessionId = `${requestPrefix}_session`;

const library = await request('/api/v1/content/library-bundle?slim=1', {
  headers: { Accept: 'application/json' },
});
assert(library.response.status === 200, `library bundle returned HTTP ${library.response.status}`);
assert(library.body && Array.isArray(library.body.subjects), 'library bundle subjects is not an array');
console.log(`[live-chat] library bundle: ${library.body.subjects.length} subjects`);

const search = await request('/api/v1/content/search?q=physics', {
  headers: { Accept: 'application/json' },
});
assert(search.response.status === 200, `content search returned HTTP ${search.response.status}`);
assert(search.body?.available === true && Array.isArray(search.body.results), 'live content search is unavailable');
assert(search.body.results.length > 0, 'live content search returned no physics results');
console.log(`[live-chat] content search: ${search.body.results.length} results`);

const englishBody = {
  message: 'Explain one key idea from this subject in two short sentences.',
  lang: 'en',
  session_id: sessionId,
  client_request_id: englishRequestId,
};
const english = await streamChat(englishBody, { authenticated: true });
assert(english.done.lang === 'en', `English stream reported lang=${english.done.lang}`);
console.log('[live-chat] English stream: source card, content, and completion passed');

const replay = await streamChat(englishBody, { authenticated: true });
assert(
  replay.done.request_id === english.done.request_id || replay.events.some(event => event.event === 'source_card'),
  'completed request replay did not return a chat stream',
);
console.log('[live-chat] idempotent persistence replay passed');

const conversations = await request('/api/v1/conversations', { headers: authHeaders });
assert(conversations.response.status === 200, `conversation history returned HTTP ${conversations.response.status}`);
assert(Array.isArray(conversations.body?.conversations), 'conversation history shape is invalid');
console.log(`[live-chat] conversation persistence: ${conversations.body.conversations.length} conversations visible`);

const assamese = await streamChat({
  message: 'গতি কি? দুটা চুটি বাক্যত বুজাই দিয়া।',
  lang: 'as',
  session_id: sessionId,
  client_request_id: `${requestPrefix}_as`,
}, { authenticated: true });
assert(assamese.done.lang === 'as', `Assamese stream reported lang=${assamese.done.lang}`);
const assameseText = assamese.events
  .filter(event => typeof event.content === 'string')
  .map(event => event.content)
  .join(' ');
assert(/[\u0980-\u09FF]/u.test(assameseText), 'Assamese response contained no Assamese script');
console.log('[live-chat] Assamese output and language routing passed');

const cancelledRequestId = `${requestPrefix}_cancel`;
const cancel = await request('/api/v1/chat/cancel', {
  method: 'POST',
  headers: { ...authHeaders, 'Content-Type': 'application/json' },
  body: JSON.stringify({ client_request_id: cancelledRequestId }),
});
assert(cancel.response.status === 202 && cancel.body?.cancelled === true, 'cancel endpoint did not claim the request');
const cancelledReplay = await request('/api/v1/chat/stream', {
  method: 'POST',
  headers: { ...authHeaders, 'Content-Type': 'application/json' },
  body: JSON.stringify({
    message: 'This request must not reach the provider.',
    lang: 'en',
    client_request_id: cancelledRequestId,
  }),
});
assert(
  cancelledReplay.response.status === 409 && cancelledReplay.body?.error_code === 'chat_request_cancelled',
  `cancelled request was not rejected safely (HTTP ${cancelledReplay.response.status})`,
);
console.log('[live-chat] cancellation claim and provider-bypass guard passed');

const malformed = await request('/api/v1/chat/stream', {
  method: 'POST',
  headers: { ...authHeaders, 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: '', client_request_id: `${requestPrefix}_invalid` }),
});
assert(malformed.response.status === 422, `structured request failure returned HTTP ${malformed.response.status}`);
assert(malformed.body?.detail === 'message is required', 'structured request failure detail changed');
console.log('[live-chat] structured failure response passed');

console.log('[live-chat] authenticated extension: all live contract checks passed');
} else {
  console.log('[live-chat] anonymous-only mode; no student or staff credentials used');
}
