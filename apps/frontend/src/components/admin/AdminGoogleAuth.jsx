import { useState, useEffect } from 'react';
import { CheckCircle2, Eye, EyeOff, TestTube2, Loader2 } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import { toast } from 'sonner';
import { adminGetApiConfig, adminUpdateApiConfig } from '@/utils/api';
import axios from 'axios';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
export default function AdminGoogleAuth({ adminToken, onNavigate }) {
  const [clientId, setClientId]     = useState('');
  const [clientSecret, setSecret]   = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [enabled, setEnabled]       = useState(false);
  const [testing, setTesting]       = useState(false);
  const [result, setResult]         = useState(null);
  const [saving, setSaving]         = useState(false);
  const [loading, setLoading]       = useState(true);
  const [fullConfig, setFullConfig]  = useState(null);

  const redirectUri = `${window.location.origin}/auth/v1/callback`;

  useEffect(() => {
    if (!adminToken) return;
    adminGetApiConfig(adminToken)
      .then((res) => {
        const cfg = res.data;
        setFullConfig(cfg);
        setClientId(cfg.google_auth?.client_id || '');
        setSecret(cfg.google_auth?.client_secret || '');
        setEnabled(cfg.google_auth?.enabled || false);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [adminToken]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {
        ...(fullConfig || {}),
        google_auth: {
          client_id: clientId,
          client_secret: clientSecret,
          enabled,
        },
      };
      await adminUpdateApiConfig(adminToken, payload);
      setFullConfig(payload);
      toast.success('Google Auth configuration saved');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!clientId) { toast.error('Client ID required'); return; }
    setTesting(true); setResult(null);
    try {
      await axios.get('https://accounts.google.com/.well-known/openid-configuration');
      setResult({ ok: true });
      toast.success('Google OAuth endpoint reachable');
    } catch { setResult({ ok: false, error: 'Google endpoint unreachable' }); } finally { setTesting(false); }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-gray-400 text-sm py-8">
        <Loader2 size={16} className="animate-spin" /> Loading Google Auth configuration...
      </div>
    );
  }

  const inputClass = "w-full h-9 px-3 rounded-xl text-sm text-gray-900 outline-none bg-gray-50 border border-gray-200 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20";

  return (
    <SectionErrorBoundary name="Google Auth">
      <div className="space-y-6 max-w-2xl">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Google OAuth Configuration</h2>
          <p className="text-sm text-gray-400 mt-0.5">Enable one-click Google login for students</p>
        </div>

        <div className="rounded-xl p-4 bg-blue-50 border border-blue-200">
          <p className="text-sm text-blue-700 font-medium mb-3">Setup instructions</p>
          <ol className="space-y-2">
            {[
              'Go to Google Cloud Console → Credentials',
              'Create OAuth 2.0 Client ID (Web application)',
              `Add redirect URI: ${redirectUri}`,
              'Copy Client ID and Client Secret below',
              'Toggle Enable Google Login and click Save',
            ].map((step, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-blue-600">
                <span className="w-5 h-5 rounded-full bg-blue-100 flex items-center justify-center text-[10px] font-bold text-blue-700 flex-shrink-0 mt-0.5">{i+1}</span>
                {step}
              </li>
            ))}
          </ol>
        </div>

        <div className="rounded-2xl border border-gray-200 p-5 space-y-4 bg-white shadow-sm">
          <div>
            <label className="text-xs text-gray-500 block mb-1">Google Client ID</label>
            <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="xxx.apps.googleusercontent.com"
              className={inputClass} />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Google Client Secret</label>
            <div className="relative">
              <input type={showSecret ? 'text' : 'password'} value={clientSecret} onChange={(e) => setSecret(e.target.value)} placeholder="GOCSPX-..."
                className={`${inputClass} pr-8 font-mono`} />
              <button onClick={() => setShowSecret(!showSecret)} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                {showSecret ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Redirect URI (read-only)</label>
            <input readOnly value={redirectUri} className="w-full h-9 px-3 rounded-xl text-sm text-gray-400 font-mono outline-none bg-gray-50 border border-gray-100" />
          </div>
          <div className="flex items-center justify-between py-2 border-t border-gray-100">
            <div>
              <p className="text-sm text-gray-900">Enable Google Login</p>
              <p className="text-xs text-gray-400">Allow students to sign in with Google</p>
            </div>
            <button onClick={() => setEnabled(!enabled)}
              className="w-11 h-6 rounded-full transition-all" style={{ background: enabled ? '#7c3aed' : '#d1d5db' }}>
              <div className={`w-5 h-5 rounded-full bg-white transition-transform mx-0.5 shadow-sm ${enabled ? 'translate-x-5' : 'translate-x-0'}`} />
            </button>
          </div>
          <div className="flex gap-2">
            <button onClick={handleTest} disabled={testing}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium border border-gray-200 text-gray-600 hover:bg-gray-50">
              {testing ? <Loader2 size={12} className="animate-spin" /> : <TestTube2 size={12} />} Test
            </button>
            <button onClick={handleSave} disabled={saving}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold text-white bg-red-600 hover:bg-red-700 transition-colors">
              {saving ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />} Save Config
            </button>
          </div>
          {result && (
            <div className={`rounded-xl p-3 text-xs ${result.ok ? 'bg-emerald-50 border border-emerald-200 text-emerald-600' : 'bg-red-50 border border-red-200 text-red-600'}`}>
              {result.ok ? '✓ Google OAuth endpoint reachable' : `✗ ${result.error}`}
            </div>
          )}
        </div>
        <AdminQuickLinks links={['settings','apiconfig','users']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
