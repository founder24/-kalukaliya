// Mirror of backend `_slugify_heading` in
// artifacts/syrabit-backend/routes/edu_study.py — keep these in lockstep.
// Used to build chapter section anchor ids (`sec-<slug>`) so that AI-notes
// citation deep-links (`/…/chapter#sec-…`) land on the matching DOM node
// regardless of language or heading length.
//
// Backend (Python 3) rules:
//   s = (s or "").lower().strip()
//   s = re.sub(r"[^\w\s-]", "", s)        # \w covers Unicode letters/digits
//   s = re.sub(r"[\s_-]+", "-", s).strip("-")
//   return s[:80] or "section"
//
// JS replication: use Unicode property escapes to approximate Python's
// `\w` (Letter + Number + Mark + underscore) and apply the same 80-char
// cap and "section" fallback.
export function slugifyHeading(input) {
  let s = (input ?? '').toString().toLowerCase().trim();
  s = s.replace(/[^\p{L}\p{N}\p{M}_\s-]/gu, '');
  s = s.replace(/[\s_-]+/g, '-').replace(/^-+|-+$/g, '');
  return s.slice(0, 80) || 'section';
}
