import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Cloud, Zap, Mic, Volume2, FileText, ShieldAlert,
  MessageSquare, Languages, Sparkles, ShieldCheck,
  RefreshCw, ExternalLink, AlertTriangle, CheckCircle2, Loader2,
} from 'lucide-react';
import { API_BASE } from '@/utils/api';

/*
  AdminAwsNativePanel — Task #337
  ================================
  Admin tile-grid for the AWS-native advanced features stood up by
  `infra/aws/aws-native-features.tf`. Every feature is an *additional*
  path in an existing failover chain — toggling one off here never
  takes a user-visible feature down (the underlying chain falls back
  to the GCP / Sarvam / Cohere primary).

  Backend contract — `GET /admin/aws-native/status`:
    {
      asOf: "2026-05-04T12:34:56Z",
      features: [
        {
          key:           "bedrock_cohere",
          enabled:       true,
          health:        "ok" | "degraded" | "failed" | "disabled",
          throttledPct:  0.4,                // last 5 min
          p95LatencyMs:  840,
          spendUsd7d:    37.21,
          dashboardUrl:  "https://...cloudwatch...",
          runbookAnchor: "31-bedrock-cohere-only",
          lastError:     null
        },
        ...
      ],
      bedrockGuardrail: "Cohere-only — Claude/Llama/Titan/Nova explicitly excluded"
    }

  Toggling a feature: `POST /admin/aws-native/toggle { key, enabled }`.
*/

const FEATURE_META = {
  bedrock_cohere: {
    label: 'Bedrock — Cohere',
    Icon: Zap,
    accent: 'text-amber-600',
    blurb: 'Embed + rerank via Cohere on Bedrock. LLM-Bedrock paths intentionally absent — Azure OpenAI + Vertex Gemini cover those.',
    chain: 'Primary embed/rerank',
  },
  polly: {
    label: 'Polly TTS',
    Icon: Volume2,
    accent: 'text-emerald-600',
    blurb: 'Neural / Generative voices as the third tier in the TTS chain (after ElevenLabs + Google TTS).',
    chain: 'TTS tier 3',
  },
  transcribe: {
    label: 'Transcribe STT',
    Icon: Mic,
    accent: 'text-emerald-600',
    blurb: 'Streaming STT as the third tier in the mic input chain (after Deepgram + Google Chirp).',
    chain: 'STT tier 3',
  },
  textract: {
    label: 'Textract',
    Icon: FileText,
    accent: 'text-violet-600',
    blurb: 'Structured-document OCR for past papers, marks sheets, handwritten exam answers.',
    chain: 'OCR (structured branch)',
  },
  rekognition: {
    label: 'Rekognition',
    Icon: ShieldAlert,
    accent: 'text-rose-600',
    blurb: 'Image moderation on every user upload before R2 commit. Closed-by-default on outage.',
    chain: 'Upload guard (required)',
  },
  comprehend: {
    label: 'Comprehend',
    Icon: MessageSquare,
    accent: 'text-sky-600',
    blurb: 'Sampled PII + sentiment over chat & reviews into the analytics warehouse. Never auto-blocks.',
    chain: 'Background (analytics only)',
  },
  translate: {
    label: 'Translate',
    Icon: Languages,
    accent: 'text-indigo-600',
    blurb: 'Indic ↔ English fallback when Sarvam returns 429 / 5xx. Sarvam stays primary for Assamese.',
    chain: 'Translate fallback',
  },
  personalize: {
    label: 'Personalize',
    Icon: Sparkles,
    accent: 'text-fuchsia-600',
    blurb: 'Home + Continue Learning recommendations. Deterministic ranker is the always-on fallback.',
    chain: 'Recs (feature-flagged)',
  },
  fraud_detector: {
    label: 'Fraud Detector',
    Icon: ShieldCheck,
    accent: 'text-orange-600',
    blurb: 'Risk score on signup + payment intent. High-risk events route to admin review.',
    chain: 'Signup + payment guard',
  },
};

const HEALTH_STYLES = {
  ok:       { wrap: 'border-emerald-200 bg-emerald-50',  text: 'text-emerald-700', Icon: CheckCircle2,  label: 'Healthy' },
  degraded: { wrap: 'border-amber-200 bg-amber-50',      text: 'text-amber-700',   Icon: AlertTriangle, label: 'Degraded' },
  failed:   { wrap: 'border-red-200 bg-red-50',          text: 'text-red-700',     Icon: AlertTriangle, label: 'Failed' },
  disabled: { wrap: 'border-gray-200 bg-gray-50',        text: 'text-gray-500',    Icon: Cloud,         label: 'Disabled' },
};

const adminHeaders = (token) => {
  const isJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isJwt ? { Authorization: `Bearer ${token}` } : {};
};

const fmtUsd = (n) => (typeof n === 'number' ? `$${n.toFixed(2)}` : '—');
const fmtMs  = (n) => (typeof n === 'number' ? `${Math.round(n)}ms` : '—');
const fmtPct = (n) => (typeof n === 'number' ? `${(n * 100).toFixed(1)}%` : '—');

function FeatureTile({ feature, onToggle, busy }) {
  const meta = FEATURE_META[feature.key] || { label: feature.key, Icon: Cloud, accent: 'text-gray-600', blurb: '', chain: '' };
  const health = HEALTH_STYLES[feature.health] || HEALTH_STYLES.disabled;
  const { Icon } = meta;

  return (
    <div
      data-testid={`aws-native-tile-${feature.key}`}
      className={`rounded-2xl border p-4 shadow-sm bg-white flex flex-col gap-3 ${feature.enabled ? '' : 'opacity-70'}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon size={18} className={meta.accent} />
          <div>
            <p className="text-sm font-semibold text-gray-900">{meta.label}</p>
            <p className="text-[11px] text-gray-400 font-mono">{meta.chain}</p>
          </div>
        </div>
        <span className={`inline-flex items-center gap-1 text-[10px] font-bold uppercase rounded-full border px-2 py-0.5 ${health.wrap} ${health.text}`}>
          <health.Icon size={11} />
          {health.label}
        </span>
      </div>

      <p className="text-xs text-gray-600 leading-snug">{meta.blurb}</p>

      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <div className="rounded-lg bg-gray-50 border border-gray-200 px-2 py-1.5">
          <p className="text-gray-400 uppercase tracking-wide">Throttle</p>
          <p className="font-mono text-gray-900">{fmtPct(feature.throttledPct)}</p>
        </div>
        <div className="rounded-lg bg-gray-50 border border-gray-200 px-2 py-1.5">
          <p className="text-gray-400 uppercase tracking-wide">p95</p>
          <p className="font-mono text-gray-900">{fmtMs(feature.p95LatencyMs)}</p>
        </div>
        <div className="rounded-lg bg-gray-50 border border-gray-200 px-2 py-1.5">
          <p className="text-gray-400 uppercase tracking-wide">Spend 7d</p>
          <p className="font-mono text-gray-900">{fmtUsd(feature.spendUsd7d)}</p>
        </div>
      </div>

      {feature.lastError && (
        <p className="text-[11px] text-red-600 truncate" title={feature.lastError}>
          Last error: {feature.lastError}
        </p>
      )}

      <div className="flex items-center justify-between pt-1 border-t border-gray-100">
        <button
          type="button"
          disabled={busy}
          onClick={() => onToggle(feature.key, !feature.enabled)}
          className={`text-xs font-medium px-2.5 py-1 rounded-md border transition-colors ${feature.enabled
            ? 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
            : 'bg-emerald-600 border-emerald-600 text-white hover:bg-emerald-700'} disabled:opacity-50`}
        >
          {busy ? 'Saving…' : feature.enabled ? 'Disable' : 'Enable'}
        </button>
        {feature.dashboardUrl && (
          <a
            href={feature.dashboardUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[11px] text-blue-600 hover:underline"
          >
            CloudWatch <ExternalLink size={10} />
          </a>
        )}
      </div>
    </div>
  );
}

export default function AdminAwsNativePanel({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [busyKey, setBusyKey] = useState(null);

  const headers = useMemo(() => adminHeaders(adminToken), [adminToken]);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const res = await axios.get(`${API_BASE}/admin/aws-native/status`, {
        headers,
        withCredentials: true,
      });
      setData(res.data);
    } catch (e) {
      setErr(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => { load(); }, [load]);

  const onToggle = useCallback(async (key, enabled) => {
    setBusyKey(key);
    try {
      await axios.post(
        `${API_BASE}/admin/aws-native/toggle`,
        { key, enabled },
        { headers, withCredentials: true },
      );
      toast.success(`${FEATURE_META[key]?.label || key} ${enabled ? 'enabled' : 'disabled'}`);
      await load();
    } catch (e) {
      toast.error(`Toggle failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusyKey(null);
    }
  }, [headers, load]);

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 text-gray-400 text-sm py-8">
        <Loader2 size={16} className="animate-spin" /> Loading AWS-native features…
      </div>
    );
  }

  if (err && !data) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-600 flex items-center gap-2">
        <AlertTriangle size={14} /> Failed to load AWS-native status: {err}
      </div>
    );
  }

  const features = data?.features || [];

  return (
    <div className="space-y-4" data-testid="admin-aws-native-panel">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cloud size={18} className="text-orange-500" />
          <h3 className="text-sm font-bold text-gray-900">AWS-Native Features</h3>
          <span className="text-xs text-gray-400">live from <code className="font-mono">/admin/aws-native/status</code></span>
        </div>
        <button
          type="button"
          onClick={load}
          className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-md border border-gray-300 bg-white hover:bg-gray-50 text-gray-700"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {data?.bedrockGuardrail && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 flex items-start gap-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span><strong>Bedrock guardrail:</strong> {data.bedrockGuardrail}. See{' '}
            <a className="underline" href="/docs/features/aws-native.md#31-bedrock-cohere-only" target="_blank" rel="noreferrer">runbook §3.1</a>.
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {features.map((f) => (
          <FeatureTile
            key={f.key}
            feature={f}
            onToggle={onToggle}
            busy={busyKey === f.key}
          />
        ))}
      </div>

      {data?.asOf && (
        <p className="text-[11px] text-gray-400 text-right">As of {new Date(data.asOf).toLocaleString()}</p>
      )}
    </div>
  );
}
