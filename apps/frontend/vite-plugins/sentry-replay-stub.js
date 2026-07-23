/**
 * sentry-replay-stub Vite plugin.
 *
 * Sentry Session Replay is never initialised on this project, but
 * @sentry/browser re-exports everything from @sentry-internal/replay,
 * @sentry-internal/replay-canvas, and @sentry-internal/feedback at its
 * barrel level.  Even though the integration objects are never called,
 * Rollup bundles the full packages because it cannot prove they are
 * side-effect-free — adding ~50 KB to every page as "unused JS".
 *
 * This plugin intercepts those package IDs with resolveId/load, returning
 * minimal stub modules whose named exports match what @sentry/browser
 * re-exports.  Rollup can then tree-shake the stubs to nothing because
 * they carry no real code.
 *
 * Named exports verified against:
 *   @sentry/browser@10.57.0 prod/index.js lines 25-26,
 *   @sentry-internal/replay@10.57.0 esm/index.js
 */

const noop = () => null;

const STUBS = {
  '@sentry-internal/replay': `
export const getReplay = () => null;
export const replayIntegration = () => null;
export const Replay = null;
`,
  '@sentry-internal/replay-canvas': `
export const replayCanvasIntegration = () => null;
`,
  // Full export list verified from @sentry-internal/feedback@10.57.0 esm/index.js
  // and all usages inside @sentry/browser/build/npm/esm/prod/*.js
  '@sentry-internal/feedback': `
export const getFeedback = () => null;
export const sendFeedback = () => Promise.resolve();
export const buildFeedbackIntegration = () => () => null;
export const feedbackModalIntegration = () => null;
export const feedbackScreenshotIntegration = () => null;
export const feedbackAsyncIntegration = () => null;
export const feedbackSyncIntegration = () => null;
export const feedbackIntegration = () => null;
`,
};

// Regex matching any absolute path into these packages inside node_modules
const PKG_PATH_RE = /@sentry-internal[/\\](replay-canvas|replay|feedback)[/\\]/;

// Map a file path to a stub package key
function pathToKey(id) {
  const m = id.match(/@sentry-internal[/\\](replay-canvas|replay|feedback)[/\\]/);
  if (!m) return null;
  return `@sentry-internal/${m[1]}`;
}

export default function sentryReplayStubPlugin() {
  const VIRTUAL_PREFIX = '\0sentry-replay-stub:';

  return {
    name: 'syrabit-sentry-replay-stub',
    // enforce:'pre' so this resolveId wins over Vite's built-in node_modules
    // resolver; without it Rollup resolves the real pnpm path first and the
    // virtual module is never used.
    enforce: 'pre',

    resolveId(id, importer, options) {
      // Intercept bare specifier  e.g. '@sentry-internal/replay'
      if (Object.prototype.hasOwnProperty.call(STUBS, id)) {
        return VIRTUAL_PREFIX + id;
      }
      // Intercept absolute paths that point into these packages
      if (PKG_PATH_RE.test(id)) {
        const key = pathToKey(id);
        if (key && STUBS[key]) return VIRTUAL_PREFIX + key;
      }
      return null;
    },

    load(id) {
      if (id.startsWith(VIRTUAL_PREFIX)) {
        const pkg = id.slice(VIRTUAL_PREFIX.length);
        return STUBS[pkg] || 'export default {};';
      }
      return null;
    },
  };
}
