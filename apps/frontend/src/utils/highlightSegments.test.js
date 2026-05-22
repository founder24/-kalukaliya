import { describe, it, expect } from 'vitest';
import { buildHighlightedSegments } from './highlightSegments';

// Task #438 — lock down the suspicious-token highlighter that powers
// the Recent cleanups Original snippet in AdminHealth. These tests
// guard the contract relied on by the JSX renderer:
//   - return value is an array of { text, highlight } segments
//   - text concatenated in order rebuilds the original input
//   - highlights only fire for full case-insensitive token matches.

const reconstruct = (segments) => segments.map((s) => s.text).join('');

describe('buildHighlightedSegments', () => {
  it('returns [] for empty / non-string text', () => {
    expect(buildHighlightedSegments('', ['x'])).toEqual([]);
    expect(buildHighlightedSegments(null, ['x'])).toEqual([]);
    expect(buildHighlightedSegments(undefined, ['x'])).toEqual([]);
    expect(buildHighlightedSegments(42, ['x'])).toEqual([]);
    expect(buildHighlightedSegments({}, ['x'])).toEqual([]);
  });

  it('returns the whole text un-highlighted when no tokens given', () => {
    const out = buildHighlightedSegments('উৰুকা hello world', []);
    expect(out).toEqual([{ text: 'উৰুকা hello world', highlight: false }]);
  });

  it('returns the whole text un-highlighted when tokens are all blank', () => {
    const out = buildHighlightedSegments('hello', ['', '   ', null]);
    expect(out).toEqual([{ text: 'hello', highlight: false }]);
  });

  it('matches a single token case-insensitively', () => {
    const out = buildHighlightedSegments('Hello WORLD hello', ['hello']);
    expect(reconstruct(out)).toBe('Hello WORLD hello');
    const highlights = out.filter((s) => s.highlight).map((s) => s.text);
    expect(highlights).toEqual(['Hello', 'hello']);
  });

  it('prefers the longest match when tokens overlap', () => {
    // "ssible" is a strict prefix of "ssible terms" — without
    // longest-first sort, the prefix would consume the match first
    // and "terms" would never be highlighted as part of the run.
    const out = buildHighlightedSegments('উৰুকা ssible terms here', [
      'ssible',
      'ssible terms',
    ]);
    expect(reconstruct(out)).toBe('উৰুকা ssible terms here');
    const highlighted = out.filter((s) => s.highlight).map((s) => s.text);
    expect(highlighted).toEqual(['ssible terms']);
  });

  it('escapes regex-special characters in tokens', () => {
    // A naive `new RegExp(token)` would explode here; assert that
    // tokens containing ., *, (, ), [, ], +, ?, ^, $, |, \ are
    // matched as literal text instead.
    const text = 'price is $9.99 (USD) — see file [a].txt or *.json';
    const tokens = ['$9.99', '(USD)', '[a].txt', '*.json'];
    const out = buildHighlightedSegments(text, tokens);
    expect(reconstruct(out)).toBe(text);
    const highlighted = out.filter((s) => s.highlight).map((s) => s.text);
    expect(highlighted).toEqual(['$9.99', '(USD)', '[a].txt', '*.json']);
  });

  it('preserves segment order so JSX renders the original sequence', () => {
    const out = buildHighlightedSegments('a foo b bar c foo d', ['foo', 'bar']);
    expect(out.map((s) => s.text)).toEqual([
      'a ', 'foo', ' b ', 'bar', ' c ', 'foo', ' d',
    ]);
    expect(out.map((s) => s.highlight)).toEqual([
      false, true, false, true, false, true, false,
    ]);
  });

  it('highlights every literal occurrence including inside larger words', () => {
    // The sanitiser already produces tokens that are full Latin runs
    // (e.g. "ssible terms"), so we want raw substring matches to
    // light up wherever they appear in the snippet — including the
    // "foo" prefix of "foobar". The reconstructed text is unchanged.
    const out = buildHighlightedSegments('foobar foo', ['foo']);
    const highlighted = out.filter((s) => s.highlight).map((s) => s.text);
    expect(highlighted).toEqual(['foo', 'foo']);
    expect(reconstruct(out)).toBe('foobar foo');
  });

  it('ignores non-string tokens without crashing', () => {
    const out = buildHighlightedSegments('hello world', [
      'world',
      null,
      undefined,
      42,
      { x: 1 },
    ]);
    const highlighted = out.filter((s) => s.highlight).map((s) => s.text);
    expect(highlighted).toEqual(['world']);
  });
});
