import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { log } from '@/utils/logger';
import AdminQuickLinks from '../AdminQuickLinks';
import AdminDraftServedSubjects from '../AdminDraftServedSubjects';
import AlertReasonsRow from '../AlertReasonsRow';
import BotCachePanel from '../BotCachePanel';
import CacheHitRatioPanel from '../CacheHitRatioPanel';
import R2ColdStoragePanel from '../R2ColdStoragePanel';
import AudioTrimPreview from '../AudioTrimPreview';
import CloudflareAnalyticsBanner from '../analytics/CloudflareAnalyticsBanner';
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
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid, Legend,
  AreaChart, Area,
} from 'recharts';
import {
  GlassCard, StatCard, formatTimeAgo, ActivityItem, DepStatusCard,
  RagAccuracyGauge, ChartTooltip, alertColor, AlertBadge, TOOLTIP_STYLE,
  PipelineWidget, formatCompactInt, adminHdr,
} from './shared';

export default function ActivityWidget(props) {
  const p = props;
  return (
    <>

      <SectionErrorBoundary name="SEO Pipeline">
        <PipelineWidget token={adminToken} />
      </SectionErrorBoundary>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {quickActions.map((action) => (
          <button
            key={action.id}
            onClick={() => onNavigate?.(action.id)}
            className="flex items-center justify-between p-4 rounded-2xl transition-all duration-300 group hover:shadow-md bg-white border border-gray-200 shadow-sm"
            data-testid={`quick-action-${action.id}`}
          >
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${action.color}15` }}>
                <action.icon size={15} style={{ color: action.color }} />
              </div>
              <span className="text-sm font-medium text-gray-700 group-hover:text-gray-900 transition-colors">{action.label}</span>
            </div>
            <ArrowRight size={14} className="text-gray-300 group-hover:text-gray-500 transition-colors" />
          </button>
        ))}
      </div>

      <SectionErrorBoundary name="Daily Visitors">
      {vs.daily_visitors?.length > 0 && (
        <GlassCard className="p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-500 text-sm font-semibold">Visitor Trend — Last 7 Days</h3>
            <span className="text-xs text-gray-400">Unique visitors per day</span>
          </div>
          <div className="flex items-end gap-2 h-20">
            {vs.daily_visitors.map((d, i) => {
              const maxV = Math.max(...vs.daily_visitors.map(x => x.visitors), 1);
              const pct = Math.max(4, (d.visitors / maxV) * 100);
              const isToday = i === vs.daily_visitors.length - 1;
              return (
                <div key={d.date} className="flex-1 flex flex-col items-center gap-1">
                  <div
                    className="w-full rounded-t transition-all duration-300"
                    style={{
                      height: `${pct}%`,
                      background: isToday
                        ? 'linear-gradient(to top, #7c3aed, #a78bfa)'
                        : '#e5e7eb',
                      minHeight: 4,
                    }}
                    title={`${d.date}: ${d.visitors} visitors, ${d.page_views} views`}
                  />
                  <span className="text-[10px] text-gray-400 whitespace-nowrap">
                    {d.date.slice(5)}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="flex gap-4 mt-3">
            {vs.daily_visitors.slice(-1).map(d => (
              <div key="today-summary" className="flex gap-4 text-xs text-gray-400" title={TODAY_BUCKET_CAPTION}>
                <span>Today (UTC, {UTC_MIDNIGHT_IN_IST}–now): <span className="text-violet-600 font-medium">{d.visitors} visitors</span></span>
                <span>·</span>
                <span><span className="text-gray-600 font-medium">{d.page_views}</span> page views</span>
              </div>
            ))}
          </div>
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Recent Activity">
      <GlassCard className="p-5" data-testid="recent-activity">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Activity size={16} className="text-violet-500" />
            <h3 className="text-gray-700 font-semibold">Recent Activity</h3>
            <span className="flex h-2 w-2 relative">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
          </div>
          <button
            onClick={() => onNavigate?.('activitylog')}
            className="text-xs text-violet-600 hover:text-violet-700 transition-colors"
          >
            View all logs →
          </button>
        </div>

        {recentEvents.length === 0 ? (
          <div className="text-center py-8">
            <Activity size={28} className="text-gray-200 mx-auto mb-3" />
            <p className="text-gray-400 text-sm">No activity yet — events will appear here in real time</p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {recentEvents.map((event, idx) => (
              <ActivityItem key={idx} event={event} idx={idx} />
            ))}
          </div>
        )}
      </GlassCard>
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Draft-Served Subjects">
        <AdminDraftServedSubjects adminToken={adminToken} />
      </SectionErrorBoundary>
    </>
  );
}
