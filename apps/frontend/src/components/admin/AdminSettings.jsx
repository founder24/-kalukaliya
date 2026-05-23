import { useState, useEffect } from 'react';
import { Save, Loader2, Settings, Trash2, AlertTriangle } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { adminGetSettings, adminUpdateSettings, adminPurgeAllCache } from '@/utils/api';
import { toast } from 'sonner';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
export default function AdminSettings({ adminToken, onNavigate }) {
  const [settings, setSettings] = useState({
    registrations_open: true,
    maintenance_mode: false,
    app_name: 'Syrabit.ai',
    tagline: 'AI-Powered AHSEC Exam Prep',
    crawl_coverage_red: 30,
    crawl_coverage_yellow: 50,
    bot_missing_days: 3,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [purging, setPurging] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [generalError, setGeneralError] = useState(null);

  useEffect(() => {
    adminGetSettings(adminToken)
      .then((res) => setSettings((prev) => ({ ...prev, ...res.data })))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [adminToken]);

  const validateThresholds = (s) => {
    const errors = {};
    const red = Number(s.crawl_coverage_red);
    const yellow = Number(s.crawl_coverage_yellow);
    const days = Number(s.bot_missing_days);
    if (isNaN(red) || red < 0 || red > 100) errors.crawl_coverage_red = 'Must be 0–100';
    if (isNaN(yellow) || yellow < 0 || yellow > 100) errors.crawl_coverage_yellow = 'Must be 0–100';
    if (!isNaN(red) && !isNaN(yellow) && red >= 0 && red <= 100 && yellow >= 0 && yellow <= 100 && red >= yellow) {
      errors.crawl_coverage_red = `Red (${red}) must be less than Yellow (${yellow})`;
    }
    if (isNaN(days) || days < 1 || days > 90) errors.bot_missing_days = 'Must be 1–90';
    return errors;
  };

  const handleFieldChange = (field, value) => {
    const next = { ...settings, [field]: value };
    setSettings(next);
    if (['crawl_coverage_red', 'crawl_coverage_yellow', 'bot_missing_days'].includes(field)) {
      const errors = validateThresholds(next);
      setFieldErrors(errors);
    }
    if (generalError) setGeneralError(null);
  };

  const parseBackendError = (err) => {
    const resp = err?.response;
    if (!resp) return { general: 'Network error — could not reach server' };
    const detail = resp.data?.detail;
    if (resp.status === 422 && Array.isArray(detail)) {
      const errors = {};
      for (const item of detail) {
        const loc = item.loc || [];
        const field = loc[loc.length - 1];
        if (['crawl_coverage_red', 'crawl_coverage_yellow', 'bot_missing_days'].includes(field)) {
          errors[field] = item.msg || 'Invalid value';
        } else {
          errors.general = item.msg || 'Validation error';
        }
      }
      return Object.keys(errors).length ? errors : { general: 'Validation failed' };
    }
    if (typeof detail === 'string') {
      const lower = detail.toLowerCase();
      if (lower.includes('crawl_coverage_red') || (lower.includes('red') && lower.includes('yellow')))
        return { crawl_coverage_red: detail };
      if (lower.includes('crawl_coverage_yellow')) return { crawl_coverage_yellow: detail };
      if (lower.includes('bot_missing_days')) return { bot_missing_days: detail };
      return { general: detail };
    }
    return { general: detail || `Server error (${resp.status})` };
  };

  const hasErrors = Object.values(fieldErrors).some(Boolean);

  const handleSave = async () => {
    const errors = validateThresholds(settings);
    setFieldErrors(errors);
    if (Object.keys(errors).length) return;
    setSaving(true);
    setGeneralError(null);
    try {
      await adminUpdateSettings(adminToken, settings);
      setFieldErrors({});
      toast.success('Settings saved');
    } catch (err) {
      const parsed = parseBackendError(err);
      const { general, ...fields } = parsed;
      if (Object.keys(fields).length) setFieldErrors(prev => ({ ...prev, ...fields }));
      if (general) setGeneralError(general);
      else if (!Object.keys(fields).length) setGeneralError('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="flex justify-center p-10"><Loader2 size={24} className="animate-spin text-violet-500" /></div>;

  return (
    <SectionErrorBoundary name="Settings">
      <div className="p-6 max-w-lg space-y-6">
        <h2 className="text-gray-900 font-semibold">App Settings</h2>

        <div className="rounded-2xl p-5 space-y-5 bg-white border border-gray-200 shadow-sm">
          <div className="space-y-1.5">
            <Label className="text-gray-500 text-sm">App Name</Label>
            <Input
              value={settings.app_name}
              onChange={(e) => setSettings((s) => ({ ...s, app_name: e.target.value }))}
              className="bg-gray-50 border-gray-200 text-gray-900 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-gray-500 text-sm">Tagline</Label>
            <Input
              value={settings.tagline}
              onChange={(e) => setSettings((s) => ({ ...s, tagline: e.target.value }))}
              className="bg-gray-50 border-gray-200 text-gray-900 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
            />
          </div>

          <div className="flex items-center justify-between py-3 border-t border-gray-100">
            <div>
              <p className="text-gray-700 text-sm font-medium">Registrations Open</p>
              <p className="text-gray-400 text-xs">Allow new users to sign up</p>
            </div>
            <Switch
              checked={settings.registrations_open}
              onCheckedChange={(v) => setSettings((s) => ({ ...s, registrations_open: v }))}
            />
          </div>

          <div className="flex items-center justify-between py-3 border-t border-gray-100">
            <div>
              <p className="text-gray-700 text-sm font-medium">Maintenance Mode</p>
              <p className="text-gray-400 text-xs">Show maintenance page to students</p>
            </div>
            <Switch
              checked={settings.maintenance_mode}
              onCheckedChange={(v) => setSettings((s) => ({ ...s, maintenance_mode: v }))}
            />
          </div>
        </div>

        <div className="rounded-2xl p-5 space-y-5 bg-white border border-gray-200 shadow-sm">
          <h3 className="text-gray-700 text-sm font-medium">Bot Alert Thresholds</h3>
          <p className="text-gray-400 text-xs">Configure when bot traffic alerts trigger on the dashboard.</p>

          <div className="space-y-1.5">
            <Label className="text-gray-500 text-sm">Crawl Coverage Red (%)</Label>
            <Input
              type="number"
              min={0}
              max={100}
              value={settings.crawl_coverage_red}
              onChange={(e) => handleFieldChange('crawl_coverage_red', parseInt(e.target.value, 10) || 0)}
              className={`bg-gray-50 text-gray-900 focus:ring-2 ${
                fieldErrors.crawl_coverage_red
                  ? 'border-red-300 focus:border-red-400 focus:ring-red-500/20'
                  : 'border-gray-200 focus:border-violet-400 focus:ring-violet-500/20'
              }`}
            />
            {fieldErrors.crawl_coverage_red ? (
              <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={11} />{fieldErrors.crawl_coverage_red}</p>
            ) : (
              <p className="text-gray-400 text-xs">Coverage below this % triggers a red alert</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="text-gray-500 text-sm">Crawl Coverage Yellow (%)</Label>
            <Input
              type="number"
              min={0}
              max={100}
              value={settings.crawl_coverage_yellow}
              onChange={(e) => handleFieldChange('crawl_coverage_yellow', parseInt(e.target.value, 10) || 0)}
              className={`bg-gray-50 text-gray-900 focus:ring-2 ${
                fieldErrors.crawl_coverage_yellow
                  ? 'border-red-300 focus:border-red-400 focus:ring-red-500/20'
                  : 'border-gray-200 focus:border-violet-400 focus:ring-violet-500/20'
              }`}
            />
            {fieldErrors.crawl_coverage_yellow ? (
              <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={11} />{fieldErrors.crawl_coverage_yellow}</p>
            ) : (
              <p className="text-gray-400 text-xs">Coverage below this % triggers a yellow alert</p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="text-gray-500 text-sm">Bot Inactivity Window (days)</Label>
            <Input
              type="number"
              min={1}
              max={90}
              value={settings.bot_missing_days}
              onChange={(e) => handleFieldChange('bot_missing_days', parseInt(e.target.value, 10) || 0)}
              className={`bg-gray-50 text-gray-900 focus:ring-2 ${
                fieldErrors.bot_missing_days
                  ? 'border-red-300 focus:border-red-400 focus:ring-red-500/20'
                  : 'border-gray-200 focus:border-violet-400 focus:ring-violet-500/20'
              }`}
            />
            {fieldErrors.bot_missing_days ? (
              <p className="text-red-500 text-xs flex items-center gap-1"><AlertTriangle size={11} />{fieldErrors.bot_missing_days}</p>
            ) : (
              <p className="text-gray-400 text-xs">Days without key bot activity before alerting</p>
            )}
          </div>
        </div>

        {generalError && (
          <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 border border-red-200">
            <AlertTriangle size={14} />
            {generalError}
          </div>
        )}

        <Button
          onClick={handleSave}
          disabled={saving || hasErrors}
          className="text-white border-0 transition-all hover:opacity-90 bg-violet-600 hover:bg-violet-700 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? <Loader2 size={16} className="animate-spin mr-2" /> : <Save size={16} className="mr-2" />}
          Save Settings
        </Button>

        <div className="rounded-2xl p-5 space-y-3 bg-white border border-gray-200 shadow-sm">
          <div>
            <p className="text-gray-700 text-sm font-medium">Purge All Content Cache</p>
            <p className="text-gray-400 text-xs">Clears backend caches and Cloudflare edge cache for all content routes. Users will see fresh data immediately.</p>
          </div>
          <Button
            onClick={async () => {
              setPurging(true);
              try {
                const res = await adminPurgeAllCache(adminToken);
                const cfStatus = res.data?.cloudflare_purged ? 'Cloudflare edge purged' : 'Cloudflare purge skipped (not configured)';
                toast.success(`Cache purged. ${cfStatus}`);
              } catch {
                toast.error('Failed to purge cache');
              } finally {
                setPurging(false);
              }
            }}
            disabled={purging}
            variant="outline"
            className="border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700"
          >
            {purging ? <Loader2 size={16} className="animate-spin mr-2" /> : <Trash2 size={16} className="mr-2" />}
            Purge All Content Cache
          </Button>
        </div>

        <AdminQuickLinks links={['apiconfig','googleauth','health','ratelimits']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
