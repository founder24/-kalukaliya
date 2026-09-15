/**
 * Normalise content pasted from documents and web pages into safe Markdown
 * without changing the words the editor received.
 *
 * This is intentionally conservative: existing Markdown markers are kept,
 * while common rich-text whitespace and bullet characters are made consistent.
 * If anything unexpected happens, callers can keep the original value.
 */
export function formatPastedContent(value) {
  if (typeof value !== 'string') return '';

  try {
    const lines = value
      .replace(/\r\n?/g, '\n')
      .replace(/\u2028|\u2029/g, '\n')
      .replace(/\u00a0/g, ' ')
      .split('\n')
      .map((line) => {
        const trimmed = line.trim();
        if (!trimmed) return '';

        // Google Docs and copied PDFs commonly use Unicode bullets that
        // Markdown does not recognise consistently.
        const bullet = trimmed.match(/^[•◦▪▸►‣]\s*(.*)$/);
        if (bullet) return `- ${bullet[1].trim()}`;

        // Keep ordered-list numbering, but normalise ")" and full-stop
        // variants to the Markdown form.
        const ordered = trimmed.match(/^(\d+)[.)]\s+(.*)$/);
        if (ordered) return `${ordered[1]}. ${ordered[2].trim()}`;

        // Normalise existing unordered list markers to one representation.
        const unordered = trimmed.match(/^[-*+]\s+(.*)$/);
        if (unordered) return `- ${unordered[1].trim()}`;

        return line.replace(/[ \t]+$/g, '').trim();
      });

    return lines
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  } catch {
    return value;
  }
}