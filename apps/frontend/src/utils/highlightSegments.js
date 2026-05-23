// Task #437 — split a snippet into ordered text segments + matched
// suspicious tokens so the audit UI can render <mark> spans inline.
// Token matching is case-insensitive and longest-first (so "ssible
// terms" wins over "ssible" when both are flagged), and falls back
// to plain text when no tokens are provided.
//
// Extracted from AdminHealth.jsx in Task #438 so it can be unit tested
// in isolation without dragging the full admin component tree.

const escapeRegex = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function buildHighlightedSegments(text, tokens) {
  if (!text || typeof text !== 'string') return [];
  const cleanTokens = (tokens || [])
    .filter((t) => typeof t === 'string' && t.trim().length > 0)
    .map((t) => t.trim())
    // Longest-first so multi-word matches aren't shadowed by their prefix.
    .sort((a, b) => b.length - a.length);
  if (cleanTokens.length === 0) return [{ text, highlight: false }];
  let pattern;
  try {
    pattern = new RegExp(`(${cleanTokens.map(escapeRegex).join('|')})`, 'gi');
  } catch {
    return [{ text, highlight: false }];
  }
  const parts = text.split(pattern);
  return parts
    .filter((p) => p !== '')
    .map((p) => ({
      text: p,
      highlight: cleanTokens.some((t) => p.toLowerCase() === t.toLowerCase()),
    }));
}

;
