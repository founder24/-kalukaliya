import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { toast } from 'sonner';
import { log } from '@/utils/logger';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { computeHeavyFreshness } from '@/utils/metricsFreshness';
import { usePushNotifications } from '@/hooks/usePushNotifications';
import { pushChannelTone } from '@/utils/pushChannelTone';
import { TODAY_BUCKET_CAPTION, UTC_MIDNIGHT_IN_IST } from '@/utils/time';
import axios from 'axios';
import {
  adminGetDashboard, adminGetCfOverview, seoPipelineStatus,
  adminSeoHealthHistory, adminSeoHealthSnapshotNow, seoHealthLive,
  seoHealthDeepScan, adminSeoDeepScanHistory, adminGetAlertCooldowns, API_BASE,
} from '@/utils/api';
import {
  Users, MessageSquare, BookOpen, Zap, Loader2, Activity,
  ArrowRight, PenTool, Settings, Eye, TrendingUp, RefreshCw,
  UserPlus, Globe, Search, Bot, BarChart2, Server, Clock,
  CheckCircle, AlertCircle, AlertTriangle, Wifi, Database, DollarSign, Crown,
  Layers, Link2, FileCheck, Target, Cpu, ShieldCheck, Smartphone,
  Volume2, VolumeX, Bell, BellOff, RotateCcw, Upload, Trash2, Music, X,
  ShieldAlert, UserCheck, Cloud,
} from 'lucide-react';
import AudioTrimPreview from './AudioTrimPreview';
import AdminDraftServedSubjects from './AdminDraftServedSubjects';
import AdminQuickLinks from './AdminQuickLinks';
import {
  GlassCard, StatCard, DepStatusCard, formatTimeAgo, alertColor, adminHdr,
  normalizeChatFallbacks, normalizeLatency, normalizeTokenSpend,
  normalizeTopQueries, normalizeChatSpeedups, normalizeVectorStats,
} from './dashboard/shared';
import AiHealthWidget from './dashboard/AiHealthWidget';
import TrafficWidget from './dashboard/TrafficWidget';
import SeoWidget from './dashboard/SeoWidget';
import ChatWidget from './dashboard/ChatWidget';
import UserAnalyticsWidget from './dashboard/UserAnalyticsWidget';
import ActivityWidget from './dashboard/ActivityWidget';

export default function AdminDashboard({ adminToken, onNavigate, navContext }) {
  const [data, setData] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  // Task #398 — render-only tick so the "Last updated Xs ago" badge on
  // the Revenue section advances visibly between the 60s polls. We
  // only flip this counter; the actual age math reads ``Date.now()``
  // at render so the value is always current. Mirrors the metricsMeta
  // tick pattern from AdminHealth.jsx (Task #396).
  const [, setMetricsMetaTick] = useState(0);

  const [ragAccuracy, setRagAccuracy] = useState(null);
  const [chatFallbacks, setChatFallbacks] = useState(null);
  const [vectorStats, setVectorStats] = useState(null);
  const [latency, setLatency] = useState(null);
  const [chatSpeedups, setChatSpeedups] = useState(null);
  const [speedupDays, setSpeedupDays] = useState(7);
  const [speedupLoading, setSpeedupLoading] = useState(false);
  // Task #810 — anonymous-quota wall card. Independent of the main
  // dashboard payload because the period picker and the
  // "Backfill today" action both re-fetch this card on demand
  // without touching the rest of the dashboard cache.
  const [anonQuotaWall, setAnonQuotaWall] = useState(null);
  const [anonQuotaDays, setAnonQuotaDays] = useState(7);
  const [anonQuotaLoading, setAnonQuotaLoading] = useState(false);
  const [anonQuotaBackfilling, setAnonQuotaBackfilling] = useState(false);
  const [topQueries, setTopQueries] = useState(null);
  const [tokenSpend, setTokenSpend] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [pwaStats, setPwaStats] = useState(null);
  const [botAnalytics, setBotAnalytics] = useState(null);
  // Cloudflare AI Crawl Control — sourced from CF GraphQL via the
  // /admin/analytics/cf-ai-crawl-control route. When CF analytics
  // credentials are missing the route returns `available: false` with a
  // reason so we render an empty-state card instead of hiding the section.
  const [cfCrawlControl, setCfCrawlControl] = useState(null);
  // Cloudflare Account Analytics overview — re-fetched whenever the
  // user clicks 24h / 7d / 30d on the Traffic card. Independent of the
  // dashboard payload so the selector responds instantly without
  // blowing the whole dashboard cache.
  const [cfRange, setCfRange] = useState('7d');
  const [cfOverview, setCfOverview] = useState(null);
  const [cfOverviewLoading, setCfOverviewLoading] = useState(false);
  // Task #13 — nightly SEO prewarm coverage tile. Sourced from
  // /admin/seo/prewarm-coverage which returns the most recent
  // aca_jobs.prewarm_seo_routes run summary (combined success_rate
  // + KV-only kv_success_rate so a degraded materialization path is
  // visible even when edge-only legs stay healthy).
  const [prewarmCoverage, setPrewarmCoverage] = useState(null);
  const [indexNowStats, setIndexNowStats] = useState(null);
  const [indexNowHistory, setIndexNowHistory] = useState(null);
  const [retryingEndpoint, setRetryingEndpoint] = useState(null);
  const [resubmittingIndexNow, setResubmittingIndexNow] = useState(false);
  const [resubmitMessage, setResubmitMessage] = useState('');
  const [alertHistory, setAlertHistory] = useState(null);
  // Task #991 — surface the persistent alert-cooldown active count
  // (from `/admin/alerts/cooldowns?only_active=true`) directly on the
  // dashboard so on-call admins can spot "alert is being suppressed"
  // without having to drill into Bot Security → Suppressed Alerts.
  // Polled every 60s on the same cadence as the rest of the dashboard
  // header counters (see AdminPage.jsx unack-alerts polling).
  const [cooldownActiveCount, setCooldownActiveCount] = useState(0);
  const [seoHealth, setSeoHealth] = useState(null);
  const [seoHealthRefreshing, setSeoHealthRefreshing] = useState(false);
  const [seoLive, setSeoLive] = useState(null);
  const [seoLiveLoading, setSeoLiveLoading] = useState(false);
  const [seoLiveError, setSeoLiveError] = useState(null);
  // Task #461 — manual D1 sync trigger + post-sync result rendering.
  // The backend (`POST /admin/d1-sync`, see admin_content.py) returns
  // the primary `sync_full` result merged with an `extended_mirror`
  // sub-block: `{ success, tables[], row_counts{}, reason? }`. Until
  // this state landed the UI dropped the extended-mirror block on the
  // floor, forcing operators to inspect the network tab to see if the
  // seo_meta / audit_log / syllabus_map mirror actually succeeded.
  const [d1SyncRunning, setD1SyncRunning] = useState(false);
  const [d1SyncResult, setD1SyncResult] = useState(null);
  const [d1SyncDurationMs, setD1SyncDurationMs] = useState(null);
  const [d1SyncError, setD1SyncError] = useState(null);
  // Task #299: which sitemap row is currently expanded to show its
  // failing URL list. Only one is open at a time to keep the card compact.
  const [expandedSitemap, setExpandedSitemap] = useState(null);
  // Task #345: per-sitemap deep-scan results, keyed by sitemap name.
  // Shape: { [name]: { loading, error, data } } where `data` is the
  // response from /admin/seo/sitemap-failing-urls (full failing list).
  const [sitemapDeepScans, setSitemapDeepScans] = useState({});
  // Task #350: auto-deep-scan summaries harvested by the alert loop
  // (Task #347) and persisted on db.alerts. Lets the on-call admin see
  // the true blast radius the moment they open the dashboard, with a
  // "fresh" indicator and a banner if any sitemap was auto-scanned in
  // the last hour.
  const [seoAutoDeepScans, setSeoAutoDeepScans] = useState(null);
  // Task #692 — alert filter selection persists in the URL query
  // string so admins can bookmark and share a focused view (e.g. drop
  // a `?alert_status=unacknowledged&alert_reason=foo` link into an
  // incident ticket). Initial state reads from the current URL so a
  // refresh restores the same view; a useEffect (below) syncs every
  // change back via history.replaceState (no extra entry in the back
  // stack — the dashboard isn't a navigable surface).
  const [alertFilter, setAlertFilter] = useState(() => {
    if (typeof window === 'undefined') return 'all';
    const v = new URLSearchParams(window.location.search).get('alert_status');
    return v === 'unacknowledged' || v === 'acknowledged' || v === 'all' ? v : 'all';
  });
  const [alertReasonFilter, setAlertReasonFilter] = useState(() => {
    if (typeof window === 'undefined') return '';
    return new URLSearchParams(window.location.search).get('alert_reason') || '';
  });
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    if (alertFilter && alertFilter !== 'all') {
      params.set('alert_status', alertFilter);
    } else {
      params.delete('alert_status');
    }
    if (alertReasonFilter) {
      params.set('alert_reason', alertReasonFilter);
    } else {
      params.delete('alert_reason');
    }
    const qs = params.toString();
    const next = `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(window.history.state, '', next);
    }
  }, [alertFilter, alertReasonFilter]);
  // Task #426: hide synthetic test alerts (from "Test alert delivery" button)
  // by default; admins can opt in via the "Show test alerts" toggle.
  const [showSyntheticAlerts, setShowSyntheticAlerts] = useState(false);
  const [alertSettingsOpen, setAlertSettingsOpen] = useState(false);
  const [alertSettings, setAlertSettings] = useState(null);
  const [alertSettingsDraft, setAlertSettingsDraft] = useState(null);
  const [alertSettingsSaving, setAlertSettingsSaving] = useState(false);
  const [failedSections, setFailedSections] = useState([]);
  const [notifPrefs, setNotifPrefs] = useState(null);
  const [notifPrefsSaving, setNotifPrefsSaving] = useState(false);
  const [notifPrefsOpen, setNotifPrefsOpen] = useState(false);
  const [pushDeliverySummary, setPushDeliverySummary] = useState(null);
  // Task #474 — most-recent SEO daily-summary email dispatches, surfaced
  // under the SEO summary opt-in toggle so admins can see whether the last
  // scheduled run actually emailed them (or was suppressed by quiet hours).
  const [seoSummaryDispatches, setSeoSummaryDispatches] = useState(null);
  // Task #476 — Cloudflare Workers KV usage snapshot from the edge
  // worker. ``null`` while loading; ``{ configured: false, ... }`` when
  // the edge URL/secret aren't set; ``{ configured: true, snapshot }``
  // when the worker responded. Surfaced in the prefs modal so admins can
  // see read/write counters & quota % at a glance and react before a
  // KV outage starts dropping pages and the analytics beacon.
  const [kvHealth, setKvHealth] = useState(null);
  // Task #510 — operator-toggled expansion of the per-isolate
  // breakdown under each kv-health binding row. Map<binding, bool> so
  // multiple bindings can be expanded independently. Starts collapsed
  // because most operators only need the aggregate; the breakdown is
  // useful when burn spikes and we need to know if one isolate is
  // responsible for most of it.
  const [kvExpandedIsolates, setKvExpandedIsolates] = useState({});
  // Task #315 — R2 cold-storage watchdog snapshot from the edge worker.
  // ``null`` while loading; ``{ configured, state?, ... }`` once the
  // backend responds. Surfaced as a tile so admins can confirm the
  // monthly lifecycle / Logpush-cap watchdog (Task #314) is still
  // happy between cron ticks without opening the Cloudflare dashboard.
  const [r2Health, setR2Health] = useState(null);
  // Task #322 — pending state for the inline "Reset" button on the
  // R2 watchdog-blind indicator. Cleared in the finally block of the
  // handler below so the button re-enables even if the POST throws.
  const [r2ResettingWatchdog, setR2ResettingWatchdog] = useState(false);
  const [r2Reevaluating, setR2Reevaluating] = useState(false);
  // Task #689 — Cached state of the periodic Gemini health probe
  // (Task #677). ``null`` while loading; ``{ status, last_check_ts,
  // reason, consecutive_failures, ... }`` once the backend responds.
  // Surfaced as a tile so admins can see *current* probe state without
  // grepping logs and waiting for the email/Slack alert.
  const [vertexProbe, setVertexProbe] = useState(null);
  // Task #470 — Latest GitHub Actions run for the backend + frontend
  // workflows. ``null`` while loading; ``{ configured: false, ... }``
  // when GITHUB_REPO isn't set; ``{ configured: true, runs: {...} }``
  // when the API responded. Surfaced so the on-call admin sees red CI
  // without leaving the app.
  const [ciStatus, setCiStatus] = useState(null);
  const [ciRerunning, setCiRerunning] = useState(null);
  // Task #434 — last_success_at / last_error for the browser-push
  // channel from /admin/alert-settings (channel_status.push). Surfaced
  // inline in the notifications tile so admins notice a degraded push
  // pipeline without drilling into Bot Security → Alert Settings.
  const [pushChannelStatus, setPushChannelStatus] = useState(null);
  const prevAlertIdsRef = useRef(new Set());
  const audioCtxRef = useRef(null);
  const customAudioRef = useRef(null);
  const chimeFileInputRef = useRef(null);
  const [chimeUploading, setChimeUploading] = useState(false);
  const [pendingChimeFile, setPendingChimeFile] = useState(null);
  const pushNotif = usePushNotifications({
    serverPushEnabled: notifPrefs?.push_enabled,
  });

  const alertSoundEnabled = notifPrefs?.sound_enabled ?? true;
  const chimeTone = notifPrefs?.chime_tone ?? 'default';

  const CHIME_TONES = {
    default: { label: 'Default', freqs: [880, 1100, 880], type: 'sine', dur: 0.5 },
    soft: { label: 'Soft', freqs: [440, 550, 440], type: 'sine', dur: 0.6 },
    urgent: { label: 'Urgent', freqs: [1200, 900, 1200, 900], type: 'square', dur: 0.4 },
    bell: { label: 'Bell', freqs: [1047, 1319, 1568], type: 'sine', dur: 0.7 },
  };

  const ALERT_SEVERITY_LABELS = {
    high_error_rate: 'High Error Rate',
    high_latency: 'High Latency',
    spoofed_bot_surge: 'Bot Surge',
    high_fallback_rate: 'High Fallback Rate',
    endpoint_down: 'Endpoint Down',
    auto_block_expired: 'Auto-Block Expired',
  };

  const loadNotifPrefs = useCallback(async () => {
    const [prefsResult, statsResult, settingsResult, dispResult, kvResult, r2Result, ciResult, vpResult] = await Promise.allSettled([
      axios.get(`${API_BASE}/admin/notification-prefs`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/push/delivery-stats?days=7`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/alert-settings`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/seo/daily-summary-dispatches?limit=5`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/kv-health`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/r2-storage-health`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/ci-status`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/vertex/probe-status`, adminHdr(adminToken)),
    ]);

    // Process results
    if (prefsResult.status === 'fulfilled') {
      setNotifPrefs(prefsResult.value.data);
    } else {
      log.error('Failed to load notification prefs', { error: prefsResult.reason?.message });
      setNotifPrefs({ sound_enabled: true, push_enabled: false, chime_tone: 'default', sound_severities: ['high_error_rate', 'high_latency', 'spoofed_bot_surge', 'high_fallback_rate', 'endpoint_down', 'auto_block_expired'], push_severities: ['high_error_rate', 'spoofed_bot_surge', 'endpoint_down', 'auto_block_expired'] });
    }
    if (statsResult.status === 'fulfilled') {
      setPushDeliverySummary(statsResult.value.data);
    } else {
      console.warn('AdminDashboard: /admin/push/delivery-stats fetch failed:', statsResult.reason);
    }
    if (settingsResult.status === 'fulfilled') {
      setPushChannelStatus(settingsResult.value.data?.channel_status?.push || null);
    } else {
      setPushChannelStatus(null);
    }
    if (dispResult.status === 'fulfilled') {
      setSeoSummaryDispatches(dispResult.value.data?.dispatches || []);
    } else {
      setSeoSummaryDispatches([]);
    }
    if (kvResult.status === 'fulfilled') {
      setKvHealth(kvResult.value.data || null);
    } else {
      setKvHealth({ configured: false, reason: 'Backend unreachable' });
    }
    if (r2Result.status === 'fulfilled') {
      setR2Health(r2Result.value.data || null);
    } else {
      setR2Health({ configured: false, reason: 'Backend unreachable' });
    }
    if (ciResult.status === 'fulfilled') {
      setCiStatus(ciResult.value.data || null);
    } else {
      setCiStatus({ configured: false, reason: 'Backend unreachable' });
    }
    if (vpResult.status === 'fulfilled') {
      setVertexProbe(vpResult.value.data || null);
    } else {
      setVertexProbe({ status: 'unknown', reason: 'Backend unreachable' });
    }
  }, [adminToken]);

  const saveNotifPrefs = useCallback(async (updates) => {
    const merged = { ...notifPrefs, ...updates };
    setNotifPrefs(merged);
    setNotifPrefsSaving(true);
    try {
      const res = await axios.put(`${API_BASE}/admin/notification-prefs`, merged, adminHdr(adminToken));
      setNotifPrefs(res.data);
    } catch (e) {
      log.error('Failed to save notification prefs', { error: e.message });
    } finally {
      setNotifPrefsSaving(false);
    }
  }, [adminToken, notifPrefs]);

  const toggleAlertSound = useCallback(() => {
    saveNotifPrefs({ sound_enabled: !alertSoundEnabled });
  }, [saveNotifPrefs, alertSoundEnabled]);

  const playAlertChime = useCallback((tone) => {
    try {
      const activeTone = tone || chimeTone;
      if (activeTone === 'custom' && notifPrefs?.custom_chime_url) {
        if (customAudioRef.current) {
          customAudioRef.current.pause();
          customAudioRef.current.currentTime = 0;
        }
        const audio = new Audio(notifPrefs.custom_chime_url);
        audio.volume = 0.5;
        customAudioRef.current = audio;
        audio.play().catch(() => {});
        return;
      }
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
      }
      const ctx = audioCtxRef.current;
      const now = ctx.currentTime;
      const toneConfig = CHIME_TONES[activeTone] || CHIME_TONES.default;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = toneConfig.type;
      const step = toneConfig.dur / toneConfig.freqs.length;
      toneConfig.freqs.forEach((f, i) => osc.frequency.setValueAtTime(f, now + i * step));
      gain.gain.setValueAtTime(0.3, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + toneConfig.dur);
      osc.start(now);
      osc.stop(now + toneConfig.dur);
    } catch (err) {
      // WebAudio/Oscillator can throw when the AudioContext is
      // suspended (e.g. before the first user gesture) or the tone
      // config is malformed — both are best-effort UX nice-to-haves
      // (the chime preview), so degrade silently with a debug log.
      console.debug('AdminDashboard: chime preview tone failed:', err?.message);
    }
  }, [chimeTone, notifPrefs?.custom_chime_url]);

  const handleChimeFileSelect = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const validTypes = ['audio/mpeg', 'audio/wav', 'audio/wave', 'audio/x-wav', 'audio/mp3'];
    if (!validTypes.includes(file.type)) {
      toast.error('Only MP3 and WAV files are supported');
      if (chimeFileInputRef.current) chimeFileInputRef.current.value = '';
      return;
    }
    if (file.size > 500 * 1024) {
      toast.error('File must be under 500 KB');
      if (chimeFileInputRef.current) chimeFileInputRef.current.value = '';
      return;
    }
    setPendingChimeFile(file);
    if (chimeFileInputRef.current) chimeFileInputRef.current.value = '';
  }, []);

  const handleChimeUploadConfirm = useCallback(async (fileToUpload) => {
    if (fileToUpload.size > 500 * 1024) {
      toast.error('Trimmed file exceeds 500 KB limit');
      return;
    }
    setChimeUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', fileToUpload);
      const res = await axios.post(`${API_BASE}/admin/notification-prefs/upload-chime`, formData, {
        ...adminHdr(adminToken),
        headers: { ...adminHdr(adminToken).headers, 'Content-Type': 'multipart/form-data' },
      });
      setNotifPrefs(res.data);
      setPendingChimeFile(null);
      toast.success('Custom chime uploaded');
    } catch (err) {
      const msg = err.response?.data?.detail || 'Upload failed';
      toast.error(msg);
    } finally {
      setChimeUploading(false);
    }
  }, [adminToken]);

  const handleDeleteCustomChime = useCallback(async () => {
    try {
      const res = await axios.delete(`${API_BASE}/admin/notification-prefs/custom-chime`, adminHdr(adminToken));
      setNotifPrefs(res.data);
      toast.success('Custom chime removed');
    } catch {
      toast.error('Failed to remove custom chime');
    }
  }, [adminToken]);

  useEffect(() => {
    if (!alertHistory?.alerts || !alertSoundEnabled) return;
    const soundSeverities = new Set(notifPrefs?.sound_severities || []);
    const currentUnack = alertHistory.alerts.filter(a => !a.acknowledged);
    const currentIds = new Set(currentUnack.map(a => a._id));
    const prevIds = prevAlertIdsRef.current;
    const newAlerts = currentUnack.filter(a => !prevIds.has(a._id));
    if (newAlerts.length > 0 && prevIds.size > 0) {
      const shouldSound = newAlerts.some(a => soundSeverities.has(a.type));
      if (shouldSound) playAlertChime();
    }
    // Cap prevAlertIdsRef to prevent unbounded memory growth
    if (currentIds.size > 1000) {
      const recent = [...currentIds].slice(-500);
      prevAlertIdsRef.current = new Set(recent);
    } else {
      prevAlertIdsRef.current = currentIds;
    }
  }, [alertHistory, alertSoundEnabled, notifPrefs, playAlertChime]);

  const headers = { withCredentials: true };
  const adminHdr = (token) => {
    const isJwt = token && typeof token === 'string' && token.split('.').length === 3;
    return isJwt ? { headers: { Authorization: `Bearer ${token}` }, withCredentials: true } : { withCredentials: true };
  };

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const [
        dashRes, metricsRes,
        ragAccRes, fallbackRes, vectorRes, latencyRes,
        queriesRes, tokenRes, funnelRes, coverageRes, pwaRes, botRes, cfCrawlRes, indexNowRes, indexNowHistRes,
        prewarmRes, alertHistRes, seoHealthRes,
      ] = await Promise.allSettled([
        adminGetDashboard(adminToken),
        axios.get(`${API_BASE}/admin/dashboard/metrics`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/rag/accuracy`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/chat/fallbacks`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/vector/stats`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/perf/latency`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/analytics/queries`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/billing/tokens`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/monetization/funnel`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/content/coverage`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/pwa/stats`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/analytics/bot-traffic?days=30`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/analytics/cf-ai-crawl-control?days=7`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/indexnow/stats`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/indexnow/history?limit=20`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/seo/prewarm-coverage`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/alerts?limit=50${showSyntheticAlerts ? '&include_synthetic=true' : ''}`, adminHdr(adminToken)),
        adminSeoHealthHistory(adminToken, 168),
      ]);
      const failed = [];
      if (dashRes.status === 'fulfilled') setData(dashRes.value.data); else { failed.push('overview'); setData(null); }
      if (metricsRes.status === 'fulfilled') setMetrics(metricsRes.value.data); else { failed.push('metrics'); setMetrics(null); }
      if (ragAccRes.status === 'fulfilled') setRagAccuracy(ragAccRes.value.data); else { failed.push('rag'); setRagAccuracy(null); }
      if (fallbackRes.status === 'fulfilled') setChatFallbacks(normalizeChatFallbacks(fallbackRes.value.data)); else { failed.push('fallbacks'); setChatFallbacks(null); }
      if (vectorRes.status === 'fulfilled') setVectorStats(normalizeVectorStats(vectorRes.value.data)); else { failed.push('vector'); setVectorStats(null); }
      if (latencyRes.status === 'fulfilled') setLatency(normalizeLatency(latencyRes.value.data)); else { failed.push('latency'); setLatency(null); }
      if (queriesRes.status === 'fulfilled') setTopQueries(normalizeTopQueries(queriesRes.value.data)); else { failed.push('queries'); setTopQueries(null); }
      if (tokenRes.status === 'fulfilled') setTokenSpend(normalizeTokenSpend(tokenRes.value.data)); else { failed.push('tokens'); setTokenSpend(null); }
      if (funnelRes.status === 'fulfilled') setFunnel(funnelRes.value.data); else { failed.push('funnel'); setFunnel(null); }
      if (coverageRes.status === 'fulfilled') setCoverage(coverageRes.value.data); else { failed.push('coverage'); setCoverage(null); }
      if (pwaRes.status === 'fulfilled') setPwaStats(pwaRes.value.data); else { failed.push('pwa'); setPwaStats(null); }
      if (botRes.status === 'fulfilled') setBotAnalytics(botRes.value.data); else { failed.push('bot-analytics'); setBotAnalytics(null); }
      if (cfCrawlRes.status === 'fulfilled') setCfCrawlControl(cfCrawlRes.value.data); else { failed.push('cf-ai-crawl-control'); setCfCrawlControl(null); }
      if (indexNowRes.status === 'fulfilled') setIndexNowStats(indexNowRes.value.data); else { failed.push('indexnow'); setIndexNowStats(null); }
      if (indexNowHistRes.status === 'fulfilled') setIndexNowHistory(indexNowHistRes.value.data); else setIndexNowHistory(null);
      if (alertHistRes.status === 'fulfilled') setAlertHistory(alertHistRes.value.data); else { failed.push('alerts'); setAlertHistory(null); }
      if (seoHealthRes.status === 'fulfilled') setSeoHealth(seoHealthRes.value.data); else { failed.push('seo-health'); setSeoHealth(null); }
      if (prewarmRes.status === 'fulfilled') setPrewarmCoverage(prewarmRes.value.data); else { failed.push('prewarm-coverage'); setPrewarmCoverage(null); }
      seoHealthLive()
        .then((r) => { setSeoLive(r.data); setSeoLiveError(null); })
        .catch((e) => { setSeoLive(null); setSeoLiveError(e?.message || 'Failed to load SEO health'); });
      // Task #350: piggy-back on the dashboard refresh — fetch the
      // most recent auto-deep-scan summary per sitemap so each row
      // can show the alert-loop's true blast-radius numbers without
      // the on-call admin having to re-click "Deep scan" per sitemap.
      adminSeoDeepScanHistory(adminToken)
        .then((r) => setSeoAutoDeepScans(r.data || null))
        .catch(() => setSeoAutoDeepScans(null));
      setFailedSections(failed);
      setLastRefresh(new Date());
    } catch (e) {
      log.error('Admin dashboard load failed', { error: e.message, status: e.response?.status });
      setFailedSections(['overview', 'metrics', 'rag', 'fallbacks', 'vector', 'latency', 'queries', 'tokens', 'funnel', 'coverage']);
    }
    finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [adminToken, showSyntheticAlerts]);

  useEffect(() => {
    load();
    loadNotifPrefs();
    const interval = setInterval(() => load(true), 60000);
    return () => clearInterval(interval);
  }, [load, loadNotifPrefs]);

  // Task #398 — 1s tick for the Revenue-section freshness badge.
  // Only runs while the metrics endpoint has actually returned a
  // ``_meta`` block (Task #396), so a failed /admin/dashboard/metrics
  // fetch doesn't leak a useless interval. Depends on a stable
  // boolean rather than the ``_meta`` object reference so the
  // interval isn't torn down and re-created on every successful
  // poll (which arrives as a new ``metrics`` object every 60s).
  const hasMetricsMeta = !!metrics?._meta;
  useEffect(() => {
    if (!hasMetricsMeta) return undefined;
    const id = setInterval(() => {
      setMetricsMetaTick((t) => (t + 1) % 1_000_000);
    }, 1000);
    return () => clearInterval(id);
  }, [hasMetricsMeta]);

  // Task #991 — poll the persistent alert-cooldown active count so the
  // Alert History header can surface a "N on hold" pill whenever the
  // 6h cross-worker cooldown is silencing alerts that would otherwise
  // fire. Cheap call (the route returns just `active_count` plus the
  // first row); we ask for limit=1 because the badge only needs the
  // count, never the body. Failures are swallowed — the badge simply
  // hides, matching the unack-count poll's behaviour in AdminPage.
  useEffect(() => {
    if (!adminToken) return undefined;
    const fetchCount = () => {
      adminGetAlertCooldowns(adminToken, { only_active: true, limit: 1 })
        .then((res) => setCooldownActiveCount(res.data?.active_count ?? 0))
        // On a transient failure, reset to 0 so a stale positive
        // count from an earlier successful poll doesn't keep the
        // badge visible and misleading. The next successful poll
        // will repopulate the real number.
        .catch(() => setCooldownActiveCount(0));
    };
    fetchCount();
    const id = setInterval(fetchCount, 60000);
    return () => clearInterval(id);
  }, [adminToken]);

  // Cloudflare Account Analytics overview — fetch on mount and whenever
  // the user clicks a different range pill on the Traffic card.
  const loadCfOverview = useCallback(async (range) => {
    if (!adminToken) return;
    setCfOverviewLoading(true);
    try {
      const r = await adminGetCfOverview(adminToken, range);
      setCfOverview(r.data || null);
    } catch (e) {
      log.error('Failed to load CF overview', { error: e.message });
      setCfOverview(null);
    } finally {
      setCfOverviewLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    loadCfOverview(cfRange);
  }, [cfRange, loadCfOverview]);

  // NOTE: previously kept a separate "locked 30d" CF fetch and used it to
  // drive the Unique Visitors tile on the Traffic card. That intentionally
  // ignored the 24h / 7d / 30d range pills, which (correctly) read as a
  // bug — clicking 24h or 7d left the visitors number frozen at the 30d
  // value while the other three tiles updated. The visitors tile now
  // follows the active `cfRange` like every other tile, so this dedicated
  // fetch was removed.

  // Dedicated 24-hour unique-visitors fetch for the Unique Visitors stat card.
  // Uses the hourly CF dataset (`httpRequests1hGroups`) so the total is the
  // rolling 24-hour unique count and the "last hour" sub-value is the most
  // recent hourly bucket. Refreshes every 5 minutes.
  const [cfVisitors24h, setCfVisitors24h] = useState(null);
  useEffect(() => {
    if (!adminToken) return;
    let cancelled = false;
    const fetch24h = async () => {
      try {
        const r = await adminGetCfOverview(adminToken, '24h');
        if (!cancelled && r?.data?.totals?.visitors != null) {
          setCfVisitors24h(r.data);
        }
      } catch (e) {
        log.warn('CF 24h visitors fetch failed', { error: e.message });
      }
    };
    fetch24h();
    const interval = setInterval(fetch24h, 5 * 60 * 1000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [adminToken]);

  const loadChatSpeedups = useCallback(async (days) => {
    setSpeedupLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/chat/speedups?days=${days}`, adminHdr(adminToken));
      setChatSpeedups(normalizeChatSpeedups(res.data));
    } catch (e) {
      log.error('Failed to load chat speedups', { error: e.message });
      setChatSpeedups(null);
    } finally {
      setSpeedupLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken]);

  useEffect(() => {
    loadChatSpeedups(speedupDays);
    const interval = setInterval(() => loadChatSpeedups(speedupDays), 60000);
    return () => clearInterval(interval);
  }, [loadChatSpeedups, speedupDays]);

  // Task #810 — pull the anonymous-quota wall stats produced by Task
  // #798's `chat.anon_quota_exhausted` counter. The endpoint can be
  // called with `?backfill=1` to seed today's chart from existing
  // Redis device-credit counters; we only do that on explicit click
  // so the auto-refresh path stays a cheap memory read.
  // We track fetch errors separately from `anonQuotaWall === null` so
  // the card can show an honest "couldn't load" banner instead of a
  // grid of zeros that would otherwise be indistinguishable from a
  // real "no devices hit the wall today" state.
  const [anonQuotaError, setAnonQuotaError] = useState(null);
  const loadAnonQuotaWall = useCallback(async (days, withBackfill = false) => {
    if (withBackfill) setAnonQuotaBackfilling(true);
    else setAnonQuotaLoading(true);
    try {
      const url = `${API_BASE}/admin/chat/anon-quota-exhausted?days=${days}${withBackfill ? '&backfill=1' : ''}`;
      const res = await axios.get(url, adminHdr(adminToken));
      setAnonQuotaWall(res.data);
      setAnonQuotaError(null);
      if (withBackfill) {
        const n = res.data?.backfilled_today ?? 0;
        toast.success(n > 0 ? `Backfilled ${n} device${n === 1 ? '' : 's'} for today` : 'Already up-to-date — nothing to backfill');
      }
    } catch (e) {
      log.error('Failed to load anon-quota wall stats', { error: e.message, status: e.response?.status });
      // Deliberately do NOT clear `anonQuotaWall` here — keep the
      // last-known-good payload visible behind the error banner so
      // the admin still has stale-but-real numbers to look at while
      // the retry resolves. The banner copy makes the staleness
      // explicit ("numbers below are not real" if there's no prior
      // payload, otherwise the banner reads as a refresh failure).
      setAnonQuotaError(e?.response?.status ? `HTTP ${e.response.status}` : (e?.message || 'Network error'));
      if (withBackfill) toast.error('Backfill failed — see logs');
    } finally {
      setAnonQuotaLoading(false);
      setAnonQuotaBackfilling(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken]);

  useEffect(() => {
    loadAnonQuotaWall(anonQuotaDays);
    const interval = setInterval(() => loadAnonQuotaWall(anonQuotaDays), 60000);
    return () => clearInterval(interval);
  }, [loadAnonQuotaWall, anonQuotaDays]);

  // Task #626 — Chat Model config tab deep-links here with
  // { scrollTo: 'chat-speedup-providers' } to land the admin on the
  // per-provider comparison. Wait a tick so the card has mounted
  // (Suspense/lazy can delay paint), then scroll it into view.
  useEffect(() => {
    const target = navContext?.scrollTo;
    if (!target || typeof document === 'undefined') return;
    const t = setTimeout(() => {
      const el = document.getElementById(target);
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 250);
    return () => clearTimeout(t);
  }, [navContext]);

  // Task #681 — the review-prompt funnel tile (in OverviewTab) renders
  // a baseline-noise legend whose "Tune sigma multiplier" link expects
  // to land the admin directly on the Reason CTR Sigma Multiplier
  // input. We listen on a window event (decoupled from OverviewTab's
  // props) and pop the Alert Settings panel + scroll-and-focus the
  // sigma input. Hoisted above the `if (loading)` early return below
  // so the hook order stays stable across loading → loaded transitions
  // (otherwise React throws "Rendered more hooks than during the
  // previous render" once `loading` flips to false).
  useEffect(() => {
    const onOpenSigma = () => {
      if (!alertSettings) loadAlertSettings();
      setAlertSettingsOpen(true);
      // Defer to the next paint so the panel is in the DOM before we
      // try to scroll/focus the input.
      setTimeout(() => {
        const el = document.getElementById('alert-reason-ctr-sigma-input');
        if (el) {
          try {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          } catch {
            el.scrollIntoView();
          }
          try { el.focus({ preventScroll: true }); } catch { el.focus(); }
        }
      }, 50);
    };
    window.addEventListener('syrabit:open-alert-sigma-setting', onOpenSigma);
    return () => window.removeEventListener('syrabit:open-alert-sigma-setting', onOpenSigma);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alertSettings]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center p-16 gap-3">
        <Loader2 size={24} className="animate-spin text-violet-500" />
        <span className="text-sm text-gray-400">Loading dashboard...</span>
      </div>
    );
  }

  const handleAcknowledgeAlert = async (alertId) => {
    try {
      await axios.patch(`${API_BASE}/admin/alerts/${alertId}/acknowledge`, {}, adminHdr(adminToken));
      setAlertHistory(prev => ({
        ...prev,
        alerts: prev.alerts.map(a => a._id === alertId ? { ...a, acknowledged: true } : a),
      }));
      toast.success('Alert acknowledged');
    } catch (e) {
      log.error('Failed to acknowledge alert', { error: e.message });
      toast.error(`Failed to acknowledge alert: ${e.response?.data?.error || e.message}`);
    }
  };

  const handleAcknowledgeAll = async () => {
    try {
      await axios.patch(`${API_BASE}/admin/alerts/acknowledge-all`, {}, adminHdr(adminToken));
      setAlertHistory(prev => ({
        ...prev,
        alerts: prev.alerts.map(a => ({ ...a, acknowledged: true })),
      }));
      toast.success('All alerts acknowledged');
    } catch (e) {
      log.error('Failed to acknowledge all alerts', { error: e.message });
      toast.error(`Failed to acknowledge alerts: ${e.response?.data?.error || e.message}`);
    }
  };

  const loadAlertSettings = async () => {
    try {
      const res = await axios.get(`${API_BASE}/admin/alert-settings`, adminHdr(adminToken));
      setAlertSettings(res.data);
      setAlertSettingsDraft({ thresholds: { ...res.data.thresholds }, expiration: { ...res.data.expiration } });
    } catch (e) {
      log.error('Failed to load alert settings', { error: e.message });
    }
  };

  const handleSaveAlertSettings = async () => {
    if (!alertSettingsDraft) return;
    setAlertSettingsSaving(true);
    try {
      await axios.put(`${API_BASE}/admin/alert-settings`, alertSettingsDraft, adminHdr(adminToken));
      setAlertSettings({ ...alertSettings, thresholds: { ...alertSettingsDraft.thresholds }, expiration: { ...alertSettingsDraft.expiration } });
      setAlertSettingsOpen(false);
      toast.success('Alert settings saved');
    } catch (e) {
      log.error('Failed to save alert settings', { error: e.message });
      toast.error(`Failed to save alert settings: ${e.response?.data?.error || e.message}`);
    } finally {
      setAlertSettingsSaving(false);
    }
  };

  const handleOpenAlertSettings = () => {
    if (!alertSettings) loadAlertSettings();
    setAlertSettingsOpen(prev => !prev);
  };

  const handleResetAlertSettings = () => {
    if (alertSettings?.defaults) {
      setAlertSettingsDraft({
        thresholds: { ...alertSettings.defaults.thresholds },
        expiration: { ...alertSettings.defaults.expiration },
      });
    }
  };

  const handleRetryEndpoint = async (endpoint) => {
    setRetryingEndpoint(endpoint);
    try {
      const retryRes = await axios.post(`${API_BASE}/admin/indexnow/endpoint/retry`, { endpoint }, adminHdr(adminToken));
      const requeued = Number(retryRes.data?.requeued ?? retryRes.data?.count ?? 0);
      toast.success(`Endpoint reset — ${requeued} URL${requeued === 1 ? '' : 's'} re-queued`);
      try {
        const statsRes = await axios.get(`${API_BASE}/admin/indexnow/stats`, adminHdr(adminToken));
        setIndexNowStats(statsRes.data);
      } catch (statsErr) {
        log.error('Stats refresh failed after retry', { endpoint, error: statsErr.message });
      }
    } catch (e) {
      log.error('Endpoint retry failed', { endpoint, error: e.message });
      toast.error(`Retry failed: ${e.response?.data?.error || e.message}`);
    } finally {
      setRetryingEndpoint(null);
    }
  };

  const handleCiRerun = async (runId, failedOnly = true) => {
    setCiRerunning(runId);
    try {
      await axios.post(
        `${API_BASE}/admin/ci-rerun`,
        { run_id: runId, failed_only: failedOnly },
        adminHdr(adminToken),
      );
      toast.success(`Re-run queued for run #${runId} — refresh in a moment to see it start`);
    } catch (e) {
      const detail = e.response?.data?.detail || e.message;
      toast.error(`Re-run failed: ${detail}`);
    } finally {
      setCiRerunning(null);
    }
  };

  const vs = data?.visitor_stats || {};
  const recentEvents = data?.recent_events || [];
  const deps = metrics?.dependencies || {};

  const ragAlert = failedSections.includes('rag') ? 'yellow' : (ragAccuracy?.alert || 'green');
  const fallbackAlert = failedSections.includes('fallbacks') ? 'yellow' : (chatFallbacks?.alert || 'green');
  const latencyAlert = failedSections.includes('latency') ? 'yellow' : (latency?.alert || 'green');
  const vectorAlert = failedSections.includes('vector') ? 'yellow'
    : (vectorStats?.overall_coverage_pct ?? 100) < 90 ? 'yellow' : 'green';
  // botAlert was used by the legacy "Bot Traffic Analytics" card,
  // which has been replaced by the Cloudflare AI Crawl Control card.
  // botAnalytics is still fetched (other consumers may rely on it),
  // but the alert badge for it is no longer rendered here.

  const hasRagIssue = ragAlert === 'red' || latencyAlert === 'red';

  const quickActions = [
    { id: 'users',     label: 'View Users',     icon: Users,    color: '#7c3aed' },
    { id: 'blog',      label: 'Blog Publisher', icon: PenTool,  color: '#3b82f6' },
    { id: 'analytics', label: 'Analytics',       icon: BarChart2, color: '#10b981' },
    { id: 'monetization', label: 'Monetization', icon: Crown,    color: '#f59e0b' },
  ];

  const ctx = {
    adminToken, onNavigate, navContext,
    data, metrics, loading, refreshing, setRefreshing, lastRefresh, failedSections, load,
    ragAccuracy, chatFallbacks, vectorStats, latency,
    chatSpeedups, speedupDays, setSpeedupDays, speedupLoading, loadChatSpeedups,
    anonQuotaWall, anonQuotaDays, setAnonQuotaDays, anonQuotaLoading,
    anonQuotaBackfilling, anonQuotaError, loadAnonQuotaWall,
    topQueries, tokenSpend, funnel, coverage, pwaStats, botAnalytics,
    cfCrawlControl, cfRange, setCfRange, cfOverview, cfOverviewLoading, loadCfOverview, cfVisitors24h,
    prewarmCoverage,
    indexNowStats, indexNowHistory, retryingEndpoint, resubmittingIndexNow,
    resubmitMessage, setResubmitMessage,
    alertHistory, setAlertHistory, cooldownActiveCount,
    alertFilter, setAlertFilter, alertReasonFilter, setAlertReasonFilter,
    showSyntheticAlerts, setShowSyntheticAlerts,
    alertSettingsOpen, setAlertSettingsOpen,
    alertSettings, alertSettingsDraft, setAlertSettingsDraft, alertSettingsSaving,
    notifPrefs, notifPrefsSaving, notifPrefsOpen, setNotifPrefsOpen,
    pushDeliverySummary, seoSummaryDispatches,
    kvHealth, kvExpandedIsolates, setKvExpandedIsolates,
    r2Health, r2ResettingWatchdog, setR2ResettingWatchdog, r2Reevaluating, setR2Reevaluating,
    vertexProbe, ciStatus, ciRerunning,
    chimeUploading, pendingChimeFile, setPendingChimeFile, chimeFileInputRef,
    pushNotif,
    alertSoundEnabled, chimeTone, CHIME_TONES, ALERT_SEVERITY_LABELS,
    seoHealth, seoHealthRefreshing, setSeoHealth, setSeoHealthRefreshing,
    seoLive, seoLiveLoading, seoLiveError,
    seoAutoDeepScans,
    expandedSitemap, setExpandedSitemap,
    sitemapDeepScans, setSitemapDeepScans,
    d1SyncRunning, setD1SyncRunning, d1SyncResult, setD1SyncResult,
    d1SyncDurationMs, setD1SyncDurationMs, d1SyncError, setD1SyncError,
    ragAlert, fallbackAlert, latencyAlert, vectorAlert, hasRagIssue,
    vs, recentEvents, deps, quickActions,
    handleAcknowledgeAlert, handleAcknowledgeAll, handleOpenAlertSettings,
    handleSaveAlertSettings, handleResetAlertSettings,
    handleRetryEndpoint, handleCiRerun, loadAlertSettings,
    saveNotifPrefs, toggleAlertSound, playAlertChime,
    handleChimeFileSelect, handleChimeUploadConfirm, handleDeleteCustomChime,
    loadNotifPrefs,
    adminHdr,
  };

  return (
    <div className="p-4 md:p-6 space-y-5 max-w-[1400px]" role="main" aria-label="Admin Dashboard">

      {failedSections.length > 0 && (
        <div className="flex items-center gap-3 p-3 rounded-xl bg-amber-50 border border-amber-200">
          <AlertTriangle size={14} className="text-amber-500 flex-shrink-0" />
          <p className="text-xs text-amber-700 flex-1">
            Some widgets failed to load ({failedSections.join(', ')}). Metrics may be stale.
          </p>
          <button onClick={() => load(true)} className="text-xs text-amber-700 hover:text-amber-900 px-2.5 py-1 rounded-lg transition-colors bg-amber-100">
            Retry
          </button>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-gray-900 font-semibold text-lg tracking-tight">Overview</h2>
          {lastRefresh && (
            <p className="text-gray-400 text-xs mt-0.5">
              Updated {formatTimeAgo(lastRefresh.toISOString())} · auto-refreshes every 60s
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {metrics?.response_time_ms && (
            <span className="text-xs text-gray-400 flex items-center gap-1">
              <Clock size={10} /> API: {metrics.response_time_ms}ms
            </span>
          )}
          <button
            onClick={() => load(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs font-medium text-gray-500 hover:text-gray-700 transition-all disabled:opacity-40 bg-white border border-gray-200 shadow-sm"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      <SectionErrorBoundary name="System Health">
      {Object.keys(deps).length > 0 && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <Wifi size={14} className="text-violet-500" />
            <h3 className="text-gray-500 text-sm font-semibold">System Health</h3>
            <div className="ml-auto flex items-center gap-1.5">
              {Object.values(deps).every(d => d.status === 'ok') && !hasRagIssue ? (
                <>
                  <CheckCircle size={12} className="text-emerald-500" />
                  <span className="text-emerald-600 text-xs font-medium">All Systems Operational</span>
                </>
              ) : (
                <>
                  <AlertCircle size={12} className="text-amber-500" />
                  <span className="text-amber-600 text-xs font-medium">
                    {hasRagIssue ? 'RAG/Latency Issue Detected' : 'Degraded'}
                  </span>
                </>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(deps).map(([name, info]) => (
              <DepStatusCard
                key={name}
                name={name}
                status={info.status}
                latency={info.latency_ms}
              />
            ))}
          </div>
        </GlassCard>
      )}
      </SectionErrorBoundary>

      {data?.conversation_date_range?.oldest && (
        <div className="flex items-center gap-3 p-3 rounded-xl flex-wrap bg-emerald-50 border border-emerald-200">
          <span className="text-xs text-emerald-700 font-bold">Data Recovered</span>
          <span className="text-xs text-gray-500">
            Conversations since <strong className="text-gray-700">{data.conversation_date_range.oldest}</strong>
            {' · '}PG: <strong className="text-blue-600">{data.pg_conversations}</strong>
            {' + '}Supabase: <strong className="text-emerald-600">{data.supa_conversations}</strong>
            {' = '}<strong className="text-gray-700">{data.total_conversations}</strong> total
            {' · '}<strong className="text-gray-700">{data.conversations_with_messages}</strong> with messages
            {' · '}<strong className="text-gray-700">{data.unique_chatters}</strong> unique chatters
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Total Users"     value={data?.total_users}          icon={Users}         color="#8b5cf6"
          subLabel="Chatted" subValue={data?.unique_chatters ?? 0} />
        <StatCard label="Conversations"   value={data?.total_conversations}  icon={MessageSquare} color="#3b82f6"
          subLabel="With messages" subValue={data?.conversations_with_messages ?? 0} />
        <StatCard label="Messages (All)"  value={data?.total_messages}       icon={Zap}           color="#10b981"
          subLabel="Since" subValue={data?.conversation_date_range?.oldest ?? '—'} />
        <StatCard label="Subjects"        value={data?.total_subjects}       icon={BookOpen}      color="#f59e0b" />
      </div>

      <SectionErrorBoundary name="Revenue">
      {metrics?.revenue && (
        <>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard
            label="Revenue (INR)"
            value={'₹' + Math.round(metrics.revenue.total_inr || 0).toLocaleString('en-IN')}
            icon={DollarSign}
            color="#10b981"
            subLabel="MRR"
            subValue={'₹' + Math.round(metrics.revenue.mrr_inr || 0).toLocaleString('en-IN')}
          />
          <StatCard label="Paid Users"      value={metrics.users?.paid || 0}     icon={Crown}  color="#f59e0b" />
          <StatCard label="Free Users"      value={metrics.users?.free || 0}     icon={Users}  color="#64748b" />
          <StatCard label="SEO Pages"       value={metrics.seo?.published_pages || 0} icon={Globe} color="#06b6d4"
            subLabel="Topics" subValue={metrics.seo?.topics || 0}
            onClick={() => onNavigate?.('seomanager')} />
          <StatCard label="Bot Renders"    value={metrics.bot_render?.total_requests || 0} icon={Bot} color="#8b5cf6"
            subLabel="Success Rate" subValue={metrics.bot_render?.success_rate_pct != null ? `${metrics.bot_render.success_rate_pct}%` : '—'} />
        </div>
        {/* Task #398 — freshness badge for the heavy users / revenue / SEO
            block. The numbers above (Revenue, Paid/Free Users, SEO Pages,
            Bot Renders) all read from `metrics`, which the backend caches
            for ~5 s (Task #395). Without this badge an admin staring at
            a revenue figure during an incident has no way to tell whether
            it was just computed or is up to 5 s stale. The 1 s tick
            useEffect above keeps the "Xs ago" label moving between the
            60 s polls so the cache age advances visibly while the panel
            sits idle. Anything beyond ~5 s flips the badge to amber so
            on-call notices when the heavy cache TTL has been blown
            (likely a wedged backend or a stuck refresh). The four heavy
            sections share one ``heavy_cached_at`` timestamp because they
            are computed and cached as a single block server-side. */}
        {metrics?._meta && (() => {
          // Shared formatter + stale boundary — see
          // src/utils/metricsFreshness.js. Centralising here means
          // the AdminHealth strip (Task #396) and this badge always
          // render the same wording for the same `heavy_cached_at`,
          // and a future change to `_METRICS_CACHE_TTL` (Task #395)
          // updates both panels in lockstep instead of drifting.
          const heavyAt = Number(metrics._meta.heavy_cached_at);
          const { label: heavyLabel, stale } = computeHeavyFreshness(heavyAt);
          return (
            <p
              className={`text-[11px] mt-2 px-1 ${stale ? 'text-amber-600 font-medium' : 'text-gray-400'}`}
              data-testid="dashboard-metrics-freshness"
              title={`heavy_cached_at=${heavyAt} (Task #396)`}
            >
              Last updated{' '}
              <span data-testid="dashboard-metrics-freshness-age">{heavyLabel}</span>
              {stale && (
                <span className="ml-1" data-testid="dashboard-metrics-freshness-stale">
                  — cache TTL (~5s) exceeded
                </span>
              )}
            </p>
          );
        })()}
        <p className="text-[11px] text-gray-400 mt-2 px-1">
          Revenue includes Razorpay (INR) + Stripe (USD→INR via daily ECB rate). All values stored as <code>amount_inr</code> on each payment row.
        </p>
        </>
      )}
      </SectionErrorBoundary>
      <SectionErrorBoundary name="Bot Render">
      {metrics?.bot_render?.by_page_type && Object.keys(metrics.bot_render.by_page_type).length > 0 && (() => {
        const raw = metrics.bot_render.by_page_type;
        const grouped = {};
        Object.entries(raw).forEach(([key, count]) => {
          const [type, status] = key.split(':');
          if (!grouped[type]) grouped[type] = { ok: 0, fail: 0 };
          if (status === 'ok') grouped[type].ok = count;
          else grouped[type].fail = count;
        });
        return (
        <div className="mt-4 bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2"><Bot size={14} className="text-violet-500" /> Bot Render by Page Type</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {Object.entries(grouped).map(([type, counts]) => (
              <div key={type} className="bg-gray-50 rounded-xl px-3 py-2 border border-gray-100">
                <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">{type.replace(/_/g, ' ')}</p>
                <p className="text-base font-bold font-mono text-gray-800">{counts.ok + counts.fail}</p>
                <p className="text-[10px] text-gray-400">{counts.ok} ok / {counts.fail} fail</p>
              </div>
            ))}
          </div>
        </div>
        );
      })()}
      </SectionErrorBoundary>


      <AiHealthWidget {...ctx} />
      <TrafficWidget {...ctx} />
      <SeoWidget {...ctx} />
      <ChatWidget {...ctx} />
      <UserAnalyticsWidget {...ctx} />
      <ActivityWidget {...ctx} />

      <AdminQuickLinks links={['content','seomanager','analytics','users','conversations','ai','revenue','roadmap']} onNavigate={onNavigate} />
    </div>
  );
}
