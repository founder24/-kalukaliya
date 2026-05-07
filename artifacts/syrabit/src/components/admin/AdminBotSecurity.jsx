import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Shield, Bot, AlertTriangle, RefreshCw, Loader2,
  Hash, Globe, Clock, TrendingUp, Eye, Ban, Unlock,
  Settings, Bell, Mail, Link2, Save, Check, RotateCcw,
  Calendar, Filter, CheckCheck, History, Send, Inbox, Smartphone,
} from 'lucide-react';
import {
  LineChart, Line, BarChart, Bar, AreaChart, Area, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts';
import { adminGetSpoofedBots, adminGetBlockedIps, adminGetBlockTrends, adminBlockIp, adminUnblockIp, adminGetAlertSettings, adminUpdateAlertSettings, adminTestAlertDelivery, adminGetTtlMonitor, adminGetCollectionSizeHistory, adminGetAlerts, adminAcknowledgeAlert, adminAcknowledgeAllAlerts, adminBackfillThresholds, adminSendReviewPromptWeeklyDigest, adminGetAlertCooldowns, adminReleaseAlertCooldown, adminGetBotTraffic } from '@/utils/api';
import { Database, Activity, CheckCircle2, XCircle } from 'lucide-react';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
function GlassCard({ children, className = '' }) {
  return (
    <div className={`relative rounded-2xl overflow-hidden bg-white border border-gray-200 shadow-sm ${className}`}>
      <div className="relative">{children}</div>
    </div>
  );
}

function StatCard({ label, value, icon: Icon, color, pulse }) {
  return (
    <div className="relative rounded-2xl p-5 overflow-hidden bg-white border border-gray-200 shadow-sm">
      {pulse && (
        <span className="absolute top-3 right-3 flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: color }} />
          <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: color }} />
        </span>
      )}
      <div className="flex items-center justify-between mb-3">
        <p className="text-gray-500 text-xs font-medium tracking-wide uppercase">{label}</p>
        <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
          <Icon size={16} style={{ color }} />
        </div>
      </div>
      <p className="text-2xl font-bold text-gray-900 tracking-tight">
        {typeof value === 'number' ? value.toLocaleString() : (value ?? '—')}
      </p>
    </div>
  );
}

// Task #418: presents per-channel last-success/last-error timestamps next to a
// "Test alert delivery" button so admins can verify their Slack/email/push
// integrations actually work without waiting for a real incident.
function _formatRelative(iso) {
  if (!iso) return 'never';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return 'never';
  const diff = Date.now() - t;
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  return `${d}d ago`;
}

const _CHANNEL_LABELS = {
  email: { label: 'Email (Resend)', icon: Mail },
  webhook: { label: 'Webhook (Slack/Discord)', icon: Link2 },
  persisted: { label: 'Persisted alert (dashboard)', icon: Inbox },
  push: { label: 'Browser push', icon: Smartphone },
};

function ChannelStatusRow({ channel, entry, outcome }) {
  const meta = _CHANNEL_LABELS[channel] || { label: channel, icon: Bell };
  const Icon = meta.icon;
  const lastSuccess = entry?.last_success_at;
  const lastError = entry?.last_error;
  const lastAttempt = entry?.last_attempt_at;
  let statusColor = 'bg-gray-100 text-gray-500';
  let statusLabel = 'No attempts yet';
  if (outcome) {
    if (outcome.ok) {
      statusColor = 'bg-emerald-50 text-emerald-700';
      statusLabel = 'Test OK';
    } else if (outcome.attempted) {
      statusColor = 'bg-red-50 text-red-700';
      statusLabel = `Test failed: ${outcome.error || 'unknown error'}`;
    } else if (outcome.skipped_reason) {
      statusColor = 'bg-amber-50 text-amber-700';
      statusLabel = `Skipped: ${outcome.skipped_reason}`;
    }
  } else if (lastSuccess && !lastError) {
    statusColor = 'bg-emerald-50 text-emerald-700';
    statusLabel = `Last success ${_formatRelative(lastSuccess)}`;
  } else if (lastError) {
    statusColor = 'bg-red-50 text-red-700';
    statusLabel = `Last error: ${lastError}`;
  } else if (lastAttempt) {
    statusColor = 'bg-amber-50 text-amber-700';
    statusLabel = `Attempted ${_formatRelative(lastAttempt)}, no success recorded`;
  }
  return (
    <div className="flex items-start justify-between gap-3 py-2 border-b border-gray-100 last:border-0">
      <div className="flex items-start gap-2 min-w-0">
        <Icon size={12} className="text-gray-400 mt-0.5 shrink-0" />
        <div className="min-w-0">
          <p className="text-[11px] font-medium text-gray-700">{meta.label}</p>
          <p className="text-[10px] text-gray-400 mt-0.5">
            Last success: {lastSuccess ? `${_formatRelative(lastSuccess)} (${new Date(lastSuccess).toLocaleString()})` : 'never'}
          </p>
        </div>
      </div>
      <span className={`text-[10px] px-2 py-0.5 rounded-full whitespace-nowrap ${statusColor}`}>{statusLabel}</span>
    </div>
  );
}

function ChannelStatusPanel({ status, testing, testResult, testError, onTest }) {
  const channels = ['email', 'webhook', 'persisted', 'push'];
  return (
    <div className="mt-5 border-t border-gray-100 pt-4">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h4 className="text-xs font-medium text-gray-700 flex items-center gap-1.5">
            <Send size={12} className="text-gray-400" />
            Delivery Status
          </h4>
          <p className="text-[10px] text-gray-400 mt-0.5">
            Sends a synthetic <code>hydrate_failure_spike</code> alert to every configured channel and reports per-channel success/failure.
          </p>
        </div>
        <button
          onClick={onTest}
          disabled={testing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium border border-violet-200 text-violet-700 bg-white hover:bg-violet-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {testing ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
          {testing ? 'Sending test...' : 'Test alert delivery'}
        </button>
      </div>
      {testError && (
        <div className="flex items-center gap-2 text-[11px] text-red-600 bg-red-50 rounded-lg px-2.5 py-1.5 mb-2">
          <AlertTriangle size={11} />
          {String(testError)}
        </div>
      )}
      {testResult?.skipped_cooldown && (
        <div className="flex items-center gap-2 text-[11px] text-amber-700 bg-amber-50 rounded-lg px-2.5 py-1.5 mb-2">
          <AlertTriangle size={11} />
          Test was rate-limited by cooldown. Try again shortly.
        </div>
      )}
      <div className="rounded-lg border border-gray-100 px-3">
        {channels.map((c) => (
          // Task #434 — anchor each channel row so the dashboard tile's
          // deep-link can scroll/highlight the right one.
          <div key={c} id={`alert-channel-${c}`} data-testid={`alert-channel-${c}`}>
            <ChannelStatusRow
              channel={c}
              entry={status?.[c]}
              outcome={testResult?.[c]}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

// Task #447 / Task #484 — config-driven threshold fields rendered by
// the Alert Settings panel. Defining them as a single list (instead
// of hand-coded inputs) keeps adding new tunable thresholds cheap:
// append a row here and the UI, form state, validator, save payload,
// and reset-to-defaults all pick it up automatically. The keys must
// match `_ALERT_THRESHOLDS_DEFAULT` in
// `artifacts/syrabit-backend/metrics.py`.
//
// Task #484 migrated the four originally hand-coded thresholds
// (spoof_rpm, auto_block_threshold, auto_block_expiry_hours,
// collection_growth_per_day) onto this same iterator so every entry
// shares the same default-text + range validation behaviour. The
// optional `secondaryUnit(value)` callback supports the expiry row's
// "(7d)" / "(permanent)" hint that hangs off the unit label, and the
// optional `formatDefault(value)` callback supports the same row's
// "168h = 7d" default-display.
export const ALERT_THRESHOLD_FIELDS = [
  {
    key: 'spoof_rpm',
    label: 'Spoof RPM Alert Threshold',
    help: 'An alert fires when spoofed bot requests per minute exceed this value.',
    unit: 'requests/min',
    default: 50,
    integer: true,
    min: 1,
    max: 10000,
    step: 1,
    width: 'w-32',
  },
  {
    key: 'auto_block_threshold',
    label: 'Auto-Block Threshold (24h)',
    help: 'IPs exceeding this many spoofing attempts in 24 hours are automatically blocked. Set to 0 to disable auto-blocking.',
    unit: 'attempts/24h',
    default: 100,
    integer: true,
    min: 0,
    max: 100000,
    step: 1,
    width: 'w-32',
  },
  {
    key: 'auto_block_expiry_hours',
    label: 'Auto-Block Expiry',
    help: 'Auto-blocked IPs are automatically unblocked after this many hours. Set to 0 for permanent blocks.',
    unit: 'hours',
    default: 168,
    integer: false,
    min: 0,
    max: 8760,
    step: 1,
    width: 'w-32',
    formatDefault: (v) => `${v}h = ${Math.round(Number(v) / 24)}d`,
    secondaryUnit: (value) => (Number(value) > 0 ? `(${Math.round(Number(value) / 24)}d)` : '(permanent)'),
  },
  {
    key: 'collection_growth_per_day',
    label: 'Collection Growth Alert (docs/day)',
    help: 'Alert when daily collection growth exceeds this number. Set to 0 to disable.',
    unit: 'docs/day',
    default: 500,
    integer: true,
    min: 0,
    max: 1000000,
    step: 1,
    width: 'w-32',
  },
  {
    key: 'memory_brain_failure_rate_pct',
    label: 'Memory-Brain Failure Rate',
    help: 'Pages on-call when the per-worker memory_brain rolling-window failure rate exceeds this percentage (Voyage embed/upsert path). Set to 0 to disable.',
    unit: '% failures',
    default: 25.0,
    integer: false,
    min: 0,
    max: 100,
    step: 0.5,
    width: 'w-32',
  },
  {
    key: 'memory_brain_failure_min_sample',
    label: 'Memory-Brain Min Sample',
    help: 'Minimum number of memory_brain attempts in the rolling window before the failure-rate alert can fire — guards against tiny samples flipping the alert on a single bad write.',
    unit: 'attempts',
    default: 20,
    integer: true,
    min: 1,
    max: 10000,
    step: 1,
    width: 'w-32',
  },
];

// Task #531 — module-level so it's unit-testable without mounting the
// component. Maps a FastAPI/axios error into a {field: msg} dict whose
// keys are drawn from ALERT_THRESHOLD_FIELDS plus the email / webhook /
// digest-emails entries — so any new threshold added to the config row
// (e.g. memory_brain_*) automatically gets per-field error surfacing
// instead of falling through to the generic banner.
export function parseAlertSettingsBackendError(err) {
  const resp = err?.response;
  if (!resp) return { general: 'Network error — could not reach server' };
  const detail = resp.data?.detail;
  const thresholdKeys = new Set(ALERT_THRESHOLD_FIELDS.map(f => f.key));
  const knownFields = new Set([...thresholdKeys, 'email', 'webhook_url', 'review_prompt_digest_emails']);
  const stringMatchers = [
    ...ALERT_THRESHOLD_FIELDS.map(f => ({ key: f.key, needles: [f.key.toLowerCase()] })),
    { key: 'email', needles: ['email'] },
    { key: 'webhook_url', needles: ['webhook'] },
  ];
  if (resp.status === 422 && Array.isArray(detail)) {
    const errors = {};
    for (const item of detail) {
      const loc = item.loc || [];
      const field = loc[loc.length - 1] || 'general';
      if (knownFields.has(field)) {
        errors[field] = item.msg || 'Invalid value';
      } else {
        errors.general = item.msg || 'Validation error';
      }
    }
    return Object.keys(errors).length ? errors : { general: 'Validation failed' };
  }
  if (typeof detail === 'string') {
    const lower = detail.toLowerCase();
    for (const m of stringMatchers) {
      if (m.needles.some(n => lower.includes(n))) {
        return { [m.key]: detail };
      }
    }
    return { general: detail };
  }
  return { general: detail || `Server error (${resp.status})` };
}

function _validateThresholdFieldValue(field, value) {
  if (value === '' || value === null || value === undefined) {
    return `${field.label} is required`;
  }
  const num = Number(value);
  if (Number.isNaN(num)) return 'Must be a number';
  if (num < field.min) return `Must be ≥ ${field.min}`;
  if (num > field.max) return `Must be ≤ ${field.max.toLocaleString()}`;
  if (field.integer && !Number.isInteger(num)) return 'Must be a whole number';
  return null;
}

function AlertThresholdPanel({ adminToken, navContext }) {
  const [settings, setSettings] = useState(null);
  const [form, setForm] = useState({
    email: '',
    webhook_url: '',
    seo_slack_enabled: true,
    hydrate_slack_enabled: true,
    review_prompt_digest_emails: '',
    // Task #484 — every threshold (legacy + memory_brain) lives in
    // ALERT_THRESHOLD_FIELDS and is seeded from its config default.
    ...Object.fromEntries(ALERT_THRESHOLD_FIELDS.map(f => [f.key, f.default])),
  });
  // Task #660: dedicated "send me a test" state for the weekly
  // review-prompt digest, distinct from the synthetic-alert
  // test-delivery flow above so admins can verify the digest send path
  // end-to-end without firing a synthetic CTR-low alert.
  const [digestSending, setDigestSending] = useState(false);
  const [digestResult, setDigestResult] = useState(null);
  const [digestError, setDigestError] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [loadingSettings, setLoadingSettings] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [settingsError, setSettingsError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});
  const [expanded, setExpanded] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillResult, setBackfillResult] = useState(null);
  const [channelStatus, setChannelStatus] = useState(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testError, setTestError] = useState(null);

  // Task #434 — when the dashboard's push-channel tile deep-links into
  // Bot Security, navContext arrives as { panel: 'alert-settings',
  // channel: 'push' }. Auto-expand the panel.
  useEffect(() => {
    if (navContext?.panel === 'alert-settings') {
      setExpanded(true);
    }
  }, [navContext]);

  // Mount-aware scroll/highlight: only runs once the panel body is
  // actually rendered (loadingSettings === false, expanded === true,
  // channelStatus available). Uses a short bounded poll because
  // ChannelStatusPanel renders inside the expanded body and we need
  // to wait for the row DOM node to exist before scrolling.
  const deepLinkAppliedRef = useRef(false);
  useEffect(() => {
    if (deepLinkAppliedRef.current) return;
    if (navContext?.panel !== 'alert-settings') return;
    if (!navContext?.channel) return;
    if (loadingSettings || !expanded) return;

    let cancelled = false;
    let attempts = 0;
    const tryScroll = () => {
      if (cancelled) return;
      const el = document.getElementById(`alert-channel-${navContext.channel}`);
      if (el && typeof el.scrollIntoView === 'function') {
        deepLinkAppliedRef.current = true;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('ring-2', 'ring-violet-400', 'rounded-lg');
        setTimeout(() => {
          el.classList.remove('ring-2', 'ring-violet-400', 'rounded-lg');
        }, 2500);
        return;
      }
      attempts += 1;
      if (attempts < 40) { // ~4s budget at 100ms intervals
        setTimeout(tryScroll, 100);
      }
    };
    // Wait one frame so React commits the expanded body before we look.
    const raf = requestAnimationFrame(tryScroll);
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };
  }, [navContext, loadingSettings, expanded, channelStatus]);

  useEffect(() => {
    (async () => {
      try {
        const res = await adminGetAlertSettings(adminToken);
        const d = res.data;
        setSettings(d);
        setDefaults(d.defaults);
        setChannelStatus(d.channel_status || null);
        setForm({
          email: d.notification_channels?.email ?? '',
          webhook_url: d.notification_channels?.webhook_url ?? '',
          seo_slack_enabled: d.notification_channels?.seo_slack_enabled ?? true,
          hydrate_slack_enabled: d.notification_channels?.hydrate_slack_enabled ?? true,
          // Task #660: render the persisted list as a comma-separated
          // string in the textarea — matches how admins typically paste
          // multiple addresses and round-trips back through the same
          // parser on save.
          review_prompt_digest_emails: Array.isArray(d.notification_channels?.review_prompt_digest_emails)
            ? d.notification_channels.review_prompt_digest_emails.join(', ')
            : (d.notification_channels?.review_prompt_digest_emails ?? ''),
          // Task #484 — every threshold (legacy + memory_brain) loads
          // through the same iterator. Falls back to defaults from the
          // backend payload, then to the field's hard-coded default so
          // a brand-new install with no api_config row still renders.
          ...Object.fromEntries(ALERT_THRESHOLD_FIELDS.map(f => [
            f.key,
            d.thresholds?.[f.key] ?? d.defaults?.thresholds?.[f.key] ?? f.default,
          ])),
        });
      } catch {
        setSettingsError('Failed to load alert settings');
      } finally {
        setLoadingSettings(false);
      }
    })();
  }, [adminToken]);

  const validateField = (field, value) => {
    // Task #484 — every threshold (legacy + memory_brain) is validated
    // through the shared range/integer validator. Check this *before*
    // the email/webhook branches so config-driven fields short-circuit
    // first.
    const cfg = ALERT_THRESHOLD_FIELDS.find(f => f.key === field);
    if (cfg) return _validateThresholdFieldValue(cfg, value);
    if (field === 'email') {
      if (!value) return null;
      if (!value.includes('@') || !value.includes('.')) return 'Enter a valid email (e.g. admin@example.com)';
      return null;
    }
    if (field === 'webhook_url') {
      if (!value) return null;
      if (!value.startsWith('http://') && !value.startsWith('https://')) return 'Must start with http:// or https://';
      try { new URL(value); } catch { return 'Enter a valid URL'; }
      return null;
    }
    if (field === 'review_prompt_digest_emails') {
      if (!value) return null;
      const parts = String(value).split(',').map(p => p.trim()).filter(Boolean);
      for (const p of parts) {
        if (!p.includes('@') || !p.includes('.')) {
          return `Invalid email: ${p}`;
        }
      }
      return null;
    }
    return null;
  };

  const handleFieldChange = (field, value) => {
    setForm(prev => ({ ...prev, [field]: value }));
    const err = validateField(field, value);
    setFieldErrors(prev => ({ ...prev, [field]: err }));
    if (settingsError) setSettingsError(null);
  };

  const parseBackendError = parseAlertSettingsBackendError;

  const handleSave = async () => {
    const errors = {};
    errors.email = validateField('email', form.email);
    errors.webhook_url = validateField('webhook_url', form.webhook_url);
    errors.review_prompt_digest_emails = validateField('review_prompt_digest_emails', form.review_prompt_digest_emails);
    // Task #484 — validate every threshold (legacy + memory_brain)
    // through the same iterator.
    for (const f of ALERT_THRESHOLD_FIELDS) {
      errors[f.key] = validateField(f.key, form[f.key]);
    }
    const cleaned = {};
    for (const [k, v] of Object.entries(errors)) { if (v) cleaned[k] = v; }
    setFieldErrors(cleaned);
    if (Object.keys(cleaned).length) {
      setSettingsError(null);
      setSaving(false);
      return;
    }
    setSaving(true);
    setSettingsError(null);
    setSaved(false);
    try {
      await adminUpdateAlertSettings(adminToken, {
        thresholds: {
          ...settings?.thresholds,
          // Task #484 — persist every threshold (legacy + memory_brain)
          // through the same iterator.
          ...Object.fromEntries(ALERT_THRESHOLD_FIELDS.map(f => [f.key, Number(form[f.key])])),
        },
        expiration: settings?.expiration || {},
        notification_channels: {
          email: form.email.trim(),
          webhook_url: form.webhook_url.trim(),
          seo_slack_enabled: !!form.seo_slack_enabled,
          hydrate_slack_enabled: !!form.hydrate_slack_enabled,
          // Task #660: send the digest list as an array so the backend
          // doesn't have to re-split (it accepts both, but the array
          // form has no ambiguity around literal commas in a name).
          review_prompt_digest_emails: String(form.review_prompt_digest_emails || '')
            .split(',')
            .map(p => p.trim())
            .filter(Boolean),
        },
      });
      setFieldErrors({});
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      const parsed = parseBackendError(err);
      const { general, ...fields } = parsed;
      if (Object.keys(fields).length) setFieldErrors(prev => ({ ...prev, ...fields }));
      if (general) setSettingsError(general);
      else if (!Object.keys(fields).length) setSettingsError('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  // Task #660: "send me a test now" — exercises the digest send path
  // end-to-end. We post the *current* draft list (parsed from the
  // textarea, before save) so admins can validate a new recipient
  // without having to commit it first.
  const handleSendDigestTest = async () => {
    setDigestSending(true);
    setDigestError(null);
    setDigestResult(null);
    try {
      const drafted = String(form.review_prompt_digest_emails || '')
        .split(',').map(p => p.trim()).filter(Boolean);
      const res = await adminSendReviewPromptWeeklyDigest(adminToken, {
        to: drafted.length ? drafted : null,
      });
      setDigestResult(res.data || null);
    } catch (err) {
      setDigestError(err.response?.data?.detail || err.message || 'Failed to send test digest');
    } finally {
      setDigestSending(false);
    }
  };

  const handleTestDelivery = async () => {
    setTesting(true);
    setTestError(null);
    setTestResult(null);
    try {
      const res = await adminTestAlertDelivery(adminToken);
      setTestResult(res.data?.outcomes || null);
      if (res.data?.channel_status) setChannelStatus(res.data.channel_status);
    } catch (err) {
      setTestError(err.response?.data?.detail || err.message || 'Failed to send test alert');
    } finally {
      setTesting(false);
    }
  };

  const handleReset = () => {
    if (defaults) {
      setForm({
        email: defaults.notification_channels?.email ?? '',
        webhook_url: defaults.notification_channels?.webhook_url ?? '',
        seo_slack_enabled: defaults.notification_channels?.seo_slack_enabled ?? true,
        hydrate_slack_enabled: defaults.notification_channels?.hydrate_slack_enabled ?? true,
        review_prompt_digest_emails: Array.isArray(defaults.notification_channels?.review_prompt_digest_emails)
          ? defaults.notification_channels.review_prompt_digest_emails.join(', ')
          : '',
        // Task #484 — reset every threshold (legacy + memory_brain)
        // back to either the backend-supplied default (preferred) or
        // the field's hard-coded default.
        ...Object.fromEntries(ALERT_THRESHOLD_FIELDS.map(f => [
          f.key,
          defaults.thresholds?.[f.key] ?? f.default,
        ])),
      });
      setFieldErrors({});
      setSettingsError(null);
    }
  };

  const hasErrors = Object.values(fieldErrors).some(Boolean);

  if (loadingSettings) {
    return (
      <GlassCard>
        <div className="p-5 flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" />
          Loading alert settings...
        </div>
      </GlassCard>
    );
  }

  return (
    <GlassCard>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full p-5 flex items-center justify-between hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-amber-50">
            <Bell size={16} className="text-amber-500" />
          </div>
          <div className="text-left">
            <h3 className="text-sm font-semibold text-gray-900">Alert Threshold Controls</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">
              RPM threshold: {form.spoof_rpm} &middot; Auto-block: {Number(form.auto_block_threshold) > 0 ? `${form.auto_block_threshold}/24h` : 'disabled'} &middot; Expiry: {Number(form.auto_block_expiry_hours) > 0 ? `${Math.round(Number(form.auto_block_expiry_hours) / 24)}d` : 'permanent'} &middot; Notifications: {form.email || form.webhook_url ? 'configured' : 'not configured'}
            </p>
          </div>
        </div>
        <Settings size={14} className={`text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />
      </button>

      {expanded && (
        <div className="border-t border-gray-100 p-5 space-y-5">
          {/* Task #484 — every threshold row (legacy spoof_rpm /
              auto_block_* / collection_growth_per_day plus the
              memory_brain_failure_* tunables) is rendered through this
              single iterator. New tunables only need an entry in
              ALERT_THRESHOLD_FIELDS — UI, validator, save payload, and
              reset-to-defaults all pick them up automatically. */}
          {ALERT_THRESHOLD_FIELDS.map((f) => {
            const backendDefault = defaults?.thresholds?.[f.key] ?? f.default;
            const defaultDisplay = f.formatDefault
              ? f.formatDefault(backendDefault)
              : `${backendDefault}${f.unit ? ` ${f.unit}` : ''}`;
            return (
              <div key={f.key}>
                <label className="block text-xs font-medium text-gray-700 mb-1.5">
                  {f.label}
                </label>
                <p className="text-[10px] text-gray-400 mb-2">
                  {f.help} (default: {defaultDisplay})
                </p>
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    min={f.min}
                    max={f.max}
                    step={f.step}
                    value={form[f.key]}
                    onChange={(e) => handleFieldChange(f.key, e.target.value)}
                    data-testid={`alert-threshold-${f.key}`}
                    className={`${f.width || 'w-32'} text-sm border rounded-lg px-3 py-2 bg-white text-gray-900 focus:outline-none focus:ring-2 ${
                      fieldErrors[f.key]
                        ? 'border-red-300 focus:ring-red-200 focus:border-red-300'
                        : 'border-gray-200 focus:ring-violet-200 focus:border-violet-300'
                    }`}
                  />
                  {f.unit && (
                    <span className="text-xs text-gray-400">
                      {f.unit}{f.secondaryUnit ? ` ${f.secondaryUnit(form[f.key])}` : ''}
                    </span>
                  )}
                </div>
                {fieldErrors[f.key] && (
                  <p className="text-[11px] text-red-500 mt-1">{fieldErrors[f.key]}</p>
                )}
              </div>
            );
          })}

          <div className="border-t border-gray-100 pt-5">
            <h4 className="text-xs font-medium text-gray-700 mb-3 flex items-center gap-1.5">
              <Bell size={12} className="text-gray-400" />
              Notification Channels
            </h4>
            <div className="space-y-4">
              <div>
                <label className="block text-[11px] font-medium text-gray-600 mb-1 flex items-center gap-1.5">
                  <Mail size={11} className="text-gray-400" />
                  Alert Email
                </label>
                <input
                  type="email"
                  placeholder="admin@example.com"
                  value={form.email}
                  onChange={(e) => handleFieldChange('email', e.target.value)}
                  className={`w-full text-sm border rounded-lg px-3 py-2 bg-white text-gray-900 placeholder-gray-300 focus:outline-none focus:ring-2 ${
                    fieldErrors.email
                      ? 'border-red-300 focus:ring-red-200 focus:border-red-300'
                      : 'border-gray-200 focus:ring-violet-200 focus:border-violet-300'
                  }`}
                />
                {fieldErrors.email ? (
                  <p className="text-[11px] text-red-500 mt-1">{fieldErrors.email}</p>
                ) : (
                  <p className="text-[10px] text-gray-400 mt-1">
                    Receives email alerts via Resend when thresholds are exceeded
                  </p>
                )}
              </div>
              <div>
                <label className="block text-[11px] font-medium text-gray-600 mb-1 flex items-center gap-1.5">
                  <Link2 size={11} className="text-gray-400" />
                  Webhook URL
                </label>
                <input
                  type="url"
                  placeholder="https://hooks.slack.com/services/..."
                  value={form.webhook_url}
                  onChange={(e) => handleFieldChange('webhook_url', e.target.value)}
                  className={`w-full text-sm border rounded-lg px-3 py-2 bg-white text-gray-900 placeholder-gray-300 focus:outline-none focus:ring-2 ${
                    fieldErrors.webhook_url
                      ? 'border-red-300 focus:ring-red-200 focus:border-red-300'
                      : 'border-gray-200 focus:ring-violet-200 focus:border-violet-300'
                  }`}
                />
                {fieldErrors.webhook_url ? (
                  <p className="text-[11px] text-red-500 mt-1">{fieldErrors.webhook_url}</p>
                ) : (
                  <p className="text-[10px] text-gray-400 mt-1">
                    Slack, Discord, or generic webhook endpoint for alert notifications
                  </p>
                )}
              </div>
            </div>
            <label className="mt-3 flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={!!form.seo_slack_enabled}
                onChange={(e) => handleFieldChange('seo_slack_enabled', e.target.checked)}
                className="mt-0.5 w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
              />
              <span>
                <span className="text-[11px] font-medium text-gray-700">Send SEO health alerts to Slack/webhook</span>
                <span className="block text-[10px] text-gray-400">
                  When enabled, <code>seo_health_degraded</code> and <code>seo_url_spike</code> alerts post a rich Slack message (severity, sitemap counts, SEO Manager link) to the webhook above. Email and browser push are unaffected.
                </span>
              </span>
            </label>
            <label className="mt-2 flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={!!form.hydrate_slack_enabled}
                onChange={(e) => handleFieldChange('hydrate_slack_enabled', e.target.checked)}
                className="mt-0.5 w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
              />
              <span>
                <span className="text-[11px] font-medium text-gray-700">Send stale-build alerts to Slack/webhook</span>
                <span className="block text-[10px] text-gray-400">
                  When enabled, <code>hydrate_failure_spike</code> and <code>hydrate_recovery_low</code> alerts post a rich Slack message to the webhook above. Email, persisted alerts, and browser push are unaffected.
                </span>
              </span>
            </label>

            {/* Task #660: dedicated recipient list + "send me a test
                now" button for the Monday review-prompt weekly digest.
                Distinct from the incident-alert email above so ops can
                fan-out the digest to product / growth / support without
                spamming everyone with every CTR-low alert. */}
            <div className="mt-4 border-t border-dashed border-gray-200 pt-4">
              <label className="block text-[11px] font-medium text-gray-600 mb-1 flex items-center gap-1.5">
                <Mail size={11} className="text-gray-400" />
                Review-prompt weekly digest recipients
              </label>
              <textarea
                rows={2}
                placeholder="ops@example.com, growth@example.com"
                value={form.review_prompt_digest_emails}
                onChange={(e) => handleFieldChange('review_prompt_digest_emails', e.target.value)}
                className={`w-full text-sm border rounded-lg px-3 py-2 bg-white text-gray-900 placeholder-gray-300 focus:outline-none focus:ring-2 ${
                  fieldErrors.review_prompt_digest_emails
                    ? 'border-red-300 focus:ring-red-200 focus:border-red-300'
                    : 'border-gray-200 focus:ring-violet-200 focus:border-violet-300'
                }`}
              />
              {fieldErrors.review_prompt_digest_emails ? (
                <p className="text-[11px] text-red-500 mt-1">{fieldErrors.review_prompt_digest_emails}</p>
              ) : (
                <p className="text-[10px] text-gray-400 mt-1">
                  Comma-separated. Receives the Monday review-prompt summary email. Leave blank to fall back to the alert email above.
                </p>
              )}
              <div className="mt-2 flex items-center gap-2 flex-wrap">
                <button
                  type="button"
                  onClick={handleSendDigestTest}
                  disabled={digestSending || !!fieldErrors.review_prompt_digest_emails}
                  className="text-[11px] font-medium px-3 py-1.5 rounded-lg border border-violet-200 bg-violet-50 text-violet-700 hover:bg-violet-100 disabled:opacity-60 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
                >
                  {digestSending ? <Loader2 size={11} className="animate-spin" /> : <Mail size={11} />}
                  {digestSending ? 'Sending test…' : 'Send me a test now'}
                </button>
                {digestResult && (
                  digestResult.sent ? (
                    <span className="text-[11px] text-emerald-700 inline-flex items-center gap-1">
                      <CheckCircle2 size={11} />
                      Sent to {(digestResult.recipients || []).join(', ') || digestResult.to}
                    </span>
                  ) : (
                    <span className="text-[11px] text-amber-700 inline-flex items-center gap-1">
                      <AlertTriangle size={11} />
                      Not sent — {digestResult.reason || 'unknown'}
                      {digestResult.recipients && digestResult.recipients.length
                        ? ` (would target: ${digestResult.recipients.join(', ')})`
                        : ''}
                    </span>
                  )
                )}
                {digestError && (
                  <span className="text-[11px] text-red-600 inline-flex items-center gap-1">
                    <AlertTriangle size={11} />
                    {digestError}
                  </span>
                )}
              </div>
            </div>

            {/* Task #418: Per-channel last-success status + test delivery button. */}
            <ChannelStatusPanel
              status={channelStatus}
              testing={testing}
              testResult={testResult}
              testError={testError}
              onTest={handleTestDelivery}
            />
          </div>

          {settingsError && (
            <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
              <AlertTriangle size={12} />
              {settingsError}
            </div>
          )}

          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleSave}
              disabled={saving || hasErrors}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-violet-600 text-white hover:bg-violet-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? <Loader2 size={12} className="animate-spin" /> : saved ? <Check size={12} /> : <Save size={12} />}
              {saving ? 'Saving...' : saved ? 'Saved' : 'Save Changes'}
            </button>
            <button
              onClick={handleReset}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-gray-500 hover:bg-gray-100 transition-colors"
            >
              <RotateCcw size={12} />
              Reset to Defaults
            </button>
          </div>

          <div className="border-t border-gray-100 pt-5">
            <h4 className="text-xs font-medium text-gray-700 mb-1.5 flex items-center gap-1.5">
              <Database size={12} className="text-gray-400" />
              Backfill Old Alerts
            </h4>
            <p className="text-[10px] text-gray-400 mb-3">
              Adds threshold context (metric, configured limit, actual value) to older alerts that were created before threshold tracking was added. Safe to run multiple times.
            </p>
            <div className="flex items-center gap-3">
              <button
                onClick={async () => {
                  setBackfilling(true);
                  setBackfillResult(null);
                  try {
                    const res = await adminBackfillThresholds(adminToken);
                    setBackfillResult(res.data);
                  } catch (err) {
                    setBackfillResult({ error: err.response?.data?.detail || 'Backfill failed' });
                  } finally {
                    setBackfilling(false);
                  }
                }}
                disabled={backfilling}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {backfilling ? <Loader2 size={12} className="animate-spin" /> : <History size={12} />}
                {backfilling ? 'Running...' : 'Backfill Old Alerts'}
              </button>
              {backfillResult && !backfillResult.error && (
                <span className="text-[11px] text-emerald-600 flex items-center gap-1">
                  <Check size={11} />
                  {backfillResult.updated > 0
                    ? `Updated ${backfillResult.updated} of ${backfillResult.total} alerts${backfillResult.skipped > 0 ? ` (${backfillResult.skipped} skipped)` : ''}`
                    : 'All alerts already have threshold data'}
                </span>
              )}
              {backfillResult?.error && (
                <span className="text-[11px] text-red-500 flex items-center gap-1">
                  <AlertTriangle size={11} />
                  {backfillResult.error}
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </GlassCard>
  );
}

function TtlMonitorPanel({ adminToken }) {
  const [ttlData, setTtlData] = useState(null);
  const [ttlLoading, setTtlLoading] = useState(true);
  const [ttlError, setTtlError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [sizeHistory, setSizeHistory] = useState([]);
  const [sizeGrowthRate, setSizeGrowthRate] = useState(null);
  const [historyDays, setHistoryDays] = useState(90);

  const fetchTtl = useCallback(async () => {
    setTtlLoading(true);
    setTtlError(null);
    try {
      const res = await adminGetTtlMonitor(adminToken);
      setTtlData(res.data);
    } catch (err) {
      setTtlError(err.response?.data?.detail || 'Failed to load TTL monitoring data');
    } finally {
      setTtlLoading(false);
    }
  }, [adminToken]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await adminGetCollectionSizeHistory(adminToken, historyDays);
      setSizeHistory(res.data.history || []);
      setSizeGrowthRate(res.data.growth_rate_per_day);
    } catch (err) {
      console.warn('AdminBotSecurity: collection-size history fetch failed:', err);
    }
  }, [adminToken, historyDays]);

  useEffect(() => { fetchTtl(); }, [fetchTtl]);
  useEffect(() => { if (expanded) fetchHistory(); }, [expanded, fetchHistory]);

  const healthColor = ttlData?.health_status === 'healthy' ? '#10b981' : '#f59e0b';
  const HealthIcon = ttlData?.health_status === 'healthy' ? CheckCircle2 : AlertTriangle;

  if (ttlLoading && !ttlData) {
    return (
      <GlassCard>
        <div className="p-5 flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" />
          Loading TTL monitoring data...
        </div>
      </GlassCard>
    );
  }

  if (ttlError && !ttlData) {
    return (
      <GlassCard className="p-6">
        <div className="flex items-center gap-3 text-red-500">
          <XCircle size={18} />
          <p className="text-sm">{ttlError}</p>
        </div>
        <button onClick={fetchTtl} className="mt-3 text-sm text-violet-600 hover:text-violet-800 flex items-center gap-1.5">
          <RefreshCw size={13} /> Retry
        </button>
      </GlassCard>
    );
  }

  if (!ttlData) return null;

  const ageDist = ttlData.age_distribution || [];
  const dailyIngest = ttlData.daily_ingest || [];

  return (
    <GlassCard>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded(!expanded); } }}
        className="w-full p-5 flex items-center justify-between hover:bg-gray-50 transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-teal-50">
            <Database size={16} className="text-teal-500" />
          </div>
          <div className="text-left">
            <h3 className="text-sm font-semibold text-gray-900">TTL Cleanup Monitor</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">
              {ttlData.total_documents?.toLocaleString()} docs &middot; TTL: {ttlData.ttl_index?.ttl_days || 90} days &middot;
              <span style={{ color: healthColor }} className="font-medium ml-1">
                {ttlData.health_status === 'healthy' ? 'Healthy' : 'Needs attention'}
              </span>
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={(e) => { e.stopPropagation(); fetchTtl(); }}
            disabled={ttlLoading}
            className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
          >
            <RefreshCw size={12} className={`text-gray-400 ${ttlLoading ? 'animate-spin' : ''}`} />
          </button>
          <Activity size={14} className={`text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </div>
      </div>

      {expanded && (
        <div className="border-t border-gray-100 p-5 space-y-5">
          <div className="flex items-start gap-3 p-3 rounded-xl" style={{ background: `${healthColor}08`, border: `1px solid ${healthColor}20` }}>
            <HealthIcon size={16} style={{ color: healthColor }} className="mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-xs font-medium" style={{ color: healthColor }}>
                {ttlData.health_status === 'healthy' ? 'TTL Cleanup Healthy' : 'TTL Cleanup Warning'}
              </p>
              <p className="text-[11px] text-gray-500 mt-0.5">{ttlData.health_message}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded-xl border border-gray-100 p-3 bg-gray-50">
              <p className="text-[10px] text-gray-400 uppercase tracking-wider">Total Documents</p>
              <p className="text-lg font-bold text-gray-900 mt-1">{ttlData.total_documents?.toLocaleString()}</p>
            </div>
            <div className="rounded-xl border border-gray-100 p-3 bg-gray-50">
              <p className="text-[10px] text-gray-400 uppercase tracking-wider">TTL Duration</p>
              <p className="text-lg font-bold text-gray-900 mt-1">{ttlData.ttl_index?.ttl_days || 90}d</p>
            </div>
            <div className="rounded-xl border border-gray-100 p-3 bg-gray-50">
              <p className="text-[10px] text-gray-400 uppercase tracking-wider">TTL Index</p>
              <p className="text-lg font-bold text-gray-900 mt-1">{ttlData.ttl_index?.name ? 'Active' : 'Missing'}</p>
            </div>
            <div className="rounded-xl border border-gray-100 p-3 bg-gray-50">
              <p className="text-[10px] text-gray-400 uppercase tracking-wider">String Timestamps</p>
              <p className={`text-lg font-bold mt-1 ${ttlData.string_timestamps_remaining > 0 ? 'text-amber-600' : 'text-gray-900'}`}>
                {ttlData.string_timestamps_remaining?.toLocaleString()}
              </p>
            </div>
          </div>

          {ageDist.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-gray-700 mb-3">Document Age Distribution</h4>
              <div className="space-y-2">
                {ageDist.map((bucket) => {
                  const maxCount = Math.max(...ageDist.map(b => b.count), 1);
                  const pct = (bucket.count / maxCount) * 100;
                  const isOverTtl = bucket.label.includes('past TTL');
                  return (
                    <div key={bucket.label} className="flex items-center gap-3">
                      <span className={`text-[11px] w-32 text-right flex-shrink-0 ${isOverTtl && bucket.count > 0 ? 'text-amber-600 font-medium' : 'text-gray-500'}`}>{bucket.label}</span>
                      <div className="flex-1 h-6 bg-gray-50 rounded-lg overflow-hidden border border-gray-100">
                        <div
                          className="h-full rounded-lg transition-all duration-500"
                          style={{
                            width: `${Math.max(pct, bucket.count > 0 ? 2 : 0)}%`,
                            background: isOverTtl && bucket.count > 0 ? '#f59e0b' : '#8b5cf6',
                          }}
                        />
                      </div>
                      <span className={`text-[11px] font-medium w-14 text-right flex-shrink-0 ${isOverTtl && bucket.count > 0 ? 'text-amber-600' : 'text-gray-600'}`}>
                        {bucket.count.toLocaleString()}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {dailyIngest.length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-gray-700 mb-2">Daily Document Ingest (Last 30 Days)</h4>
              <div style={{ height: 200 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailyIngest}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fill: '#94a3b8' }}
                      tickFormatter={(v) => {
                        const d = new Date(v);
                        return `${d.getMonth() + 1}/${d.getDate()}`;
                      }}
                    />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} allowDecimals={false} />
                    <Tooltip
                      contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e2e8f0' }}
                      labelFormatter={(v) => new Date(v).toLocaleDateString()}
                    />
                    <Bar dataKey="count" fill="#14b8a6" radius={[4, 4, 0, 0]} name="Documents Added" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {sizeHistory.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <h4 className="text-xs font-medium text-gray-700">Collection Size History</h4>
                  {sizeGrowthRate !== null && (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${sizeGrowthRate > 100 ? 'bg-amber-50 text-amber-600' : 'bg-green-50 text-green-600'}`}>
                      {sizeGrowthRate > 0 ? '+' : ''}{sizeGrowthRate}/day
                    </span>
                  )}
                </div>
                <select
                  value={historyDays}
                  onChange={(e) => setHistoryDays(Number(e.target.value))}
                  className="text-[10px] border border-gray-200 rounded-md px-1.5 py-0.5 text-gray-600"
                >
                  <option value={30}>30 days</option>
                  <option value={60}>60 days</option>
                  <option value={90}>90 days</option>
                  <option value={180}>180 days</option>
                  <option value={365}>365 days</option>
                </select>
              </div>
              <div style={{ height: 200 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={sizeHistory}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fill: '#94a3b8' }}
                      tickFormatter={(v) => {
                        const d = new Date(v);
                        return `${d.getMonth() + 1}/${d.getDate()}`;
                      }}
                    />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} allowDecimals={false} />
                    <Tooltip
                      contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e2e8f0' }}
                      labelFormatter={(v) => new Date(v).toLocaleDateString()}
                      formatter={(value) => [value.toLocaleString(), 'Documents']}
                    />
                    <Line type="monotone" dataKey="size" stroke="#8b5cf6" strokeWidth={2} dot={false} name="Collection Size" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {ttlData.checked_at && (
            <p className="text-[10px] text-gray-400 text-right">
              Last checked: {new Date(ttlData.checked_at).toLocaleString()}
            </p>
          )}
        </div>
      )}
    </GlassCard>
  );
}

const ALERT_TYPE_OPTIONS = [
  { label: 'All Types', value: '' },
  { label: 'Spoofed Bot Surge', value: 'spoofed_bot_surge' },
  { label: 'High Error Rate', value: 'high_error_rate' },
  { label: 'High Latency', value: 'high_latency' },
  { label: 'High Fallback Rate', value: 'high_fallback_rate' },
  { label: 'Collection Growth Spike', value: 'collection_growth_spike' },
];

const DATE_RANGE_OPTIONS = [
  { label: 'All Time', value: '' },
  { label: 'Last 24 hours', value: '1' },
  { label: 'Last 7 days', value: '7' },
  { label: 'Last 30 days', value: '30' },
  { label: 'Last 90 days', value: '90' },
];

function _formatCountdown(seconds) {
  if (!seconds || seconds <= 0) return 'expired';
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) {
    const s = seconds % 60;
    return s ? `${m}m ${s}s` : `${m}m`;
  }
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return remM ? `${h}h ${remM}m` : `${h}h`;
}

function _formatCooldownType(type) {
  return (type || 'unknown').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function AlertCooldownsPanel({ adminToken, navContext }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [onlyActive, setOnlyActive] = useState(true);
  const [cooldownWindow, setCooldownWindow] = useState(6 * 3600);
  const [serverNow, setServerNow] = useState(null);
  const [activeCount, setActiveCount] = useState(0);
  const [releasingKey, setReleasingKey] = useState(null);
  const [releaseError, setReleaseError] = useState(null);
  const [tick, setTick] = useState(0);
  // Task #991 — when an admin clicks the dashboard's "N on hold" pill,
  // AdminPage routes them here with navContext.panel === 'alert-cooldowns'.
  // We auto-expand the panel and scroll its container into view so the
  // suppressed list is the first thing they see, mirroring the
  // existing AlertThresholdPanel `panel === 'alert-settings'` flow.
  const containerRef = useRef(null);

  const fetchCooldowns = useCallback(async () => {
    setLoading(true);
    setError(null);
    setReleaseError(null);
    try {
      const res = await adminGetAlertCooldowns(adminToken, { limit: 200, only_active: onlyActive });
      const fetched = res.data?.cooldowns || [];
      setRows(fetched);
      setActiveCount(res.data?.active_count ?? fetched.filter(r => r.active).length);
      setCooldownWindow(res.data?.cooldown_window_seconds || 6 * 3600);
      setServerNow(res.data?.now || null);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load alert cooldowns');
    } finally {
      setLoading(false);
    }
  }, [adminToken, onlyActive]);

  useEffect(() => {
    if (expanded) fetchCooldowns();
  }, [expanded, fetchCooldowns]);

  // Task #991 — auto-expand + scroll into view when arriving from the
  // dashboard's "N on hold" badge. We delay the scroll one tick so the
  // expansion has had a chance to lay out the table; otherwise the
  // smooth scroll lands on the still-collapsed header. Mirrors the
  // pattern in AlertThresholdPanel above.
  useEffect(() => {
    if (navContext?.panel !== 'alert-cooldowns') return undefined;
    setExpanded(true);
    const t = setTimeout(() => {
      const el = containerRef.current;
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 100);
    return () => clearTimeout(t);
  }, [navContext]);

  // Re-render every 10s while expanded so the countdown column ticks
  // down without the user having to hit refresh. Cheap (in-memory math
  // only — no network call).
  useEffect(() => {
    if (!expanded) return undefined;
    const id = setInterval(() => setTick((n) => n + 1), 10000);
    return () => clearInterval(id);
  }, [expanded]);

  const handleRelease = async (dedupKey) => {
    if (!dedupKey) return;
    if (typeof window !== 'undefined' && !window.confirm(`Release cooldown for "${dedupKey}"? The next matching alert will be allowed to fire immediately.`)) {
      return;
    }
    setReleasingKey(dedupKey);
    setReleaseError(null);
    try {
      await adminReleaseAlertCooldown(adminToken, dedupKey);
      // Only decrement the active counter when the row we just released
      // was actually still suppressing; releasing an already-expired row
      // shouldn't change the "X on hold" badge.
      const released = rows.find((r) => r.dedup_key === dedupKey);
      const wasActive = released && (released._live_active ?? released.active);
      setRows((prev) => prev.filter((r) => r.dedup_key !== dedupKey));
      if (wasActive) {
        setActiveCount((prev) => Math.max(0, prev - 1));
      }
    } catch (err) {
      setReleaseError(err.response?.data?.detail || 'Failed to release cooldown');
    } finally {
      setReleasingKey(null);
    }
  };

  const cooldownHours = Math.round(cooldownWindow / 3600);
  // Recompute remaining seconds locally so the countdown stays fresh
  // between refreshes (the server-side value is only accurate at the
  // moment of the API response).
  const nowMs = serverNow ? new Date(serverNow).getTime() + (tick * 10000) : Date.now();
  const liveRows = rows.map((r) => {
    if (!r.cooldown_expires_at) return r;
    const remaining = Math.max(0, Math.floor((new Date(r.cooldown_expires_at).getTime() - nowMs) / 1000));
    return { ...r, _live_remaining: remaining, _live_active: remaining > 0 };
  });

  return (
    <div ref={containerRef}>
    <GlassCard>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full p-5 flex items-center justify-between hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-amber-50">
            <Clock size={16} className="text-amber-500" />
          </div>
          <div className="text-left">
            <h3 className="text-sm font-semibold text-gray-900">Suppressed Alerts (Cooldowns)</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">
              {loading && expanded
                ? 'Loading...'
                : `${activeCount} active${cooldownWindow ? ` · ${cooldownHours}h window` : ''}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {activeCount > 0 && !expanded && (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-700">
              {activeCount} on hold
            </span>
          )}
          <AlertTriangle size={14} className={`text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-gray-100">
          <div className="p-4 flex flex-wrap items-center gap-3 border-b border-gray-50">
            <p className="text-[11px] text-gray-500 max-w-2xl">
              These alerts WOULD have fired in the last {cooldownHours}h but were
              suppressed by the persistent cooldown. Release a row to allow the next
              matching dispatch through immediately.
            </p>
            <label className="flex items-center gap-1.5 text-[11px] text-gray-600 cursor-pointer select-none ml-auto">
              <input
                type="checkbox"
                checked={onlyActive}
                onChange={(e) => setOnlyActive(e.target.checked)}
                className="h-3 w-3 rounded border-gray-300 text-violet-600 focus:ring-violet-200"
              />
              Active only
            </label>
            <button
              onClick={fetchCooldowns}
              disabled={loading}
              className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
              title="Refresh cooldowns"
            >
              <RefreshCw size={12} className={`text-gray-400 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>

          {(error || releaseError) && (
            <div className="p-4 space-y-2">
              {error && (
                <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
                  <AlertTriangle size={12} />
                  {error}
                </div>
              )}
              {releaseError && (
                <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
                  <AlertTriangle size={12} />
                  {releaseError}
                </div>
              )}
            </div>
          )}

          {loading ? (
            <div className="p-5 flex items-center gap-2 text-sm text-gray-400">
              <Loader2 size={14} className="animate-spin" />
              Loading cooldowns...
            </div>
          ) : liveRows.length === 0 ? (
            <div className="px-5 py-8 text-center text-sm text-gray-400">
              {onlyActive
                ? 'No alerts are currently being suppressed by cooldown.'
                : 'No cooldown rows on file.'}
            </div>
          ) : (
            <div className="max-h-[500px] overflow-y-auto divide-y divide-gray-50">
              {liveRows.map((row) => {
                const isReleasing = releasingKey === row.dedup_key;
                const remaining = row._live_remaining ?? row.seconds_until_expires ?? 0;
                const isActive = row._live_active ?? row.active;
                return (
                  <div key={row.dedup_key} className="p-4 hover:bg-gray-50 transition-colors">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wider bg-amber-50 text-amber-700">
                            {_formatCooldownType(row.alert_type)}
                          </span>
                          {isActive ? (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 text-[9px] font-semibold">
                              Suppressing
                            </span>
                          ) : (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 text-[9px] font-semibold">
                              Expired
                            </span>
                          )}
                        </div>
                        <p className="text-xs font-mono text-gray-900 mt-1 break-all">{row.dedup_key}</p>
                        <div className="mt-2 flex items-center gap-3 text-[10px] text-gray-400 flex-wrap">
                          <span className="flex items-center gap-1">
                            <Clock size={10} />
                            Last fired: {row.fired_at ? new Date(row.fired_at).toLocaleString() : '—'}
                          </span>
                          {row.cooldown_expires_at && (
                            <span className="flex items-center gap-1">
                              <Calendar size={10} />
                              {isActive
                                ? `Expires in ${_formatCountdown(remaining)}`
                                : `Expired ${new Date(row.cooldown_expires_at).toLocaleString()}`}
                            </span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => handleRelease(row.dedup_key)}
                        disabled={isReleasing}
                        className="flex-shrink-0 flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium bg-violet-50 text-violet-700 hover:bg-violet-100 transition-colors disabled:opacity-50"
                        title="Delete this dedup_key row so the next matching alert is free to fire"
                      >
                        {isReleasing ? <Loader2 size={10} className="animate-spin" /> : <Unlock size={10} />}
                        Release
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </GlassCard>
    </div>
  );
}

function AlertHistoryPanel({ adminToken }) {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [typeFilter, setTypeFilter] = useState('');
  const [dateRange, setDateRange] = useState('');
  const [ackFilter, setAckFilter] = useState(null);
  const [showSynthetic, setShowSynthetic] = useState(false);
  const [ackingId, setAckingId] = useState(null);
  const [ackingAll, setAckingAll] = useState(false);
  const [ackError, setAckError] = useState(null);
  const [unacknowledgedCount, setUnacknowledgedCount] = useState(0);

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { limit: 100 };
      if (typeFilter) params.type = typeFilter;
      if (ackFilter !== null) params.acknowledged = ackFilter;
      if (showSynthetic) params.include_synthetic = true;
      if (dateRange) {
        const now = new Date();
        const from = new Date(now.getTime() - Number(dateRange) * 86400000);
        params.date_from = from.toISOString();
      }
      const res = await adminGetAlerts(adminToken, params);
      const fetched = res.data?.alerts || [];
      setAlerts(fetched);
      setUnacknowledgedCount(fetched.filter(a => !a.acknowledged).length);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load alert history');
    } finally {
      setLoading(false);
    }
  }, [adminToken, typeFilter, dateRange, ackFilter, showSynthetic]);

  useEffect(() => {
    if (expanded) fetchAlerts();
  }, [expanded, fetchAlerts]);

  const handleAcknowledge = async (alertId) => {
    setAckingId(alertId);
    setAckError(null);
    try {
      await adminAcknowledgeAlert(adminToken, alertId);
      setAlerts(prev => prev.map(a => a._id === alertId ? { ...a, acknowledged: true, acknowledged_at: new Date().toISOString() } : a));
      setUnacknowledgedCount(prev => Math.max(0, prev - 1));
    } catch (err) {
      setAckError(err.response?.data?.detail || 'Failed to acknowledge alert');
    } finally {
      setAckingId(null);
    }
  };

  const handleAcknowledgeAll = async () => {
    setAckingAll(true);
    setAckError(null);
    try {
      await adminAcknowledgeAllAlerts(adminToken);
      setAlerts(prev => prev.map(a => ({ ...a, acknowledged: true, acknowledged_at: a.acknowledged_at || new Date().toISOString() })));
      setUnacknowledgedCount(0);
    } catch (err) {
      setAckError(err.response?.data?.detail || 'Failed to acknowledge alerts');
    } finally {
      setAckingAll(false);
    }
  };

  const alertTypeColor = (type) => {
    switch (type) {
      case 'spoofed_bot_surge': return { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-100' };
      case 'high_error_rate': return { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-100' };
      case 'high_latency': return { bg: 'bg-yellow-50', text: 'text-yellow-700', border: 'border-yellow-100' };
      case 'high_fallback_rate': return { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-100' };
      default: return { bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-100' };
    }
  };

  const formatType = (type) => {
    return (type || 'unknown').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  };

  return (
    <GlassCard>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full p-5 flex items-center justify-between hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-red-50">
            <History size={16} className="text-red-500" />
          </div>
          <div className="text-left">
            <h3 className="text-sm font-semibold text-gray-900">Alert History</h3>
            <p className="text-[10px] text-gray-400 mt-0.5">
              {loading && expanded ? 'Loading...' : `${alerts.length} alerts${unacknowledgedCount > 0 ? ` · ${unacknowledgedCount} unacknowledged` : ''}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {unacknowledgedCount > 0 && !expanded && (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-100 text-red-700">
              {unacknowledgedCount} new
            </span>
          )}
          <AlertTriangle size={14} className={`text-gray-400 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-gray-100">
          <div className="p-4 flex flex-wrap items-center gap-2 border-b border-gray-50">
            <div className="flex items-center gap-1.5">
              <Filter size={12} className="text-gray-400" />
              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
                className="text-[11px] border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-violet-200"
              >
                {ALERT_TYPE_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1.5">
              <Calendar size={12} className="text-gray-400" />
              <select
                value={dateRange}
                onChange={(e) => setDateRange(e.target.value)}
                className="text-[11px] border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-violet-200"
              >
                {DATE_RANGE_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1.5">
              <select
                value={ackFilter === null ? '' : ackFilter ? 'true' : 'false'}
                onChange={(e) => setAckFilter(e.target.value === '' ? null : e.target.value === 'true')}
                className="text-[11px] border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-violet-200"
              >
                <option value="">All Status</option>
                <option value="false">Unacknowledged</option>
                <option value="true">Acknowledged</option>
              </select>
            </div>
            <label
              className="flex items-center gap-1.5 text-[11px] text-gray-600 cursor-pointer select-none"
              title="Include synthetic alerts produced by the Test alert delivery button"
            >
              <input
                type="checkbox"
                checked={showSynthetic}
                onChange={(e) => setShowSynthetic(e.target.checked)}
                className="h-3 w-3 rounded border-gray-300 text-violet-600 focus:ring-violet-200"
              />
              Show test alerts
            </label>
            <div className="flex items-center gap-1.5 ml-auto">
              {unacknowledgedCount > 0 && (
                <button
                  onClick={handleAcknowledgeAll}
                  disabled={ackingAll}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-medium bg-violet-50 text-violet-600 hover:bg-violet-100 transition-colors disabled:opacity-50"
                >
                  {ackingAll ? <Loader2 size={10} className="animate-spin" /> : <CheckCheck size={10} />}
                  Acknowledge All
                </button>
              )}
              <button
                onClick={fetchAlerts}
                disabled={loading}
                className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
              >
                <RefreshCw size={12} className={`text-gray-400 ${loading ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>

          {(error || ackError) && (
            <div className="p-4 space-y-2">
              {error && (
                <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
                  <AlertTriangle size={12} />
                  {error}
                </div>
              )}
              {ackError && (
                <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
                  <AlertTriangle size={12} />
                  {ackError}
                </div>
              )}
            </div>
          )}

          {loading ? (
            <div className="p-5 flex items-center gap-2 text-sm text-gray-400">
              <Loader2 size={14} className="animate-spin" />
              Loading alert history...
            </div>
          ) : alerts.length === 0 ? (
            <div className="px-5 py-8 text-center text-sm text-gray-400">
              No alerts found matching the current filters
            </div>
          ) : (
            <div className="max-h-[500px] overflow-y-auto divide-y divide-gray-50">
              {alerts.map((alert) => {
                const colors = alertTypeColor(alert.type);
                const isAcking = ackingId === alert._id;
                return (
                  <div key={alert._id} className={`p-4 hover:bg-gray-50 transition-colors ${!alert.acknowledged ? 'bg-red-50/30' : ''}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wider ${colors.bg} ${colors.text}`}>
                            {formatType(alert.type)}
                          </span>
                          {!alert.acknowledged && (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 text-[9px] font-semibold">
                              New
                            </span>
                          )}
                        </div>
                        <p className="text-xs font-medium text-gray-900 mt-1">{alert.title}</p>
                        <p className="text-[11px] text-gray-500 mt-0.5 line-clamp-2">{alert.body}</p>
                        {alert.threshold_snapshot && (
                          <div className="mt-2 flex items-center gap-3 text-[10px] text-gray-400">
                            <span>Metric: {alert.threshold_snapshot.metric}</span>
                            {alert.threshold_snapshot.value !== undefined && <span>Threshold: {alert.threshold_snapshot.value}</span>}
                            {alert.threshold_snapshot.actual !== undefined && <span>Actual: {alert.threshold_snapshot.actual}</span>}
                          </div>
                        )}
                        <div className="mt-2 flex items-center gap-3 text-[10px] text-gray-400">
                          <span className="flex items-center gap-1">
                            <Clock size={10} />
                            {alert.fired_at ? new Date(alert.fired_at).toLocaleString() : '—'}
                          </span>
                          {alert.acknowledged && alert.acknowledged_at && (
                            <span className="flex items-center gap-1">
                              <Check size={10} className="text-emerald-500" />
                              Ack'd {new Date(alert.acknowledged_at).toLocaleString()}
                              {alert.acknowledged_by ? ` by ${alert.acknowledged_by}` : ''}
                            </span>
                          )}
                        </div>
                      </div>
                      {!alert.acknowledged && (
                        <button
                          onClick={() => handleAcknowledge(alert._id)}
                          disabled={isAcking}
                          className="flex-shrink-0 flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-colors disabled:opacity-50"
                        >
                          {isAcking ? <Loader2 size={10} className="animate-spin" /> : <Check size={10} />}
                          Acknowledge
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </GlassCard>
  );
}

const PERIOD_OPTIONS = [
  { label: '7 days', value: 7 },
  { label: '14 days', value: 14 },
  { label: '30 days', value: 30 },
  { label: '90 days', value: 90 },
];

export default function AdminBotSecurity({ adminToken, navContext }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(7);
  const [blockedIps, setBlockedIps] = useState([]);
  const [durationBreakdown, setDurationBreakdown] = useState(null);
  const [blockTrends, setBlockTrends] = useState([]);
  const [actionLoading, setActionLoading] = useState({});
  const [blockDurationMenu, setBlockDurationMenu] = useState(null);
  const [botTraffic, setBotTraffic] = useState(null);
  // Task #573 — bumped on every successful refetch so per-card
  // SectionErrorBoundary instances can drop any stale fallback the
  // moment fresh data arrives. Without this, a transient API blip
  // leaves each tripped card showing its inline "Try again" button
  // even after subsequent refreshes return valid data.
  const [refreshCounter, setRefreshCounter] = useState(0);

  const blockedSet = new Set(
    blockedIps
      .filter((b) => !b.expires_at || new Date(b.expires_at) > new Date())
      .map((b) => b.ip_hash)
  );

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [spoofRes, blockedRes] = await Promise.all([
        adminGetSpoofedBots(adminToken, days),
        adminGetBlockedIps(adminToken),
      ]);
      setData(spoofRes.data);
      setBlockedIps(blockedRes.data?.blocked_ips || []);
      setDurationBreakdown(blockedRes.data?.duration_breakdown || null);
      adminGetBlockTrends(adminToken, 30)
        .then(res => setBlockTrends(res.data?.series || []))
        .catch(() => setBlockTrends([]));
      adminGetBotTraffic(adminToken)
        .then(res => setBotTraffic(res.data))
        .catch(() => setBotTraffic(null));
      setRefreshCounter((n) => n + 1);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load spoofed bot data');
    } finally {
      setLoading(false);
    }
  }, [adminToken, days]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const BLOCK_DURATIONS = [
    { label: '1 hour', hours: 1 },
    { label: '6 hours', hours: 6 },
    { label: '24 hours', hours: 24 },
    { label: '7 days', hours: 168 },
    { label: '30 days', hours: 720 },
    { label: 'Permanent', hours: null },
  ];

  const handleBlock = async (ipHash, expiresIn = null) => {
    setBlockDurationMenu(null);
    setActionLoading((prev) => ({ ...prev, [ipHash]: 'blocking' }));
    try {
      await adminBlockIp(adminToken, ipHash, 'repeat_spoof_offender', expiresIn);
      const entry = { ip_hash: ipHash, blocked_at: new Date().toISOString(), reason: 'repeat_spoof_offender' };
      if (expiresIn) {
        entry.expires_at = new Date(Date.now() + expiresIn * 3600000).toISOString();
      }
      setBlockedIps((prev) => [...prev, entry]);
      adminGetBlockedIps(adminToken).then(res => {
        setDurationBreakdown(res.data?.duration_breakdown || null);
      }).catch(() => {});
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to block IP');
    } finally {
      setActionLoading((prev) => ({ ...prev, [ipHash]: null }));
    }
  };

  const handleUnblock = async (ipHash) => {
    setActionLoading((prev) => ({ ...prev, [ipHash]: 'unblocking' }));
    try {
      await adminUnblockIp(adminToken, ipHash);
      setBlockedIps((prev) => prev.filter((b) => b.ip_hash !== ipHash));
      adminGetBlockedIps(adminToken).then(res => {
        setDurationBreakdown(res.data?.duration_breakdown || null);
      }).catch(() => {});
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to unblock IP');
    } finally {
      setActionLoading((prev) => ({ ...prev, [ipHash]: null }));
    }
  };

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center h-40 gap-3">
        <Loader2 className="w-5 h-5 animate-spin text-violet-500" />
        <span className="text-sm text-gray-400">Loading bot security data...</span>
      </div>
    );
  }

  if (error && !data) {
    return (
      <GlassCard className="p-6">
        <div className="flex items-center gap-3 text-red-500">
          <AlertTriangle size={18} />
          <p className="text-sm">{error}</p>
        </div>
        <button
          onClick={fetchData}
          className="mt-3 text-sm text-violet-600 hover:text-violet-800 flex items-center gap-1.5"
        >
          <RefreshCw size={13} /> Retry
        </button>
      </GlassCard>
    );
  }

  const realtime = data?.realtime || {};
  const dailyCounts = data?.daily_counts || [];
  const topBots = data?.by_claimed_bot || [];
  const offenders = data?.repeat_offender_ips || [];
  const recent = data?.recent_attempts || [];

  const rpmColor = (realtime.spoof_rpm || 0) > 10 ? '#ef4444' : (realtime.spoof_rpm || 0) > 3 ? '#f59e0b' : '#10b981';

  return (
    <SectionErrorBoundary name="Bot Security">
      <div className="space-y-6 max-w-7xl">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-red-50">
              <Shield size={20} className="text-red-500" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-gray-900">Bot Security</h2>
              <p className="text-xs text-gray-400">Spoofed bot detection & monitoring</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="text-xs border border-gray-200 rounded-lg px-3 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-violet-200"
            >
              {PERIOD_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <button
              onClick={fetchData}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-violet-50 text-violet-600 hover:bg-violet-100 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              Refresh
            </button>
          </div>
        </div>

        {botTraffic && (
          <GlassCard>
            <div className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <Bot size={14} className="text-violet-500" />
                <span className="text-xs font-semibold text-gray-700">Cloudflare Bot Traffic ({botTraffic.period || '24h'})</span>
                {botTraffic.status === 'amber' && (
                  <span className="ml-auto text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">Warning</span>
                )}
                {botTraffic.status === 'red' && (
                  <span className="ml-auto text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-700">Critical</span>
                )}
              </div>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold text-gray-900">{botTraffic.bot_pct != null ? `${botTraffic.bot_pct}%` : '—'}</span>
                <span className="text-xs text-gray-400">bot requests</span>
              </div>
              <div className="mt-1 text-xs text-gray-500">
                {botTraffic.bot_requests?.toLocaleString() || 0} / {botTraffic.total_requests?.toLocaleString() || 0} requests
              </div>
              {botTraffic.break_glass && (
                <div className="mt-2 text-xs font-bold text-red-600 bg-red-50 rounded px-2 py-1">Break-glass threshold exceeded</div>
              )}
            </div>
          </GlassCard>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
          <StatCard
            label="Spoof RPM"
            value={realtime.spoof_rpm ?? 0}
            icon={TrendingUp}
            color={rpmColor}
            pulse={(realtime.spoof_rpm || 0) > 0}
          />
          <StatCard
            label="Session Total"
            value={realtime.session_total ?? 0}
            icon={Shield}
            color="#8b5cf6"
          />
          <StatCard
            label={`Period Total (${days}d)`}
            value={data?.period_total ?? 0}
            icon={AlertTriangle}
            color="#f59e0b"
          />
          <StatCard
            label="Repeat Offenders"
            value={offenders.length}
            icon={Hash}
            color="#ef4444"
          />
          <StatCard
            label="Blocked IPs"
            value={blockedIps.length}
            icon={Ban}
            color="#dc2626"
          />
        </div>

        {durationBreakdown && Object.values(durationBreakdown).some(v => v > 0) && (
          <SectionErrorBoundary name="Block Duration Breakdown" resetKeys={[refreshCounter]}>
          <GlassCard>
            <div className="p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                  <Clock size={14} className="text-violet-500" />
                  Block Duration Breakdown
                </h3>
                <span className="text-[10px] text-gray-400 uppercase tracking-wider">Active blocks only</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                {[
                  { key: '1h', label: '1 Hour', color: '#3b82f6' },
                  { key: '6h', label: '6 Hours', color: '#6366f1' },
                  { key: '24h', label: '24 Hours', color: '#8b5cf6' },
                  { key: '7d', label: '7 Days', color: '#f59e0b' },
                  { key: '30d', label: '30 Days', color: '#ef4444' },
                  { key: 'permanent', label: 'Permanent', color: '#dc2626' },
                ].map(({ key, label, color }) => {
                  const count = durationBreakdown[key] || 0;
                  const total = Object.values(durationBreakdown).reduce((a, b) => a + b, 0);
                  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
                  return (
                    <div key={key} className="rounded-xl border border-gray-100 p-3 bg-gray-50">
                      <p className="text-[10px] text-gray-400 uppercase tracking-wider">{label}</p>
                      <p className="text-lg font-bold text-gray-900 mt-1">{count}</p>
                      <div className="mt-2 h-1.5 rounded-full bg-gray-200 overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${pct}%`, background: color }}
                        />
                      </div>
                      <p className="text-[10px] text-gray-400 mt-1">{pct}%</p>
                    </div>
                  );
                })}
              </div>
            </div>
          </GlassCard>
          </SectionErrorBoundary>
        )}

        {blockTrends.length > 0 && blockTrends.some(d => d['1h'] + d['6h'] + d['24h'] + d['7d'] + d['30d'] + d.permanent > 0) && (
          <SectionErrorBoundary name="Block Trends" resetKeys={[refreshCounter]}>
          <GlassCard>
            <div className="p-5 pb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                <TrendingUp size={14} className="text-violet-500" />
                Block Duration Trends
              </h3>
              <span className="text-[10px] text-gray-400 uppercase tracking-wider">Last 30 days</span>
            </div>
            <div className="px-3 pb-4" style={{ height: 280 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={blockTrends} margin={{ top: 5, right: 10, bottom: 0, left: -15 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                    tickFormatter={(v) => {
                      const d = new Date(v);
                      return `${d.getMonth() + 1}/${d.getDate()}`;
                    }}
                  />
                  <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #e2e8f0' }}
                    labelFormatter={(v) => new Date(v).toLocaleDateString()}
                  />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Area type="monotone" dataKey="1h" stackId="1" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.6} name="1 Hour" />
                  <Area type="monotone" dataKey="6h" stackId="1" stroke="#6366f1" fill="#6366f1" fillOpacity={0.6} name="6 Hours" />
                  <Area type="monotone" dataKey="24h" stackId="1" stroke="#8b5cf6" fill="#8b5cf6" fillOpacity={0.6} name="24 Hours" />
                  <Area type="monotone" dataKey="7d" stackId="1" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.6} name="7 Days" />
                  <Area type="monotone" dataKey="30d" stackId="1" stroke="#ef4444" fill="#ef4444" fillOpacity={0.6} name="30 Days" />
                  <Area type="monotone" dataKey="permanent" stackId="1" stroke="#dc2626" fill="#dc2626" fillOpacity={0.6} name="Permanent" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </GlassCard>
          </SectionErrorBoundary>
        )}

        <SectionErrorBoundary name="Alert Thresholds" resetKeys={[refreshCounter]}><AlertThresholdPanel adminToken={adminToken} navContext={navContext} /></SectionErrorBoundary>

        <SectionErrorBoundary name="Alert History" resetKeys={[refreshCounter]}><AlertHistoryPanel adminToken={adminToken} /></SectionErrorBoundary>

        <SectionErrorBoundary name="Suppressed Alerts" resetKeys={[refreshCounter]}><AlertCooldownsPanel adminToken={adminToken} navContext={navContext} /></SectionErrorBoundary>

        <SectionErrorBoundary name="TTL Monitor" resetKeys={[refreshCounter]}><TtlMonitorPanel adminToken={adminToken} /></SectionErrorBoundary>

        <SectionErrorBoundary name="Recent Alerts" resetKeys={[refreshCounter]}>
        <GlassCard>
          <div className="p-5 pb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
              <TrendingUp size={14} className="text-violet-500" />
              Daily Spoof Attempts
            </h3>
            <span className="text-[10px] text-gray-400 uppercase tracking-wider">Last {days} days</span>
          </div>
          <div className="px-3 pb-4" style={{ height: 260 }}>
            {dailyCounts.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={dailyCounts}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                    tickFormatter={(v) => {
                      const d = new Date(v);
                      return `${d.getMonth() + 1}/${d.getDate()}`;
                    }}
                  />
                  <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e2e8f0' }}
                    labelFormatter={(v) => new Date(v).toLocaleDateString()}
                  />
                  <Line
                    type="monotone"
                    dataKey="count"
                    stroke="#8b5cf6"
                    strokeWidth={2}
                    dot={{ r: 3, fill: '#8b5cf6' }}
                    activeDot={{ r: 5, fill: '#7c3aed' }}
                    name="Attempts"
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-sm text-gray-400">
                No data for this period
              </div>
            )}
          </div>
        </GlassCard>
        </SectionErrorBoundary>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <SectionErrorBoundary name="Top Claimed Bots" resetKeys={[refreshCounter]}>
          <GlassCard>
            <div className="p-5 pb-3">
              <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                <Bot size={14} className="text-violet-500" />
                Top Claimed Bots
              </h3>
            </div>
            {topBots.length > 0 ? (
              <>
                <div className="px-3 pb-2" style={{ height: 220 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={topBots.slice(0, 10)} layout="vertical">
                      <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 10, fill: '#94a3b8' }} allowDecimals={false} />
                      <YAxis
                        type="category"
                        dataKey="bot"
                        tick={{ fontSize: 10, fill: '#64748b' }}
                        width={120}
                      />
                      <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e2e8f0' }} />
                      <Bar dataKey="count" fill="#8b5cf6" radius={[0, 4, 4, 0]} name="Attempts" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="border-t border-gray-100 max-h-48 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-400 uppercase tracking-wider">
                        <th className="text-left px-5 py-2 font-medium">Bot Name</th>
                        <th className="text-right px-5 py-2 font-medium">Count</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topBots.map((b, i) => (
                        <tr key={i} className="border-t border-gray-50 hover:bg-gray-50">
                          <td className="px-5 py-2 text-gray-700 font-medium">{b.bot}</td>
                          <td className="px-5 py-2 text-right text-gray-500">{b.count.toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <div className="px-5 pb-5 text-sm text-gray-400">No spoofed bots detected</div>
            )}
          </GlassCard>
          </SectionErrorBoundary>

          <SectionErrorBoundary name="Repeat Offender IPs" resetKeys={[refreshCounter]}>
          <GlassCard>
            <div className="p-5 pb-3">
              <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                <Hash size={14} className="text-red-500" />
                Repeat Offender IPs
              </h3>
              <p className="text-[10px] text-gray-400 mt-0.5">IPs with 5+ spoofing attempts</p>
            </div>
            {offenders.length > 0 ? (
              <div className="border-t border-gray-100 max-h-[380px] overflow-y-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-gray-400 uppercase tracking-wider">
                      <th className="text-left px-5 py-2 font-medium">IP Hash</th>
                      <th className="text-right px-5 py-2 font-medium">Attempts</th>
                      <th className="text-left px-5 py-2 font-medium">Claimed Bots</th>
                      <th className="text-center px-5 py-2 font-medium">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {offenders.map((o, i) => {
                      const isBlocked = blockedSet.has(o.ip_hash);
                      const busy = actionLoading[o.ip_hash];
                      return (
                        <tr key={i} className="border-t border-gray-50 hover:bg-gray-50">
                          <td className="px-5 py-2 text-gray-600 font-mono text-[11px]">
                            {o.ip_hash ? `${o.ip_hash.slice(0, 12)}...` : '—'}
                          </td>
                          <td className="px-5 py-2 text-right">
                            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              o.attempts >= 50 ? 'bg-red-100 text-red-700' :
                              o.attempts >= 20 ? 'bg-amber-100 text-amber-700' :
                              'bg-gray-100 text-gray-600'
                            }`}>
                              {o.attempts ?? 0}
                            </span>
                          </td>
                          <td className="px-5 py-2">
                            <div className="flex flex-wrap gap-1">
                              {(o.claimed_bots || []).slice(0, 3).map((bot, j) => (
                                <span key={j} className="inline-flex items-center px-1.5 py-0.5 rounded bg-violet-50 text-violet-600 text-[10px]">
                                  {bot}
                                </span>
                              ))}
                              {(o.claimed_bots || []).length > 3 && (
                                <span className="text-[10px] text-gray-400">+{o.claimed_bots.length - 3}</span>
                              )}
                            </div>
                          </td>
                          <td className="px-5 py-2 text-center">
                            {isBlocked ? (
                              <button
                                onClick={() => handleUnblock(o.ip_hash)}
                                disabled={!!busy}
                                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-colors disabled:opacity-50"
                              >
                                {busy === 'unblocking' ? <Loader2 size={10} className="animate-spin" /> : <Unlock size={10} />}
                                Unblock
                              </button>
                            ) : (
                              <div className="relative">
                                <button
                                  onClick={() => setBlockDurationMenu(blockDurationMenu === o.ip_hash ? null : o.ip_hash)}
                                  disabled={!!busy}
                                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium bg-red-50 text-red-700 hover:bg-red-100 transition-colors disabled:opacity-50"
                                >
                                  {busy === 'blocking' ? <Loader2 size={10} className="animate-spin" /> : <Ban size={10} />}
                                  Block
                                </button>
                                {blockDurationMenu === o.ip_hash && (
                                  <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[120px]">
                                    {BLOCK_DURATIONS.map((d) => (
                                      <button
                                        key={d.label}
                                        onClick={() => handleBlock(o.ip_hash, d.hours)}
                                        className="block w-full text-left px-3 py-1.5 text-[11px] text-gray-700 hover:bg-gray-50 transition-colors"
                                      >
                                        {d.label}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="px-5 pb-5 text-sm text-gray-400">No repeat offenders found</div>
            )}
          </GlassCard>
          </SectionErrorBoundary>
        </div>

        {blockedIps.length > 0 && (
          <SectionErrorBoundary name="Blocked IPs" resetKeys={[refreshCounter]}>
          <GlassCard>
            <div className="p-5 pb-3">
              <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                <Ban size={14} className="text-red-500" />
                Blocked IPs
              </h3>
              <p className="text-[10px] text-gray-400 mt-0.5">{blockedIps.length} IP{blockedIps.length !== 1 ? 's' : ''} currently blocked</p>
            </div>
            <div className="border-t border-gray-100 max-h-[300px] overflow-y-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-400 uppercase tracking-wider">
                    <th className="text-left px-5 py-2 font-medium">IP Hash</th>
                    <th className="text-left px-5 py-2 font-medium">Reason</th>
                    <th className="text-left px-5 py-2 font-medium">Blocked At</th>
                    <th className="text-left px-5 py-2 font-medium">Expires</th>
                    <th className="text-left px-5 py-2 font-medium">Blocked By</th>
                    <th className="text-center px-5 py-2 font-medium">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {blockedIps.map((b, i) => {
                    const busy = actionLoading[b.ip_hash];
                    return (
                      <tr key={i} className="border-t border-gray-50 hover:bg-gray-50">
                        <td className="px-5 py-2 text-gray-600 font-mono text-[11px]">
                          {b.ip_hash ? `${b.ip_hash.slice(0, 12)}...` : '—'}
                        </td>
                        <td className="px-5 py-2 text-gray-500">
                          <span className="flex items-center gap-1.5">
                            {b.reason || '—'}
                            {b.reason === 'auto_threshold' && (
                              <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[9px] font-semibold uppercase tracking-wider">Auto</span>
                            )}
                          </span>
                        </td>
                        <td className="px-5 py-2 text-gray-500 whitespace-nowrap">
                          {b.blocked_at ? new Date(b.blocked_at).toLocaleString() : '—'}
                        </td>
                        <td className="px-5 py-2 whitespace-nowrap">
                          {b.expires_at ? (
                            new Date(b.expires_at) <= new Date() ? (
                              <span className="text-amber-600 text-[10px] font-medium">Expired</span>
                            ) : (
                              <span className="text-gray-500">{new Date(b.expires_at).toLocaleString()}</span>
                            )
                          ) : (
                            <span className="text-gray-400 text-[10px]">Permanent</span>
                          )}
                        </td>
                        <td className="px-5 py-2 text-gray-500">{b.blocked_by || '—'}</td>
                        <td className="px-5 py-2 text-center">
                          <button
                            onClick={() => handleUnblock(b.ip_hash)}
                            disabled={!!busy}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-colors disabled:opacity-50"
                          >
                            {busy === 'unblocking' ? <Loader2 size={10} className="animate-spin" /> : <Unlock size={10} />}
                            Unblock
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </GlassCard>
          </SectionErrorBoundary>
        )}

        <SectionErrorBoundary name="Bot User Agents" resetKeys={[refreshCounter]}>
        <GlassCard>
          <div className="p-5 pb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
              <Clock size={14} className="text-violet-500" />
              Recent Spoof Attempts
            </h3>
            <span className="text-[10px] text-gray-400">Latest 50</span>
          </div>
          {recent.length > 0 ? (
            <div className="border-t border-gray-100 max-h-[420px] overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-white">
                  <tr className="text-gray-400 uppercase tracking-wider">
                    <th className="text-left px-5 py-2 font-medium">Time</th>
                    <th className="text-left px-5 py-2 font-medium">Claimed Bot</th>
                    <th className="text-left px-5 py-2 font-medium">IP Hash</th>
                    <th className="text-left px-5 py-2 font-medium">Path</th>
                    <th className="text-left px-5 py-2 font-medium">User Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((r, i) => (
                    <tr key={i} className="border-t border-gray-50 hover:bg-gray-50">
                      <td className="px-5 py-2 text-gray-500 whitespace-nowrap">
                        {r.timestamp ? new Date(r.timestamp).toLocaleString() : r.date || '—'}
                      </td>
                      <td className="px-5 py-2">
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-red-50 text-red-600 text-[10px] font-medium">
                          {r.claimed_bot || 'Unknown'}
                        </span>
                      </td>
                      <td className="px-5 py-2 text-gray-600 font-mono text-[11px]">
                        {r.ip_hash ? `${r.ip_hash.slice(0, 12)}...` : '—'}
                      </td>
                      <td className="px-5 py-2 text-gray-600 max-w-[200px] truncate" title={r.path}>
                        {r.path || '—'}
                      </td>
                      <td className="px-5 py-2 text-gray-400 max-w-[200px] truncate" title={r.user_agent}>
                        {r.user_agent || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-5 pb-5 text-sm text-gray-400">No recent attempts</div>
          )}
        </GlassCard>
        </SectionErrorBoundary>

        {realtime.session_by_bot && Object.keys(realtime.session_by_bot).length > 0 && (
          <SectionErrorBoundary name="Realtime Session Bots" resetKeys={[refreshCounter]}>
          <GlassCard>
            <div className="p-5 pb-3">
              <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                <Eye size={14} className="text-amber-500" />
                Real-time Session Breakdown
              </h3>
              <p className="text-[10px] text-gray-400 mt-0.5">Spoofed requests in current server session</p>
            </div>
            <div className="border-t border-gray-100">
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 p-5">
                {Object.entries(realtime.session_by_bot)
                  .sort(([, a], [, b]) => b - a)
                  .map(([bot, count]) => (
                    <div key={bot} className="rounded-xl border border-gray-100 p-3 bg-gray-50">
                      <p className="text-[10px] text-gray-400 uppercase tracking-wider truncate">{bot}</p>
                      <p className="text-lg font-bold text-gray-900 mt-1">{count.toLocaleString()}</p>
                    </div>
                  ))}
              </div>
            </div>
          </GlassCard>
          </SectionErrorBoundary>
        )}
      </div>
    </SectionErrorBoundary>
  );
}
