---
name: Prerender $X replacement-pattern corruption
description: String.prototype.replace() $X specifiers corrupt prerendered HTML when chapter/SSR content contains dollar-sign sequences; fix is function-form replacements.
---

## The bug

`String.prototype.replace(pattern, templateString)` silently interprets `$'`, `$&`, `` $` ``, `$1`–`$9` in the replacement string as special substitution patterns:
- `$'` → inserts everything AFTER the match in the original string
- `$&` → inserts the entire matched substring
- `` $` `` → inserts everything BEFORE the match

If any data-derived value (chapter preload JSON, SSR HTML, inlined CSS, FAQ JSON-LD) contains one of those sequences, the resulting HTML is silently corrupted. The most dangerous case is `$'`:

1. The chapter preload `<script>window.__CHAPTER_PRELOAD__=...</script>` gets a huge chunk of the rest of the HTML file injected into its content.
2. The browser closes the `<script>` at the first `</script>` it encounters in that injected blob.
3. The remaining content (HTML tags, text nodes) is then parsed as visible body HTML — "raw JSON / content appearing above the navbar."

**Why:** Dollar signs (`$`) appear naturally in educational text (prices, LaTeX notation like `$E=mc^2$`, chemistry, Assamese text combined with apostrophes). Apostrophes (`'`) are extremely common. The combination `$'` can occur in any content mixing dollar amounts with possessive forms.

## Fix

Use arrow-function replacements everywhere the replacement string is derived from user/data content:

```js
// BROKEN — $X specifiers in `replacement` corrupt output
html = html.replace(pattern, replacement);

// SAFE — function form never interprets $X specifiers
html = html.replace(pattern, () => replacement);
```

**How to apply:** In every prerender script, wrap the replacement with `() =>` when `replacement` is constructed from: SSR HTML output, JSON.stringify() of any data, inlined CSS file content, or FAQ/JSON-LD entries.

## Affected files (all fixed)

- `prerender-routes.mjs` — `injectShell` (root div + inline scripts), `inlineMainCssOnce`, `injectFaqJsonLdIntoHead`
- `prerender-chat.mjs` — root div injection
- `prerender-library.mjs` — root div + inline scripts + CSS content

## Safe exceptions

- `_page-chunk-preload.mjs` line 99: uses `$1` **intentionally** to capture a group — correct.
- `_page-chunk-preload.mjs` line 102: replacement is a pure URL path — no `$X` possible.
- `rewriteHead()` in all scripts: uses `escapeHtml()` which converts `'` → `&#39;`, breaking the `$'` pattern. Plus canonical URLs never contain `$'`. Safe as-is.
- `prerender-static-routes.mjs`: all replacements use hardcoded strings. Safe as-is.
