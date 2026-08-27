/**
 * D1-backed saved conversation history.
 *
 * Messages live in the normalized `chats` table. This router groups them by
 * session_id and keeps rename/star/archive UI state in conversation_metadata.
 * Every read and mutation scopes by user_id before using a session ID.
 */

import { Hono, type Context } from 'hono';
import { extractBearer, isSessionValid, verifyToken } from '../middleware/auth';
import { anonUserId } from '../services/anonymous';
import type { Env } from '../types';

export const conversationsRouter = new Hono<{ Bindings: Env }>();

type ConversationRow = {
  session_id: string;
  message_count: number;
  created_at: number | null;
  updated_at: number | null;
  preview: string | null;
  title: string | null;
  starred: number | null;
  archived: number | null;
};

function toIso(timestamp: number | null): string | null {
  return timestamp == null ? null : new Date(timestamp * 1000).toISOString();
}

async function requireUser(c: Context<{ Bindings: Env }>): Promise<{ id: string; error?: Response }> {
  const token = extractBearer(c.req.header('Authorization') ?? null);
  if (!token) return { id: '', error: c.json({ detail: 'Not authenticated' }, 401) as Response };
  const payload = await verifyToken(token, c.env.JWT_SECRET);
  if (!payload || payload.type !== 'access' || !payload.sub) {
    return { id: '', error: c.json({ detail: 'Invalid or expired token' }, 401) as Response };
  }
  if (!(await isSessionValid(c.env.DB, payload.sub, payload.iat))) {
    return { id: '', error: c.json({ detail: 'Session expired after password change. Please sign in again.' }, 401) as Response };
  }
  return { id: payload.sub };
}

function pageParams(c: Context<{ Bindings: Env }>, maxLimit: number): { skip: number; limit: number } {
  const skip = Math.max(0, Number.parseInt(c.req.query('skip') ?? '0', 10) || 0);
  const limit = Math.min(maxLimit, Math.max(1, Number.parseInt(c.req.query('limit') ?? '20', 10) || 20));
  return { skip, limit };
}

async function listConversations(
  c: Context<{ Bindings: Env }>,
  userId: string,
  maxLimit: number,
): Promise<Response> {
  const { skip, limit } = pageParams(c, maxLimit);
  const rows = await c.env.DB.prepare(`
    SELECT c.session_id,
           COUNT(*) AS message_count,
           MIN(c.created_at) AS created_at,
           COALESCE(meta.updated_at, MAX(c.created_at)) AS updated_at,
           (
             SELECT m.content FROM chats m
             WHERE m.user_id = c.user_id AND m.session_id = c.session_id
             ORDER BY m.created_at ASC LIMIT 1
           ) AS preview,
           meta.title AS title,
           meta.starred AS starred,
           meta.archived AS archived
    FROM chats c
    LEFT JOIN conversation_metadata meta
      ON meta.user_id = c.user_id AND meta.session_id = c.session_id
    WHERE c.user_id = ?
    GROUP BY c.session_id
    ORDER BY COALESCE(meta.updated_at, MAX(c.created_at)) DESC
    LIMIT ? OFFSET ?
  `).bind(userId, limit, skip).all<ConversationRow>();
  const totalRow = await c.env.DB.prepare(
    'SELECT COUNT(DISTINCT session_id) AS total FROM chats WHERE user_id = ?',
  ).bind(userId).first<{ total: number }>();
  const total = totalRow?.total ?? 0;

  return c.json({
    conversations: (rows.results ?? []).map(row => ({
      // D1's stable conversation identity is the chat session id. The
      // frontend passes this same ID into detail/update/delete routes.
      id: row.session_id,
      session_id: row.session_id,
      title: row.title ?? row.preview?.slice(0, 80) ?? 'New conversation',
      preview: row.preview ?? '',
      message_count: row.message_count,
      starred: Boolean(row.starred),
      archived: Boolean(row.archived),
      created_at: toIso(row.created_at),
      updated_at: toIso(row.updated_at),
    })),
    pagination: { skip, limit, total, has_more: skip + limit < total },
  });
}

async function conversationDetail(
  c: Context<{ Bindings: Env }>,
  userId: string,
  sessionId: string,
): Promise<Response> {
  const messages = await c.env.DB.prepare(`
    SELECT role, content, lang, subject_id, chapter_id, metadata, created_at
    FROM chats WHERE user_id = ? AND session_id = ?
    ORDER BY created_at ASC
  `).bind(userId, sessionId).all<{
    role: string; content: string; lang: string | null; subject_id: string | null;
    chapter_id: string | null; metadata: string | null; created_at: number | null;
  }>();
  if (!(messages.results ?? []).length) return c.json({ detail: 'Conversation not found' }, 404);

  const meta = await c.env.DB.prepare(`
    SELECT title, starred, archived, updated_at FROM conversation_metadata
    WHERE user_id = ? AND session_id = ?
  `).bind(userId, sessionId).first<{
    title: string | null; starred: number; archived: number; updated_at: number | null;
  }>();
  const first = messages.results![0]!;
  const last = messages.results![messages.results!.length - 1]!;
  return c.json({
    id: sessionId,
    session_id: sessionId,
    title: meta?.title ?? first.content.slice(0, 80) ?? 'New conversation',
    starred: Boolean(meta?.starred),
    archived: Boolean(meta?.archived),
    message_count: messages.results!.length,
    created_at: toIso(first.created_at),
    updated_at: toIso(meta?.updated_at ?? last.created_at),
    messages: messages.results!.map(message => ({
      role: message.role,
      content: message.content,
      lang: message.lang ?? 'en',
      subject_id: message.subject_id,
      chapter_id: message.chapter_id,
      created_at: toIso(message.created_at),
    })),
  });
}

async function deleteConversation(c: Context<{ Bindings: Env }>, userId: string, sessionId: string): Promise<Response> {
  const exists = await c.env.DB.prepare(
    'SELECT 1 AS found FROM chats WHERE user_id = ? AND session_id = ? LIMIT 1',
  ).bind(userId, sessionId).first();
  if (!exists) return c.json({ detail: 'Conversation not found' }, 404);
  await c.env.DB.batch([
    c.env.DB.prepare('DELETE FROM chats WHERE user_id = ? AND session_id = ?').bind(userId, sessionId),
    c.env.DB.prepare('DELETE FROM conversation_metadata WHERE user_id = ? AND session_id = ?').bind(userId, sessionId),
  ]);
  return c.json({ message: 'Conversation deleted' });
}

conversationsRouter.get('/', async c => {
  const { id, error } = await requireUser(c);
  return error ?? listConversations(c, id, 100);
});

conversationsRouter.get('/anon', c => listConversations(c, anonUserId(c.req.raw), 5));

conversationsRouter.get('/anon/:sessionId', c =>
  conversationDetail(c, anonUserId(c.req.raw), c.req.param('sessionId')));

conversationsRouter.delete('/anon/:sessionId', c =>
  deleteConversation(c, anonUserId(c.req.raw), c.req.param('sessionId')));

conversationsRouter.get('/:sessionId', async c => {
  const { id, error } = await requireUser(c);
  return error ?? conversationDetail(c, id, c.req.param('sessionId'));
});

conversationsRouter.delete('/:sessionId', async c => {
  const { id, error } = await requireUser(c);
  return error ?? deleteConversation(c, id, c.req.param('sessionId'));
});

conversationsRouter.patch('/:sessionId', async c => {
  const { id, error } = await requireUser(c);
  if (error) return error;
  const sessionId = c.req.param('sessionId');
  const exists = await c.env.DB.prepare(
    'SELECT 1 AS found FROM chats WHERE user_id = ? AND session_id = ? LIMIT 1',
  ).bind(id, sessionId).first();
  if (!exists) return c.json({ detail: 'Conversation not found' }, 404);

  let body: { title?: unknown; starred?: unknown; archived?: unknown };
  try { body = await c.req.json(); } catch { return c.json({ detail: 'Invalid JSON' }, 400); }
  if (body.title !== undefined && (typeof body.title !== 'string' || body.title.trim().length > 160)) {
    return c.json({ detail: 'title must be a string up to 160 characters' }, 422);
  }
  if (body.starred !== undefined && typeof body.starred !== 'boolean') {
    return c.json({ detail: 'starred must be a boolean' }, 422);
  }
  if (body.archived !== undefined && typeof body.archived !== 'boolean') {
    return c.json({ detail: 'archived must be a boolean' }, 422);
  }

  const current = await c.env.DB.prepare(`
    SELECT title, starred, archived FROM conversation_metadata
    WHERE user_id = ? AND session_id = ?
  `).bind(id, sessionId).first<{ title: string | null; starred: number; archived: number }>();
  const title = body.title === undefined ? current?.title ?? null : body.title.trim();
  const starred = body.starred === undefined ? current?.starred ?? 0 : Number(body.starred);
  const archived = body.archived === undefined ? current?.archived ?? 0 : Number(body.archived);
  await c.env.DB.prepare(`
    INSERT INTO conversation_metadata (user_id, session_id, title, starred, archived, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id, session_id) DO UPDATE SET
      title = excluded.title, starred = excluded.starred, archived = excluded.archived,
      updated_at = excluded.updated_at
  `).bind(id, sessionId, title, starred, archived, Math.floor(Date.now() / 1000)).run();
  return conversationDetail(c, id, sessionId);
});