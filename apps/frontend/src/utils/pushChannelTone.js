export const PUSH_CHANNEL_STALE_MS = 24 * 60 * 60 * 1000;

export function pushChannelTone({ last_success_at, last_error, last_attempt_at, now } = {}) {
  const refNow = typeof now === 'number' ? now : Date.now();
  const successMs = last_success_at ? new Date(last_success_at).getTime() : null;
  const successAge = successMs !== null && !Number.isNaN(successMs) ? refNow - successMs : null;
  const hasAttempt = Boolean(last_attempt_at);
  const isStale = hasAttempt && (successAge === null || successAge > PUSH_CHANNEL_STALE_MS);
  const degraded = Boolean(last_error) || isStale;

  let tone;
  if (degraded) tone = 'degraded';
  else if (last_success_at) tone = 'healthy';
  else tone = 'idle';

  return { tone, degraded, isStale, successAgeMs: successAge };
}
