const KEY = 'syrabit:recent-chapters';
const MAX = 6;

function safeRead() {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

function safeWrite(list) {
  if (typeof window === 'undefined') return;
  try { window.localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))); } catch {}
}

export function pushRecentChapter(entry) {
  if (!entry || !entry.path) return;
  const cleaned = {
    path: entry.path,
    title: (entry.title || '').slice(0, 140),
    subject: (entry.subject || '').slice(0, 80),
    board: (entry.board || '').slice(0, 40),
    ts: Date.now(),
  };
  const list = safeRead().filter((it) => it && it.path !== cleaned.path);
  list.unshift(cleaned);
  safeWrite(list);
}

export function getRecentChapters() {
  return safeRead();
}

export function clearRecentChapters() {
  safeWrite([]);
}
