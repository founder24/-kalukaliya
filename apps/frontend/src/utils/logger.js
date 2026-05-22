/**
 * Structured logger for Syrabit frontend.
 *
 * Usage:
 *   log.error('Payment verification failed', { error: err.message, userId, status: 402 })
 *   log.warn('Cache miss', { key, ttl })
 *   log.info('Service worker registered', { scope: reg.scope })
 *
 * Rules:
 *   - error: always logged (prod + dev)
 *   - warn:  only in development
 *   - info:  only in development
 *
 * Each call automatically appends { _ts, _level, _env } to the context so
 * log lines are grep-friendly and self-describing in the browser devtools.
 */

const isDev = import.meta.env.DEV;

function buildCtx(level, ctx) {
  return {
    ...ctx,
    _level: level,
    _ts: new Date().toISOString(),
    _env: isDev ? 'development' : 'production',
  };
}

export const log = {
  error(message, ctx = {}) {
    console.error(message, buildCtx('error', ctx));
  },

  warn(message, ctx = {}) {
    if (isDev) console.warn(message, buildCtx('warn', ctx));
  },

  info(message, ctx = {}) {
    if (isDev) console.info(message, buildCtx('info', ctx));
  },
};
