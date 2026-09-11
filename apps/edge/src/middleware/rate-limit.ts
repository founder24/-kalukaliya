/**
 * Per-Language Rate Limiting via Cloudflare Durable Objects
 *
 * Tracks request counts per (userId + lang) combination using hourly windows.
 * Prevents Assamese quota exhaustion independently from English.
 * One Durable Object instance owns each identity/language/window bucket, so
 * concurrent requests are serialized and cannot overwrite one another.
 */

export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: number; // Unix timestamp (ms) when the window resets
}

// Chat's production allowance is one minute per response language. Keep this
// in the edge limiter (the authoritative reservation point) so API and UI
// consumers do not drift back to the retired daily/30-message contract.
export const CHAT_REQUESTS_PER_MINUTE = 6;
export const CHAT_RATE_LIMIT_WINDOW_MS = 60 * 1000;

interface RateLimitCommand {
  limit: number;
  resetAt: number;
}

const BROWSER_ANON_ID_PATTERN = /^anon_[a-f0-9]{32}$/;
const ANONYMOUS_COOKIE_NAME = 'syrabit_anon_id';
const SIGNATURE_PATTERN = /^[a-f0-9]{64}$/;
const ANONYMOUS_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;
const CLEANUP_RECOVERY_DELAY_MS = 5 * 60 * 1000;
const CLEANUP_FAILURE_ALERT_THRESHOLD = 3;
const CLEANUP_FAILURE_ALERT_DEDUP_MS = 60 * 60 * 1000;
const CLEANUP_FAILURE_COUNT_KEY = 'cleanupFailureCount';
const CLEANUP_ALERTED_AT_KEY = 'cleanupAlertedAt';
const CLEANUP_INCIDENT_TOKEN_KEY = 'cleanupIncidentToken';
const CLEANUP_HEALTH_AGGREGATE_NAME = 'rate-limit-cleanup-health-aggregate';
const CLEANUP_HEALTH_INCIDENTS_KEY = 'activeCleanupIncidents';
const CLEANUP_HISTORY_RETENTION_MS = 24 * 60 * 60 * 1000;
const CLEANUP_HISTORY_MAX_ENTRIES = 20;
const CLEANUP_COUNT_BUCKET_MS = 60 * 1000;
const CLEANUP_AGGREGATE_ALERT_STATE_KEY = 'cleanupAggregateAlertState';
const DEFAULT_CLEANUP_AGGREGATE_ALERT_THRESHOLD = 3;
const DEFAULT_CLEANUP_AGGREGATE_ALERT_WINDOW_MS = 60 * 60 * 1000;
export const RATE_LIMIT_CLEANUP_HEALTH_KEY = 'health:rate-limit-cleanup';

interface CleanupHealthTransition {
  event: 'failed' | 'recovered';
  occurred_at: string;
}

interface CleanupIncidentCountBucket {
  started_at: string;
  count: number;
}

interface CleanupHealthState {
  degraded: boolean;
  active_incidents: number;
  latest_failure_at: string | null;
  latest_recovery_at: string | null;
  rolling_incident_count: number;
  history_window_hours: number;
  recent_transitions: CleanupHealthTransition[];
  incident_count_buckets: CleanupIncidentCountBucket[];
  alert: CleanupAggregateAlertSnapshot;
}

interface CleanupAggregateAlertState {
  window_started_at: string;
  last_fired_at: string;
}

interface CleanupAggregateAlertSnapshot {
  enabled: boolean;
  threshold: number;
  window_minutes: number;
  state: 'disabled' | 'healthy' | 'active' | 'recovered' | 'expired' | 'delivery_failed';
  last_fired_at: string | null;
  window_expires_at: string | null;
}

interface CleanupIncidentCommand {
  action: 'failed' | 'recovered';
  incidentToken: string;
  occurredAt: string;
}

export interface AnonymousIdentity {
  id: string;
  setCookie: string | null;
}

function isBrowserAnonId(value: string | null | undefined): value is string {
  return typeof value === 'string' && BROWSER_ANON_ID_PATTERN.test(value.trim());
}

function cookieValue(cookieHeader: string, name: string): string | null {
  const prefix = `${name}=`;
  const part = cookieHeader.split(';')
    .map(value => value.trim())
    .find(value => value.startsWith(prefix));
  return part ? part.slice(prefix.length) : null;
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

function timingSafeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function signatureFor(id: string, secret: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  return hex(await crypto.subtle.sign('HMAC', key, encoder.encode(id)));
}

async function readSignedCookie(request: Request, secret?: string): Promise<string | null> {
  if (!secret) return null;
  const encoded = cookieValue(request.headers.get('Cookie') ?? '', ANONYMOUS_COOKIE_NAME);
  if (!encoded) return null;

  let value: string;
  try {
    value = decodeURIComponent(encoded);
  } catch {
    return null;
  }
  const separator = value.lastIndexOf('.');
  if (separator < 0) return null;
  const id = value.slice(0, separator);
  const signature = value.slice(separator + 1);
  if (!isBrowserAnonId(id) || !SIGNATURE_PATTERN.test(signature)) return null;
  const expected = await signatureFor(id, secret);
  return timingSafeEqual(signature, expected) ? id : null;
}

function ipFallback(request: Request): string {
  const ip = request.headers.get('CF-Connecting-IP') ?? 'unknown';
  const normalizedIp = ip.trim().toLowerCase().replace(/[^a-z0-9]/g, '_').slice(0, 55);
  return `ip_${normalizedIp}`;
}

export async function resolveAnonymousIdentity(
  request: Request,
  cookieSecret?: string,
): Promise<AnonymousIdentity> {
  const cookieId = await readSignedCookie(request, cookieSecret);
  if (cookieId) return { id: cookieId, setCookie: null };

  if (!cookieSecret) return { id: ipFallback(request), setCookie: null };

  // Never sign a caller-selected ID. Only an edge-minted random ID can become
  // an ownership credential for anonymous history and quota.
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const id = `anon_${Array.from(bytes).map(byte => byte.toString(16).padStart(2, '0')).join('')}`;
  const signature = await signatureFor(id, cookieSecret);
  return {
    id,
    setCookie: `${ANONYMOUS_COOKIE_NAME}=${id}.${signature}; Path=/; Max-Age=${ANONYMOUS_COOKIE_MAX_AGE}; HttpOnly; Secure; SameSite=Lax`,
  };
}

/**
 * Resolve the anonymous browser identity used by the edge burst limiter.
 * This mirrors the API Worker's anonymous identity contract so one browser
 * does not share a global limiter bucket with every other anonymous student.
 */
export async function anonymousRateLimitIdentity(
  request: Request,
  cookieSecret?: string,
): Promise<string> {
  return (await resolveAnonymousIdentity(request, cookieSecret)).id;
}

export function anonymousNetworkRateLimitIdentity(request: Request): string {
  return ipFallback(request);
}

/**
 * Check if a request is within the rate limit for a given user + language.
 *
 * @param namespace - Durable Object namespace for strongly-consistent counters
 * @param userId - Authenticated user ID (or "anonymous")
 * @param lang - Language code ("en" or "as")
 * @param limit - Max requests per minute per language
 * @returns RateLimitResult with allowed status, remaining count, and reset time
 */
export async function checkRateLimit(
  namespace: DurableObjectNamespace,
  userId: string,
  lang: string,
  limit: number = CHAT_REQUESTS_PER_MINUTE,
  windowMs: number = CHAT_RATE_LIMIT_WINDOW_MS,
): Promise<RateLimitResult> {
  const now = Date.now();
  const windowKey = Math.floor(now / windowMs);
  const resetAt = (windowKey + 1) * windowMs;

  const bucket = `rl:${userId}:${lang}:${windowKey}`;
  const stub = namespace.get(namespace.idFromName(bucket));
  const response = await stub.fetch('https://rate-limit.internal/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit, resetAt } satisfies RateLimitCommand),
  });

  if (!response.ok) {
    throw new Error(`Atomic rate-limit store returned ${response.status}`);
  }

  return response.json<RateLimitResult>();
}

/**
 * Build rate limit response headers for the client.
 */
export function rateLimitHeaders(result: RateLimitResult, limit: number = 30): Record<string, string> {
  return {
    'X-RateLimit-Limit': String(limit),
    'X-RateLimit-Remaining': String(Math.max(0, result.remaining)),
    'X-RateLimit-Reset': String(Math.floor(result.resetAt / 1000)),
    ...(result.allowed ? {} : {
      'Retry-After': String(Math.ceil((result.resetAt - Date.now()) / 1000)),
    }),
  };
}

/**
 * Strongly-consistent hourly counter. A bucket name maps to one object, and a
 * Durable Object processes requests one at a time. The alarm removes expired
 * state after the hour rolls over.
 */
export class RateLimitDurableObject {
  constructor(
    private readonly state: DurableObjectState,
    private readonly env?: Partial<Pick<
      Env,
      'RATE_LIMIT_KV'
      | 'RATE_LIMIT_DO'
      | 'RATE_LIMIT_CLEANUP_ALERT_WEBHOOK_URL'
      | 'RATE_LIMIT_CLEANUP_ALERT_THRESHOLD'
      | 'RATE_LIMIT_CLEANUP_ALERT_WINDOW_MINUTES'
    >>,
  ) {}

  private aggregateAlertConfig(): {
    enabled: boolean;
    threshold: number;
    windowMs: number;
  } {
    const parsedThreshold = Number(this.env?.RATE_LIMIT_CLEANUP_ALERT_THRESHOLD);
    const parsedWindowMinutes = Number(this.env?.RATE_LIMIT_CLEANUP_ALERT_WINDOW_MINUTES);
    const threshold = Number.isSafeInteger(parsedThreshold) && parsedThreshold > 0
      ? parsedThreshold
      : DEFAULT_CLEANUP_AGGREGATE_ALERT_THRESHOLD;
    const windowMinutes = Number.isFinite(parsedWindowMinutes) && parsedWindowMinutes > 0
      ? parsedWindowMinutes
      : DEFAULT_CLEANUP_AGGREGATE_ALERT_WINDOW_MS / 60_000;
    return {
      enabled: Boolean(this.env?.RATE_LIMIT_CLEANUP_ALERT_WEBHOOK_URL),
      threshold,
      windowMs: windowMinutes * 60_000,
    };
  }

  private async sendAggregateCleanupAlert(
    incidentCount: number,
    threshold: number,
    windowStartedAt: string,
    windowExpiresAt: string,
    firedAt: string,
  ): Promise<boolean> {
    const webhookUrl = this.env?.RATE_LIMIT_CLEANUP_ALERT_WEBHOOK_URL;
    if (!webhookUrl) return false;
    try {
      const response = await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: `Chat-limit cleanup crossed the alert threshold: ${incidentCount} incidents (threshold ${threshold}) from ${windowStartedAt} to ${windowExpiresAt}; fired at ${firedAt}.`,
          event: 'rate_limit_cleanup_incident_threshold_crossed',
          incident_count: incidentCount,
          threshold,
          window_started_at: windowStartedAt,
          window_expires_at: windowExpiresAt,
          fired_at: firedAt,
        }),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  private async reportCleanupIncident(command: CleanupIncidentCommand): Promise<void> {
    if (!this.env?.RATE_LIMIT_DO) return;
    const aggregate = this.env.RATE_LIMIT_DO.get(
      this.env.RATE_LIMIT_DO.idFromName(CLEANUP_HEALTH_AGGREGATE_NAME),
    );
    const response = await aggregate.fetch('https://rate-limit.internal/cleanup-health', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(command),
    });
    if (!response.ok) {
      throw new Error(`Cleanup health aggregate returned ${response.status}`);
    }
  }

  private async updateCleanupHealthAggregate(request: Request): Promise<Response> {
    let command: CleanupIncidentCommand;
    try {
      command = await request.json<CleanupIncidentCommand>();
    } catch {
      return Response.json({ error: 'Invalid request' }, { status: 400 });
    }
    if (
      (command.action !== 'failed' && command.action !== 'recovered')
      || typeof command.incidentToken !== 'string'
      || !/^[a-f0-9-]{36}$/.test(command.incidentToken)
      || Number.isNaN(Date.parse(command.occurredAt))
    ) {
      return Response.json({ error: 'Invalid cleanup incident' }, { status: 400 });
    }

    const aggregateState = await this.state.storage.transaction(async txn => {
      const active = new Set(
        await txn.get<string[]>(CLEANUP_HEALTH_INCIDENTS_KEY) ?? [],
      );
      const alertState = await txn.get<CleanupAggregateAlertState>(
        CLEANUP_AGGREGATE_ALERT_STATE_KEY,
      );
      if (command.action === 'failed') active.add(command.incidentToken);
      else active.delete(command.incidentToken);
      await txn.put(CLEANUP_HEALTH_INCIDENTS_KEY, [...active]);
      return { activeIncidentCount: active.size, alertState };
    });
    let previous: CleanupHealthState | null = null;
    if (this.env?.RATE_LIMIT_KV) {
      try {
        const raw = await this.env.RATE_LIMIT_KV.get(RATE_LIMIT_CLEANUP_HEALTH_KEY);
        previous = raw ? JSON.parse(raw) as CleanupHealthState : null;
      } catch {
        // The serialized aggregate remains authoritative if telemetry KV is unavailable.
      }
    }
    const retentionCutoff = Date.now() - CLEANUP_HISTORY_RETENTION_MS;
    const recentTransitions = [
      ...(Array.isArray(previous?.recent_transitions) ? previous.recent_transitions : []),
      { event: command.action, occurred_at: command.occurredAt },
    ]
      .filter(transition => (
        (transition.event === 'failed' || transition.event === 'recovered')
        && typeof transition.occurred_at === 'string'
        && Date.parse(transition.occurred_at) >= retentionCutoff
      ))
      .slice(-CLEANUP_HISTORY_MAX_ENTRIES);
    const incidentCountBuckets = (Array.isArray(previous?.incident_count_buckets)
      ? previous.incident_count_buckets
      : [])
      .filter(bucket => (
        typeof bucket.started_at === 'string'
        && Date.parse(bucket.started_at) >= retentionCutoff
        && Number.isSafeInteger(bucket.count)
        && bucket.count > 0
      ));
    if (command.action === 'failed') {
      const occurredAt = Date.parse(command.occurredAt);
      const bucketStartedAt = new Date(
        Math.floor(occurredAt / CLEANUP_COUNT_BUCKET_MS) * CLEANUP_COUNT_BUCKET_MS,
      ).toISOString();
      const existingBucket = incidentCountBuckets.find(
        bucket => bucket.started_at === bucketStartedAt,
      );
      if (existingBucket) existingBucket.count += 1;
      else incidentCountBuckets.push({ started_at: bucketStartedAt, count: 1 });
    }
    const snapshot: CleanupHealthState = {
      degraded: aggregateState.activeIncidentCount > 0,
      active_incidents: aggregateState.activeIncidentCount,
      latest_failure_at: command.action === 'failed'
        ? command.occurredAt
        : previous?.latest_failure_at ?? null,
      latest_recovery_at: command.action === 'recovered'
        ? command.occurredAt
        : previous?.latest_recovery_at ?? null,
      rolling_incident_count: incidentCountBuckets.reduce(
        (total, bucket) => total + bucket.count,
        0,
      ),
      history_window_hours: CLEANUP_HISTORY_RETENTION_MS / (60 * 60 * 1000),
      recent_transitions: recentTransitions,
      incident_count_buckets: incidentCountBuckets,
      alert: {
        enabled: false,
        threshold: DEFAULT_CLEANUP_AGGREGATE_ALERT_THRESHOLD,
        window_minutes: DEFAULT_CLEANUP_AGGREGATE_ALERT_WINDOW_MS / 60_000,
        state: 'disabled',
        last_fired_at: null,
        window_expires_at: null,
      },
    };

    const alertConfig = this.aggregateAlertConfig();
    const now = Date.parse(command.occurredAt);
    let alertState = aggregateState.alertState;
    let alertStatus: CleanupAggregateAlertSnapshot['state'] = alertConfig.enabled
      ? 'healthy'
      : 'disabled';
    if (alertState) {
      const expiresAt = Date.parse(alertState.window_started_at) + alertConfig.windowMs;
      if (now >= expiresAt) {
        await this.state.storage.transaction(txn =>
          txn.delete(CLEANUP_AGGREGATE_ALERT_STATE_KEY)
        );
        alertState = undefined;
        alertStatus = alertConfig.enabled ? 'expired' : 'disabled';
      } else if (snapshot.degraded) {
        alertStatus = alertConfig.enabled ? 'active' : 'disabled';
      } else {
        await this.state.storage.transaction(txn =>
          txn.delete(CLEANUP_AGGREGATE_ALERT_STATE_KEY)
        );
        alertState = undefined;
        alertStatus = alertConfig.enabled ? 'recovered' : 'disabled';
      }
    }
    if (
      alertConfig.enabled
      && command.action === 'failed'
      && snapshot.rolling_incident_count >= alertConfig.threshold
      && !alertState
    ) {
      const firedAt = command.occurredAt;
      const windowStartedAt = firedAt;
      const windowExpiresAt = new Date(now + alertConfig.windowMs).toISOString();
      const delivered = await this.sendAggregateCleanupAlert(
        snapshot.rolling_incident_count,
        alertConfig.threshold,
        windowStartedAt,
        windowExpiresAt,
        firedAt,
      );
      if (delivered) {
        alertState = { window_started_at: windowStartedAt, last_fired_at: firedAt };
        await this.state.storage.transaction(txn =>
          txn.put(CLEANUP_AGGREGATE_ALERT_STATE_KEY, alertState as CleanupAggregateAlertState)
        );
        alertStatus = 'active';
      } else {
        alertStatus = 'delivery_failed';
      }
    }
    snapshot.alert = {
      enabled: alertConfig.enabled,
      threshold: alertConfig.threshold,
      window_minutes: alertConfig.windowMs / 60_000,
      state: alertStatus,
      last_fired_at: alertState?.last_fired_at ?? previous?.alert?.last_fired_at ?? null,
      window_expires_at: alertState
        ? new Date(Date.parse(alertState.window_started_at) + alertConfig.windowMs).toISOString()
        : null,
    };
    await this.env?.RATE_LIMIT_KV?.put(
      RATE_LIMIT_CLEANUP_HEALTH_KEY,
      JSON.stringify(snapshot),
    );
    return Response.json(snapshot);
  }

  async fetch(request: Request): Promise<Response> {
    if (request.method !== 'POST') {
      return Response.json({ error: 'Method not allowed' }, { status: 405 });
    }
    if (new URL(request.url).pathname === '/cleanup-health') {
      return this.state.blockConcurrencyWhile(
        () => this.updateCleanupHealthAggregate(request),
      );
    }

    let command: RateLimitCommand;
    try {
      command = await request.json<RateLimitCommand>();
    } catch {
      return Response.json({ error: 'Invalid request' }, { status: 400 });
    }

    if (!Number.isSafeInteger(command.limit) || command.limit < 1
      || !Number.isFinite(command.resetAt) || command.resetAt <= Date.now()) {
      return Response.json({ error: 'Invalid rate-limit command' }, { status: 400 });
    }

    const result = await this.state.storage.transaction(async (txn) => {
      const count = await txn.get<number>('count') ?? 0;
      if (count >= command.limit) {
        return {
          allowed: false,
          remaining: 0,
          resetAt: command.resetAt,
        } satisfies RateLimitResult;
      }

      const nextCount = count + 1;
      await txn.put('count', nextCount);
      return {
        allowed: true,
        remaining: command.limit - nextCount,
        resetAt: command.resetAt,
      } satisfies RateLimitResult;
    });
    await this.state.storage.setAlarm(command.resetAt);

    return Response.json(result);
  }

  async alarm(): Promise<void> {
    // Install a recovery alarm before deleting state. If this handler fails
    // after that point, the bucket gets another bounded cleanup attempt even
    // when the original alarm delivery is not retried.
    await this.state.storage.setAlarm(Date.now() + CLEANUP_RECOVERY_DELAY_MS);
    const [failureCount = 0, alertedAt] = await Promise.all([
      this.state.storage.get<number>(CLEANUP_FAILURE_COUNT_KEY),
      this.state.storage.get<number>(CLEANUP_ALERTED_AT_KEY),
    ]);
    const activeIncidentToken = await this.state.storage.get<string>(
      CLEANUP_INCIDENT_TOKEN_KEY,
    );

    try {
      await this.state.storage.deleteAll();
      await this.state.storage.deleteAlarm();
      if (failureCount >= CLEANUP_FAILURE_ALERT_THRESHOLD && activeIncidentToken) {
        const recoveredAt = new Date().toISOString();
        await this.reportCleanupIncident({
          action: 'recovered',
          incidentToken: activeIncidentToken,
          occurredAt: recoveredAt,
        });
        console.info(JSON.stringify({
          event: 'rate_limit_cleanup_recovered',
          previousFailures: failureCount,
        }));
      }
    } catch (error) {
      const nextFailureCount = failureCount + 1;
      const now = Date.now();
      const shouldAlert = nextFailureCount >= CLEANUP_FAILURE_ALERT_THRESHOLD
        && (alertedAt === undefined || now - alertedAt >= CLEANUP_FAILURE_ALERT_DEDUP_MS);

      await this.state.storage.put(CLEANUP_FAILURE_COUNT_KEY, nextFailureCount);
      // deleteAll/deleteAlarm may already have succeeded before publishing the
      // recovery transition failed. Preserve the opaque incident token and a
      // retry alarm so the aggregate can eventually observe the recovery.
      if (activeIncidentToken) {
        await this.state.storage.put(CLEANUP_INCIDENT_TOKEN_KEY, activeIncidentToken);
        if (alertedAt !== undefined) {
          await this.state.storage.put(CLEANUP_ALERTED_AT_KEY, alertedAt);
        }
        await this.state.storage.setAlarm(now + CLEANUP_RECOVERY_DELAY_MS);
      }
      if (shouldAlert) {
        await this.state.storage.put(CLEANUP_ALERTED_AT_KEY, now);
        const incidentToken = activeIncidentToken ?? crypto.randomUUID();
        if (!activeIncidentToken) {
          await this.state.storage.put(CLEANUP_INCIDENT_TOKEN_KEY, incidentToken);
        }
        await this.reportCleanupIncident({
          action: 'failed',
          incidentToken,
          occurredAt: new Date(now).toISOString(),
        });
        console.error(JSON.stringify({
          event: 'rate_limit_cleanup_repeated_failure',
          failures: nextFailureCount,
          retryInSeconds: CLEANUP_RECOVERY_DELAY_MS / 1000,
        }));
      }
      throw error;
    }
  }
}
