import { useState, useEffect } from 'react';
import { Key, Zap, CreditCard, Mail, Bell, BarChart3, Shield, CheckCircle2, Eye, EyeOff, TestTube2, Loader2, Database, Cpu, Mic, Languages, Layers } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import RoutingPools from './RoutingPools';
import { toast } from 'sonner';
import { adminGetApiConfig, adminUpdateApiConfig, API_BASE } from '@/utils/api';
import axios from 'axios';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const adminHeaders = (token) => {
  const isRealJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isRealJwt ? { Authorization: `Bearer ${token}` } : {};
};

const SERVICES = [
  { id: 'routing', icon: Layers,     label: 'Routing & Pools',  accent: 'violet', desc: 'Live snapshot of PROVIDER_PRIORITY × POOL_WEIGHTS — locked provider chain' },
  { id: 'chat_model', icon: Cpu,     label: 'Chat Model',       accent: 'violet', desc: 'Active LLM provider for the user-facing chat (Vertex Gemini Flash / Workers AI)' },
  { id: 'deepgram',icon: Mic,        label: 'Deepgram',         accent: 'emerald',desc: 'STT primary (nova-3). Speech pipeline backbone (Task #552 §G — TTS branch retired; ElevenLabs is sole English TTS).' },
  { id: 'workers_ai_indic', icon: Languages, label: 'Workers AI · IndicTrans2', accent: 'orange', desc: 'Cloudflare Workers AI dedicated Indic neural MT — primary for translate + assamese_content pools.' },
  { id: 'mongodb_atlas', icon: Database, label: 'MongoDB Atlas',accent: 'emerald',desc: 'Atlas $vectorSearch — weight-0 fallback in vector_search pool (free tier).' },
  { id: 'emergent',icon: Zap,        label: 'Emergent AI',      accent: 'amber',  desc: 'Universal LLM key — admin AI generation' },
  { id: 'supabase',icon: Database,   label: 'Supabase',         accent: 'cyan',   desc: 'Users & conversations DB' },
  { id: 'payment', icon: CreditCard, label: 'Payments',          accent: 'emerald', desc: 'Razorpay / Stripe' },
  { id: 'email',   icon: Mail,       label: 'Email',             accent: 'blue',   desc: 'Amazon SES' },
  { id: 'push',    icon: Bell,       label: 'Push',              accent: 'orange', desc: 'OneSignal / FCM' },
  { id: 'analytics',icon: BarChart3, label: 'Analytics',         accent: 'pink',   desc: 'PostHog / GA4' },
  { id: 'auth',    icon: Shield,     label: 'Google Auth',       accent: 'red',    desc: 'OAuth 2.0' },
];

const ACCENT = {
  amber:   { text: 'text-amber-600',   bg: 'bg-amber-50',   border: 'border-amber-200',   btn: 'bg-amber-600 hover:bg-amber-700'   },
  violet:  { text: 'text-violet-600',  bg: 'bg-violet-50',  border: 'border-violet-200',  btn: 'bg-violet-600 hover:bg-violet-700' },
  cyan:    { text: 'text-cyan-600',    bg: 'bg-cyan-50',    border: 'border-cyan-200',    btn: 'bg-cyan-600 hover:bg-cyan-700'    },
  emerald: { text: 'text-emerald-600', bg: 'bg-emerald-50', border: 'border-emerald-200', btn: 'bg-emerald-600 hover:bg-emerald-700' },
  blue:    { text: 'text-blue-600',    bg: 'bg-blue-50',    border: 'border-blue-200',    btn: 'bg-blue-600 hover:bg-blue-700'    },
  orange:  { text: 'text-orange-600',  bg: 'bg-orange-50',  border: 'border-orange-200',  btn: 'bg-orange-600 hover:bg-orange-700'},
  pink:    { text: 'text-pink-600',    bg: 'bg-pink-50',    border: 'border-pink-200',    btn: 'bg-pink-600 hover:bg-pink-700'    },
  red:     { text: 'text-red-600',     bg: 'bg-red-50',     border: 'border-red-200',     btn: 'bg-red-600 hover:bg-red-700'      },
};

function SecretInput({ value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input type={show ? 'text' : 'password'} value={value} onChange={onChange} placeholder={placeholder}
        className="w-full h-9 px-3 pr-8 rounded-xl text-sm text-gray-900 font-mono outline-none bg-gray-50 border border-gray-200 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20" />
      <button onClick={() => setShow(!show)} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
        {show ? <EyeOff size={13} /> : <Eye size={13} />}
      </button>
    </div>
  );
}

const inputStyle = "w-full h-9 px-3 rounded-xl text-sm text-gray-900 font-mono outline-none bg-gray-50 border border-gray-200 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20";

export default function AdminApiConfig({ adminToken, onNavigate }) {
  const [active, setActive] = useState('routing');
  const [creds, setCreds] = useState({ chatModelDefault: 'vertex/gemini-flash', emergentKey: '', emergentBaseUrl: '', supabaseUrl: '', supabaseServiceKey: '', supabaseAnonKey: '', razorpayKeyId: '', razorpayKeySecret: '', razorpayWebhookSecret: '', sesRegion: '', oneSignalKey: '', posthogKey: '', googleClientId: '', googleClientSecret: '' });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminGetApiConfig(adminToken)
      .then((res) => {
        const cfg = res.data;
        setCreds({
          chatModelDefault: cfg.chat_model?.default || 'vertex/gemini-flash',
          emergentKey: cfg.emergent?.key || '',
          emergentBaseUrl: cfg.emergent?.base_url || '',
          supabaseUrl: cfg.supabase?.url || '',
          supabaseServiceKey: cfg.supabase?.service_key || '',
          supabaseAnonKey: cfg.supabase?.anon_key || '',
          razorpayKeyId: cfg.payment?.razorpay_key_id || '',
          razorpayKeySecret: cfg.payment?.razorpay_key_secret || '',
          razorpayWebhookSecret: cfg.payment?.razorpay_webhook_secret || '',
          sesRegion: cfg.email?.ses_region || '',
          oneSignalKey: cfg.push?.onesignal_key || '',
          posthogKey: cfg.analytics?.posthog_key || '',
          googleClientId: cfg.google_auth?.client_id || '',
          googleClientSecret: cfg.google_auth?.client_secret || '',
        });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [adminToken]);

  const ac = SERVICES.find((s) => s.id === active);
  const colors = ACCENT[ac?.accent || 'violet'];

  const buildPayload = () => ({
    chat_model: { default: creds.chatModelDefault },
    emergent: { key: creds.emergentKey, base_url: creds.emergentBaseUrl },
    supabase: { url: creds.supabaseUrl, service_key: creds.supabaseServiceKey, anon_key: creds.supabaseAnonKey },
    payment: { razorpay_key_id: creds.razorpayKeyId, razorpay_key_secret: creds.razorpayKeySecret, razorpay_webhook_secret: creds.razorpayWebhookSecret },
    email: { ses_region: creds.sesRegion },
    push: { onesignal_key: creds.oneSignalKey },
    analytics: { posthog_key: creds.posthogKey },
    google_auth: { client_id: creds.googleClientId, client_secret: creds.googleClientSecret },
  });

  const adminAxios = (method, url, data) => axios({ method, url: `${API_BASE}${url}`, data, headers: adminHeaders(adminToken), withCredentials: true });

  const handleSave = async () => {
    setSaving(true);
    try {
      if (active === 'supabase') {
        await adminAxios('post', '/admin/supabase/apply', { url: creds.supabaseUrl, service_key: creds.supabaseServiceKey, anon_key: creds.supabaseAnonKey });
        toast.success('Supabase credentials applied and verified');
      } else {
        await adminUpdateApiConfig(adminToken, buildPayload());
        toast.success(`${ac?.label} config saved`);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true); setTestResult(null);
    try {
      if (active === 'supabase') {
        const res = await adminAxios('post', '/admin/supabase/test', { url: creds.supabaseUrl, service_key: creds.supabaseServiceKey });
        setTestResult({ ok: res.data.ok, data: res.data.message, error: res.data.error });
      } else if (active === 'chat_model') {
        const res = await adminAxios('get', '/health');
        const llmStatus = res.data?.dependencies?.llm?.status;
        const llmOk = llmStatus === 'ok';
        setTestResult({ ok: llmOk, data: llmOk ? `Chat default = ${creds.chatModelDefault}; LLM service healthy` : `LLM status: ${llmStatus || 'unknown'}` });
      } else if (active === 'routing') {
        const res = await adminAxios('get', '/admin/routing-config');
        const poolCount = (res.data?.pools || []).length;
        const lockedCount = (res.data?.pools || []).filter((p) => p.strict_primary_lock).length;
        setTestResult({ ok: poolCount > 0, data: poolCount > 0
          ? `Routing config reachable — ${poolCount} pools (${lockedCount} strict-primary locked).`
          : 'Routing config returned empty pools.' });
      } else if (active === 'emergent') {
        const hasKey = !!creds.emergentKey;
        setTestResult({ ok: hasKey, data: hasKey ? 'Emergent API key is configured (used for admin AI generation)' : 'No Emergent API key configured — other providers will be used as fallback' });
      } else if (active === 'deepgram' || active === 'workers_ai_indic' || active === 'mongodb_atlas') {
        const res = await adminAxios('get', '/admin/routing-config');
        const provName = active;
        const inAnyPool = (res.data?.pools || []).some((pool) =>
          (pool.providers || []).some((p) => p.name === provName)
        );
        setTestResult({ ok: inAnyPool, data: inAnyPool
          ? `${provName} is wired into the locked provider chain — see Routing & Pools tab.`
          : `${provName} is not present in any pool right now.` });
      } else if (active === 'payment') {
        const res = await adminAxios('get', '/health');
        const payStatus = res.data?.dependencies?.payment?.status;
        const payOk = payStatus === 'ok';
        setTestResult({ ok: payOk, data: payOk ? 'Payment service reachable' : `Payment status: ${payStatus || 'not_configured'}` });
      } else if (active === 'auth') {
        await axios.get('https://accounts.google.com/.well-known/openid-configuration');
        setTestResult({ ok: true, data: 'Google OAuth endpoint reachable' });
      } else {
        const hasKey = active === 'email' ? creds.sesRegion : active === 'push' ? creds.oneSignalKey : creds.posthogKey;
        setTestResult({ ok: !!hasKey, data: hasKey ? 'API key is configured' : 'No API key configured' });
      }
    } catch (e) {
      setTestResult({ ok: false, error: e.message });
    } finally { setTesting(false); }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-gray-400 text-sm py-8">
        <Loader2 size={16} className="animate-spin" /> Loading API configuration...
      </div>
    );
  }

  return (
    <SectionErrorBoundary name="API Config">
      <div className="space-y-4 max-w-3xl">
        <div>
          <h2 className="text-lg font-bold text-gray-900">API Configuration</h2>
          <p className="text-sm text-gray-400 mt-0.5">Configure external service credentials and test connections</p>
        </div>

        <div className="flex gap-2 flex-wrap">
          {SERVICES.map(({id, icon: Icon, label, accent}) => {
            const c = ACCENT[accent];
            return (
              <button key={id} onClick={() => { setActive(id); setTestResult(null); }}
                className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium border transition-all ${active === id ? `${c.bg} ${c.border} ${c.text}` : 'border-gray-200 text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}>
                <Icon size={13} /> {label}
              </button>
            );
          })}
        </div>

        <div className="rounded-2xl border border-gray-200 overflow-hidden bg-white shadow-sm">
          <div className={`p-4 border-b ${colors.bg} ${colors.border}`}>
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${colors.bg} border ${colors.border}`}>
                {ac && <ac.icon size={18} className={colors.text} />}
              </div>
              <div>
                <p className={`font-bold ${colors.text}`}>{ac?.label}</p>
                <p className="text-xs text-gray-500">{ac?.desc}</p>
              </div>
            </div>
          </div>

          <div className="p-4 space-y-4">
            {active === 'routing' && (
              <RoutingPools adminToken={adminToken} />
            )}
            {active === 'deepgram' && (
              <div className="space-y-2 text-xs text-gray-600">
                <p>
                  <strong>Deepgram</strong> is the sole English STT primary (model <code className="font-mono">nova-3</code>).
                  Task #552 §G retired the legacy Deepgram TTS branch — ElevenLabs is now the sole
                  English TTS specialist; Google Cloud TTS Neural2 is the sole Indic TTS specialist.
                  Wired via CF AI Gateway BYOK; the <code className="font-mono">DEEPGRAM_API_KEY</code>
                  env var is only required when running outside the gateway.
                </p>
                <p>Pools touched: <code className="font-mono">stt</code>, <code className="font-mono">voice</code>.</p>
              </div>
            )}
            {active === 'workers_ai_indic' && (
              <div className="space-y-2 text-xs text-gray-600">
                <p>
                  <strong>Cloudflare Workers AI · IndicTrans2</strong> is the dedicated Indic neural MT model
                  (<code className="font-mono">@cf/ai4bharat/indictrans2-en-indic-1B</code>). Locked as the
                  primary in <code className="font-mono">translate</code> (weight 3000) and
                  <code className="font-mono">assamese_content</code> pools — Vertex Gemini stays at
                  weight 100 strictly for note-formatting fallback.
                </p>
                <p>No separate API key — uses the existing CF Workers AI gateway credentials.</p>
              </div>
            )}
            {active === 'mongodb_atlas' && (
              <div className="space-y-2 text-xs text-gray-600">
                <p>
                  <strong>MongoDB Atlas $vectorSearch</strong> is the weight-0 fallback in the
                  <code className="font-mono">vector_search</code> pool. Pinecone wins every healthy
                  draw; Atlas only fires when Pinecone is excluded. Connection string lives in
                  <code className="font-mono">MONGO_URL</code> (true infrastructure secret — must
                  remain in Railway, not BYOK-substitutable).
                </p>
              </div>
            )}
            {active === 'supabase' && (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">Connect to Supabase for user accounts and conversation storage. Find credentials in your Supabase dashboard under Settings &gt; API.</p>
                <div><label className="text-xs text-gray-500 block mb-1" data-testid="label-supabase-url">Project URL</label>
                  <input value={creds.supabaseUrl} onChange={(e) => setCreds((c) => ({...c, supabaseUrl: e.target.value}))} placeholder="https://xxxxx.supabase.co" data-testid="input-supabase-url" className={inputStyle} />
                </div>
                <div><label className="text-xs text-gray-500 block mb-1" data-testid="label-supabase-service-key">Service Role Key</label>
                  <SecretInput value={creds.supabaseServiceKey} onChange={(e) => setCreds((c) => ({...c, supabaseServiceKey: e.target.value}))} placeholder="eyJhbGci..." />
                </div>
                <div><label className="text-xs text-gray-500 block mb-1" data-testid="label-supabase-anon-key">Anon Key (public)</label>
                  <SecretInput value={creds.supabaseAnonKey} onChange={(e) => setCreds((c) => ({...c, supabaseAnonKey: e.target.value}))} placeholder="eyJhbGci..." />
                </div>
              </div>
            )}
            {active === 'chat_model' && (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">
                  Choose which provider powers the user-facing chat stream.
                  Vertex AI Gemini Flash gives the lowest first-token latency.
                  If the active provider fails before the first token is sent,
                  the backend automatically falls back to the Workers AI pool —
                  citations, guardrails and rate limits are preserved either way.
                </p>
                <div>
                  <label className="text-xs text-gray-500 block mb-1" data-testid="label-chat-model-default">Active chat model</label>
                  <select
                    value={creds.chatModelDefault}
                    onChange={(e) => setCreds((c) => ({...c, chatModelDefault: e.target.value}))}
                    data-testid="select-chat-model-default"
                    className={inputStyle}
                  >
                    <option value="vertex/gemini-flash">Vertex AI — Gemini Flash (recommended, lowest TTFT)</option>
                    <option value="workers-ai/@cf/openai/gpt-oss-20b">Workers AI — gpt-oss-20b (fallback)</option>
                  </select>
                </div>
                <p className="text-[11px] text-gray-400">
                  Vertex requires <code className="font-mono">VERTEX_PROJECT_ID</code> and either Application Default Credentials
                  or <code className="font-mono">VERTEX_SERVICE_ACCOUNT_JSON</code> set in the backend env.
                </p>
                {/* Task #626 — deep-link to the Chat Speed-up card so
                    the admin can see whether Vertex is actually faster
                    than legacy in production before switching. */}
                {typeof onNavigate === 'function' && (
                  <button
                    type="button"
                    onClick={() => onNavigate('dashboard', { scrollTo: 'chat-speedup-providers' })}
                    data-testid="link-chat-provider-comparison"
                    className="inline-flex items-center gap-1 text-xs font-medium text-violet-600 hover:text-violet-700 hover:underline"
                  >
                    View Vertex vs legacy comparison →
                  </button>
                )}
              </div>
            )}
            {active === 'emergent' && (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">Emergent universal API key — powers all admin AI content generation (content hub, SEO, GEO). Highest priority provider; other keys serve as fallbacks.</p>
                <div><label className="text-xs text-gray-500 block mb-1">EMERGENT_API_KEY</label>
                  <SecretInput value={creds.emergentKey} onChange={(e) => setCreds((c) => ({...c, emergentKey: e.target.value}))} placeholder="em_..." />
                </div>
                <div><label className="text-xs text-gray-500 block mb-1">Base URL (optional)</label>
                  <input value={creds.emergentBaseUrl} onChange={(e) => setCreds((c) => ({...c, emergentBaseUrl: e.target.value}))} placeholder="https://api.emergent.sh/v1" className={inputStyle} />
                </div>
              </div>
            )}
            {active === 'payment' && (
              <div className="space-y-3">
                <div><label className="text-xs text-gray-500 block mb-1">Razorpay Key ID</label>
                  <input value={creds.razorpayKeyId} onChange={(e) => setCreds((c) => ({...c, razorpayKeyId: e.target.value}))} placeholder="rzp_live_..." className={inputStyle} />
                </div>
                <div><label className="text-xs text-gray-500 block mb-1">Razorpay Key Secret</label>
                  <SecretInput value={creds.razorpayKeySecret} onChange={(e) => setCreds((c) => ({...c, razorpayKeySecret: e.target.value}))} placeholder="secret..." />
                </div>
                <div><label className="text-xs text-gray-500 block mb-1">Razorpay Webhook Secret</label>
                  <SecretInput value={creds.razorpayWebhookSecret} onChange={(e) => setCreds((c) => ({...c, razorpayWebhookSecret: e.target.value}))} placeholder="webhook_secret..." />
                </div>
              </div>
            )}
            {active === 'email' && (
              <div><label className="text-xs text-gray-500 block mb-1">Amazon SES Region</label>
                <input value={creds.sesRegion} onChange={(e) => setCreds((c) => ({...c, sesRegion: e.target.value}))} placeholder="us-east-1" className={inputStyle} />
                <p className="text-[11px] text-gray-500 mt-1">SES credentials live in env vars (AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY); only the region is configurable here.</p>
              </div>
            )}
            {active === 'push' && (
              <div><label className="text-xs text-gray-500 block mb-1">OneSignal API Key</label>
                <SecretInput value={creds.oneSignalKey} onChange={(e) => setCreds((c) => ({...c, oneSignalKey: e.target.value}))} placeholder="os_..." />
              </div>
            )}
            {active === 'analytics' && (
              <div><label className="text-xs text-gray-500 block mb-1">PostHog API Key</label>
                <SecretInput value={creds.posthogKey} onChange={(e) => setCreds((c) => ({...c, posthogKey: e.target.value}))} placeholder="phc_..." />
              </div>
            )}
            {active === 'auth' && (
              <div className="space-y-3">
                <div><label className="text-xs text-gray-500 block mb-1">Google Client ID</label>
                  <input value={creds.googleClientId} onChange={(e) => setCreds((c) => ({...c, googleClientId: e.target.value}))} placeholder="xxx.apps.googleusercontent.com" className={inputStyle} />
                </div>
                <div><label className="text-xs text-gray-500 block mb-1">Google Client Secret</label>
                  <SecretInput value={creds.googleClientSecret} onChange={(e) => setCreds((c) => ({...c, googleClientSecret: e.target.value}))} placeholder="GOCSPX-..." />
                </div>
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <button onClick={handleTest} disabled={testing}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors">
                {testing ? <Loader2 size={12} className="animate-spin" /> : <TestTube2 size={12} />} Test Connection
              </button>
              <button onClick={handleSave} disabled={saving}
                className={`flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white ${colors.btn} transition-colors`}>
                {saving ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />} Deploy
              </button>
            </div>

            {testResult && (
              <div className={`rounded-xl p-3 text-xs ${testResult.ok ? 'bg-emerald-50 border border-emerald-200 text-emerald-600' : 'bg-red-50 border border-red-200 text-red-600'}`}>
                {testResult.ok ? `✓ ${testResult.data}` : `✗ Error: ${testResult.error || testResult.data}`}
              </div>
            )}
          </div>
        </div>
        <AdminQuickLinks links={['vertex','health','settings','googleauth','ratelimits']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
