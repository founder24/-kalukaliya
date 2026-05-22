import { useState, useEffect, useCallback } from 'react';
import AdminQuickLinks from './AdminQuickLinks';
import { Loader2, RefreshCw } from 'lucide-react';
import axios from 'axios';
import { adminGetAnalytics, adminGetRevenue, adminGetPredictor,
  adminGetGA4Status, adminGetGA4AuthUrl, adminTestGA4, API_BASE,
  pageConversions, adminGetDailyAnalytics } from '@/utils/api';
import { toast } from 'sonner';
import OverviewTab from './analytics/OverviewTab';
import DailyStatsTab from './analytics/DailyStatsTab';
import FunnelTab from './analytics/FunnelTab';
import HeatmapTab from './analytics/HeatmapTab';
import SeoPagesTab from './analytics/SeoPagesTab';
import RevenueTab from './analytics/RevenueTab';
import PredictionsTab from './analytics/PredictionsTab';
import ConversionsTab from './analytics/ConversionsTab';
import ContentCardViewsTab from './analytics/ContentCardViewsTab';
import CloudflareAnalyticsBanner from './analytics/CloudflareAnalyticsBanner';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
export default function AdminAnalytics({ adminToken, onNavigate }) {
  const [data, setData]         = useState(null);
  const [funnel, setFunnel]     = useState(null);
  const [heatmap, setHeatmap]   = useState(null);
  const [revenue, setRevenue]   = useState(null);
  const [predict, setPredict]   = useState(null);
  const [ga4Status, setGa4Status] = useState(null);
  const [loading, setLoading]   = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [tab, setTab]           = useState('overview');
  const [ga4Testing, setGa4Testing] = useState(false);
  const [ga4TestResult, setGa4TestResult] = useState(null);
  const [pageConvData, setPageConvData] = useState(null);
  const [pageConvLoading, setPageConvLoading] = useState(false);
  const [dailyData, setDailyData] = useState(null);
  const [dailyLoading, setDailyLoading] = useState(false);
  const [dailyDays, setDailyDays] = useState(30);
  const [overviewDays, setOverviewDays] = useState(7);
  const [widgetErrors, setWidgetErrors] = useState({});
  const h = { withCredentials: true };

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true); else setRefreshing(true);
    const [r1, r2, r3, r4, r5, r6] = await Promise.allSettled([
      adminGetAnalytics(adminToken, overviewDays),
      axios.get(`${API_BASE}/admin/analytics/funnel`, h),
      axios.get(`${API_BASE}/admin/analytics/content-heatmap`, h),
      // Daily-revenue chart and cohort split follow the page's range
      // selector. (Top-line MRR/Predicted MRR come from the predictor
      // endpoint and are intentionally range-agnostic — MRR is a
      // 30-day SaaS metric by definition.)
      adminGetRevenue(adminToken, overviewDays),
      adminGetPredictor(adminToken),
      adminGetGA4Status(adminToken),
    ]);
    const errs = {};
    if (r1.status === 'fulfilled') setData(r1.value.data); else { errs.overview = true; setData(null); }
    if (r2.status === 'fulfilled') setFunnel(r2.value.data); else { errs.funnel = true; setFunnel(null); }
    if (r3.status === 'fulfilled') setHeatmap(r3.value.data); else { errs.heatmap = true; setHeatmap(null); }
    if (r4.status === 'fulfilled') setRevenue(r4.value.data); else { errs.revenue = true; setRevenue(null); }
    if (r5.status === 'fulfilled') setPredict(r5.value.data); else { errs.predictions = true; setPredict(null); }
    if (r6.status === 'fulfilled') setGa4Status(r6.value.data); else errs.ga4 = true;
    setWidgetErrors(errs);
    setLastRefresh(new Date());
    setLoading(false);
    setRefreshing(false);
  }, [adminToken, overviewDays]);

  const handleGA4Connect = async () => {
    const redirectUri = `${window.location.origin}/admin?ga4callback=1`;
    try {
      const r = await adminGetGA4AuthUrl(adminToken, redirectUri);
      window.open(r.data.url, '_blank', 'width=600,height=700');
    } catch (e) {
      toast.error('Failed to get GA4 auth URL');
    }
  };

  const handleGA4Test = async () => {
    setGa4Testing(true);
    setGa4TestResult(null);
    try {
      const r = await adminTestGA4(adminToken);
      setGa4TestResult(r.data);
    } catch (e) {
      setGa4TestResult({ ok: false, reason: 'Request failed' });
    }
    setGa4Testing(false);
  };

  const loadPageConversions = useCallback(async () => {
    setPageConvLoading(true);
    try {
      // Follow the page's range selector so the conversions list moves
      // with overviewDays instead of being permanently pinned to 30.
      const r = await pageConversions(adminToken, overviewDays);
      setPageConvData(r.data);
    } catch { toast.error('Failed to load page conversions'); }
    finally { setPageConvLoading(false); }
  }, [adminToken, overviewDays]);

  const loadDailyAnalytics = useCallback(async (days = dailyDays) => {
    setDailyLoading(true);
    try {
      const r = await adminGetDailyAnalytics(adminToken, days);
      setDailyData(r.data);
    } catch { toast.error('Failed to load daily analytics'); }
    finally { setDailyLoading(false); }
  }, [adminToken, dailyDays]);

  useEffect(() => {
    load();
    const iv = setInterval(() => load(true), 60000);
    return () => clearInterval(iv);
  }, [load]);

  useEffect(() => {
    if (tab === 'pages') loadPageConversions();
  }, [tab, loadPageConversions]);

  useEffect(() => {
    if (tab === 'daily') loadDailyAnalytics(dailyDays);
  }, [tab, dailyDays]);

  if (loading) return (
    <div className="flex justify-center p-10">
      <Loader2 size={24} className="animate-spin text-violet-500" />
    </div>
  );
  if (!data) return (
    <div className="p-6">
      <div className="rounded-2xl p-8 text-center bg-white border border-gray-200 shadow-sm">
        <p className="text-gray-400">Unable to load analytics</p>
        <button onClick={load} className="mt-3 text-sm text-violet-600 hover:underline">Retry</button>
      </div>
    </div>
  );

  const vs             = data?.visitor_stats || {};
  const mrr         = predict?.current_mrr_inr || 0;
  const predicted   = predict?.predicted_mrr_inr || 0;
  const growth      = predict?.growth_rate_pct || 0;
  const cohorts     = revenue?.cohorts || {};
  const dailyRev    = revenue?.daily_revenue || [];
  const paidUsers   = funnel?.funnel?.find(f => f.stage === 'Paid User')?.count || 0;
  const arpu        = paidUsers > 0 ? Math.round(mrr / paidUsers) : 0;
  const ltv         = arpu > 0 ? Math.round(arpu * 12) : 0;
  const topSubject  = heatmap?.top_subjects?.[0];
  const topSearch   = heatmap?.top_searches?.[0];
  const aiInsight   = topSubject
    ? `"${topSubject.name}" drives ${topSubject.views} views — push MCQs & Important Questions for it to maximise conversions.`
    : topSearch
    ? `Top search query: "${topSearch.query}" — consider generating dedicated SEO pages for it.`
    : null;
  const cohortData = Object.entries(cohorts).map(([plan, count]) => ({ plan, count }));

  const TABS = [
    { id: 'overview',  label: 'Overview' },
    { id: 'daily',     label: 'Daily' },
    { id: 'funnel',    label: 'Funnel' },
    { id: 'heatmap',   label: 'Heatmap' },
    { id: 'cardviews', label: 'Card Views' },
    { id: 'seo',       label: 'SEO & Pages' },
    { id: 'revenue',   label: 'Revenue' },
    { id: 'predict',   label: 'Predictions' },
    { id: 'pages',     label: 'Page Conversions' },
  ];

  return (
    <SectionErrorBoundary name="Analytics">
      <div className="p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-gray-900 font-semibold text-lg">Analytics</h2>
            {lastRefresh && (
              <p className="text-gray-400 text-xs mt-0.5">
                Updated {Math.floor((Date.now() - lastRefresh) / 1000)}s ago · auto-refreshes every 60s
              </p>
            )}
          </div>
          <button onClick={() => load(true)} disabled={refreshing}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs text-gray-500 hover:text-gray-700 transition-all bg-white border border-gray-200 shadow-sm">
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>

        {/* Task #456: surfaces last_error / consecutive_failures / blocked_for_seconds
            plus a one-click "Re-check now" button (POST /admin/analytics/cf-recheck)
            and a deep-link to https://dash.cloudflare.com/profile/api-tokens with the
            three required scopes pre-listed. Self-hides when auth_ok=true. */}
        <CloudflareAnalyticsBanner
          adminToken={adminToken}
          onRecheck={() => load(true)}
        />

        <div className="flex gap-1 flex-wrap rounded-xl p-1 w-fit bg-gray-100">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
                tab === t.id
                  ? 'text-white bg-violet-600 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'overview' && (
          <OverviewTab data={data} vs={vs} widgetErrors={widgetErrors} load={load}
            mrr={mrr} predicted={predicted} growth={growth} arpu={arpu} ltv={ltv}
            cfConnected={data?.cf_connected}
            overviewDays={overviewDays} setOverviewDays={setOverviewDays}
            adminToken={adminToken} />
        )}

        {tab === 'daily' && (
          <DailyStatsTab dailyDays={dailyDays} setDailyDays={setDailyDays}
            dailyLoading={dailyLoading} dailyData={dailyData} loadDailyAnalytics={loadDailyAnalytics} />
        )}

        {tab === 'funnel' && (
          <FunnelTab funnel={funnel} widgetErrors={widgetErrors} load={load} />
        )}

        {tab === 'heatmap' && (
          <HeatmapTab heatmap={heatmap} aiInsight={aiInsight} widgetErrors={widgetErrors} load={load} />
        )}

        {tab === 'cardviews' && (
          <ContentCardViewsTab adminToken={adminToken} />
        )}

        {tab === 'seo' && (
          <SeoPagesTab data={data} vs={vs} ga4Status={ga4Status}
            ga4Testing={ga4Testing} ga4TestResult={ga4TestResult}
            handleGA4Connect={handleGA4Connect} handleGA4Test={handleGA4Test}
            onNavigate={onNavigate} />
        )}

        {tab === 'revenue' && (
          <RevenueTab widgetErrors={widgetErrors} load={load} mrr={mrr} predicted={predicted}
            growth={growth} arpu={arpu} ltv={ltv} paidUsers={paidUsers}
            dailyRev={dailyRev} cohortData={cohortData} predict={predict} revenue={revenue}
            rangeDays={overviewDays} />
        )}

        {tab === 'predict' && (
          <PredictionsTab widgetErrors={widgetErrors} load={load} mrr={mrr} predicted={predicted}
            growth={growth} aiInsight={aiInsight} topSubject={topSubject} predict={predict} />
        )}

        {tab === 'pages' && (
          <ConversionsTab pageConvData={pageConvData} pageConvLoading={pageConvLoading}
            loadPageConversions={loadPageConversions} rangeDays={overviewDays} />
        )}

        <AdminQuickLinks links={['seomanager','users','conversations','monetization','dashboard']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
