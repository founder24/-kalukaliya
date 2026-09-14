#!/usr/bin/env node

/**
 * Focused production contract probe for the native Cloudflare chat path.
 *
 * This is intentionally opt-in: it uses a real student token and creates
 * short-lived chat request claims in production. It must only run from the
 * guarded release workflow.
 */

import { randomUUID } from 'node:crypto';

const origin = (process.env.PUBLIC_EDGE_URL || 'https://api.syrabit.ai').replace(/\/+$/, '');
const token = process.env.STUDENT_TOKEN?.trim();
if (!token) throw new Error('STUDENT_TOKEN is required for the live chat contract');

const authHeaders = {
  Authorization: `Bearer ${token}`,
  Accept: 'application/json',
};

async function request(path, init = {}) {
  const response = await fetch(`${origin}${path}`, {
    ...init,
    signal: AbortSignal.timeout(Number(process.env.LIVE_CHAT_TIMEOUT_MS || 60_000)),
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

async function streamChat(body) {
  const response = await fetch(`${origin}/api/v1/chat/stream`, {
    method: 'POST',
    headers: {
      ...authHeaders,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(Number(process.env.LIVE_CHAT_TIMEOUT_MS || 60_000)),
  });
  const text = await response.text();
  const events = parseSse(text);
  assert(response.status === 200, `chat stream returned HTTP ${response.status}: ${text.slice(0, 300)}`);
  assert(events.some(event => event.event === 'source_card'), 'chat stream did not emit source_card');
  assert(events.some(event => typeof event.content === 'string' && event.content.length > 0), 'chat stream emitted no content');
  const done = events.find(event => event.event === 'syrabit_done');
  assert(done, 'chat stream did not emit syrabit_done');
  assert(!events.some(event => event.error === true), 'chat stream emitted an error event');
  return { text, events, done };
}

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
const english = await streamChat(englishBody);
assert(english.done.lang === 'en', `English stream reported lang=${english.done.lang}`);
console.log('[live-chat] English stream: source card, content, and completion passed');

const replay = await streamChat(englishBody);
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
});
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

console.log('[live-chat] all live contract checks passed');