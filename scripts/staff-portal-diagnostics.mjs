import { spawnSync } from 'node:child_process';
import { rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';

export const STAFF_PORTAL_TRACE_OPTIONS = Object.freeze({
  screenshots: false,
  snapshots: true,
  sources: false,
});

export function addLoginTokensToRedactions(tokens, sensitiveValues) {
  for (const name of ['access_token', 'refresh_token']) {
    if (typeof tokens?.[name] === 'string' && tokens[name]) {
      sensitiveValues.add(tokens[name]);
    }
  }
}

export async function saveSafeStaffPortalScreenshot(page, screenshotPath, sensitiveValues = []) {
  try {
    const knownRedactions = [
      ...new Set(sensitiveValues.filter(Boolean).flatMap(sensitiveVariants).filter(Boolean)),
    ];
    await page.evaluate(() => {
      window.__staffPortalScreenshotMutationCount = 0;
      new MutationObserver(() => {
        window.__staffPortalScreenshotMutationCount += 1;
      }).observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        characterData: true,
      });
    });
    await page.waitForTimeout(100);

    const result = await page.evaluate(knownValues => {
      const isVisible = element => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity) !== 0
          && rect.width > 0
          && rect.height > 0;
      };
      if (window.__staffPortalScreenshotMutationCount) {
        return { safe: false, reason: 'page changed while preparing diagnostic snapshot' };
      }
      const sensitiveName = /(?:pass(?:word)?|token|secret|authorization|credential|api[-_]?key|session|cookie)/i;
      const visibleElements = [...document.querySelectorAll('*')].filter(isVisible);
      const visibleControls = visibleElements
        .filter(element => element.matches('input, textarea, select'));
      const sensitiveControls = visibleControls
        .filter(element =>
          element.type === 'password'
          || sensitiveName.test([
            element.name,
            element.id,
            element.autocomplete,
            element.getAttribute('aria-label'),
            element.getAttribute('placeholder'),
          ].filter(Boolean).join(' ')));
      if (sensitiveControls.length) {
        return { safe: false, reason: 'visible sensitive authentication control' };
      }
      const hasOpaqueRendering = visibleElements.some(element => {
        if (element.matches('iframe, frame, canvas, video, object, embed')) return true;
        const style = getComputedStyle(element);
        const before = getComputedStyle(element, '::before').content;
        const after = getComputedStyle(element, '::after').content;
        return style.backgroundImage !== 'none'
          || (before !== 'none' && before !== 'normal')
          || (after !== 'none' && after !== 'normal');
      });
      if (hasOpaqueRendering) {
        return { safe: false, reason: 'opaque rendered content cannot be sanitized' };
      }

      const storageValues = [];
      for (const storage of [localStorage, sessionStorage]) {
        for (let index = 0; index < storage.length; index += 1) {
          const key = storage.key(index);
          const value = key && storage.getItem(key);
          if (value && (sensitiveName.test(key) || value.length >= 16)) storageValues.push(value);
        }
      }
      const browserSensitiveVariants = value => {
        const bytes = new TextEncoder().encode(value);
        let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        const base64 = btoa(binary);
        return [
          value,
          encodeURIComponent(value),
          base64,
          base64.replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, ''),
          JSON.stringify(value).slice(1, -1),
        ];
      };
      const replacements = [
        ...new Set([
          ...knownValues,
          ...storageValues.flatMap(browserSensitiveVariants),
        ].filter(Boolean)),
      ];
      const redact = input => {
        let output = input || '';
        for (const value of replacements) {
          output = output.replaceAll(value, '[REDACTED]');
        }
        return output;
      };

      const unsafeSignatures = [
        /\b(?:bearer|basic)\s+(?!\[REDACTED\])\S+/i,
        /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/,
        /\b(?:pass(?:word)?|secret|api[-_ ]?key|session|cookie|credential|authorization|(?:access|refresh|auth)[-_ ]?token|cf-access-client-(?:id|secret))\b\s*[:=]\s*(?!\[REDACTED\])\S+/i,
      ];
      const visibleControlContent = visibleControls
        .flatMap(element => element instanceof HTMLSelectElement
          ? [element.value, ...[...element.selectedOptions].map(option => option.textContent || '')]
          : [
              element.value,
              element.getAttribute('placeholder') || '',
              element.getAttribute('title') || '',
              element.getAttribute('aria-label') || '',
            ])
        .map(redact)
        .filter(Boolean);
      const renderedContent = [
        redact(document.body?.innerText || ''),
        redact(document.title),
        redact(`${location.origin}${location.pathname}`),
        ...visibleControlContent,
      ];
      if (renderedContent.some(content =>
        unsafeSignatures.some(pattern => pattern.test(content)))) {
        return { safe: false, reason: 'credential-shaped visible page content' };
      }
      if (renderedContent.some(content =>
        replacements.some(value => content.includes(value)))) {
        return { safe: false, reason: 'authentication storage value remained visible' };
      }
      return {
        safe: true,
        snapshot: {
          title: renderedContent[1] || 'Staff portal failure',
          location: renderedContent[2],
          text: renderedContent[0] || '(No visible page text)',
        },
      };
    }, knownRedactions);

    if (!result.safe) throw new Error(`unsafe staff portal screenshot: ${result.reason}`);
    const escapeHtml = value => value.replace(/[&<>"']/g, character => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    })[character]);
    await page.setContent(`<!doctype html>
      <meta charset="utf-8">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
      <title>Sanitized staff portal diagnostic</title>
      <style>
        body { margin: 0; padding: 32px; background: #f8fafc; color: #172033; font: 16px/1.5 system-ui, sans-serif; }
        main { max-width: 1100px; margin: auto; background: white; border: 1px solid #d9e1ec; border-radius: 12px; padding: 28px; }
        h1 { margin-top: 0; font-size: 24px; }
        .location { color: #526078; overflow-wrap: anywhere; }
        pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
      </style>
      <main>
        <h1>${escapeHtml(result.snapshot.title)}</h1>
        <p class="location">${escapeHtml(result.snapshot.location)}</p>
        <pre>${escapeHtml(result.snapshot.text)}</pre>
      </main>`);
    await page.screenshot({ path: screenshotPath, fullPage: true });
  } catch (error) {
    await rm(screenshotPath, { force: true });
    throw error;
  }
}

function sensitiveVariants(value) {
  const buffer = Buffer.from(value);
  return [
    value,
    encodeURIComponent(value),
    buffer.toString('base64'),
    buffer.toString('base64url'),
    JSON.stringify(value).slice(1, -1),
  ];
}

export async function saveRedactedTrace(rawTracePath, tracePath, sensitiveValues) {
  const redactions = [
    ...new Set(
      sensitiveValues
        .filter(Boolean)
        .flatMap(sensitiveVariants)
        .filter(Boolean),
    ),
  ];
  const redactionsPath = resolve(
    tmpdir(),
    `staff-portal-trace-redactions-${process.pid}.json`,
  );
  await writeFile(redactionsPath, JSON.stringify(redactions), { mode: 0o600 });
  try {
    const sanitizer = spawnSync('python3', [
      '-c',
      [
        'import json, re, sys, zipfile',
        'source, target, redactions_path = sys.argv[1:]',
        'with open(redactions_path, encoding="utf-8") as handle:',
        '    redactions = [value.encode() for value in json.load(handle) if value]',
        'signatures = [',
        '    ("authorization header", re.compile(rb"authorization[\'\\\" ]*[:=][ ]*[\'\\\"]?(?:bearer|basic)[ ]+(?!\\[REDACTED\\])[^\\s\'\\\",}]+", re.I)),',
        '    ("authorization header record", re.compile(rb"[\'\\\"]name[\'\\\"]\\s*:\\s*[\'\\\"]authorization[\'\\\"].{0,160}?[\'\\\"]value[\'\\\"]\\s*:\\s*[\'\\\"](?:bearer|basic)\\s+(?!\\[REDACTED\\])[^\'\\\"]+", re.I | re.S)),',
        '    ("JWT-shaped value", re.compile(rb"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")),',
        '    ("Cloudflare Access credential", re.compile(rb"cf-access-client-(?:id|secret)[\'\\\" ]*[:=][ ]*[\'\\\"]?(?!\\[REDACTED\\])[^\\s\'\\\",}]+", re.I)),',
        '    ("Cloudflare Access header record", re.compile(rb"[\'\\\"]name[\'\\\"]\\s*:\\s*[\'\\\"]cf-access-client-(?:id|secret)[\'\\\"].{0,160}?[\'\\\"]value[\'\\\"]\\s*:\\s*[\'\\\"](?!\\[REDACTED\\])[^\'\\\"]+", re.I | re.S)),',
        '    ("auth storage value", re.compile(rb"(?:access_token|refresh_token|authToken)[\'\\\" ]*[:=][ ]*[\'\\\"]?(?!\\[REDACTED\\])[^\\s\'\\\",}]+", re.I)),',
        '    ("auth storage record", re.compile(rb"[\'\\\"]name[\'\\\"]\\s*:\\s*[\'\\\"](?:access_token|refresh_token|authToken)[\'\\\"].{0,160}?[\'\\\"]value[\'\\\"]\\s*:\\s*[\'\\\"](?!\\[REDACTED\\])[^\'\\\"]+", re.I | re.S)),',
        ']',
        'findings = set()',
        'with zipfile.ZipFile(source, "r") as incoming, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as outgoing:',
        '    for info in incoming.infolist():',
        '        data = incoming.read(info.filename)',
        '        for value in redactions:',
        '            data = data.replace(value, b"[REDACTED]")',
        '        for label, pattern in signatures:',
        '            if pattern.search(data):',
        '                findings.add((label, info.filename))',
        '        outgoing.writestr(info, data)',
        'if findings:',
        '    summary = ", ".join(f"{label} in {name}" for label, name in sorted(findings))',
        '    raise SystemExit(f"unsafe credential signature(s) remained after trace sanitization: {summary}")',
      ].join('\n'),
      rawTracePath,
      tracePath,
      redactionsPath,
    ], { encoding: 'utf8' });
    if (sanitizer.status !== 0) {
      throw new Error(sanitizer.stderr.trim() || `trace sanitizer exited ${sanitizer.status}`);
    }
  } catch (error) {
    await rm(tracePath, { force: true });
    throw error;
  } finally {
    await rm(redactionsPath, { force: true });
  }
}