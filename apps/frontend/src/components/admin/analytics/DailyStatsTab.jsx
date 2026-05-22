import { Loader2, RefreshCw, Globe, Eye, Users, Bot, MessageSquare,
  Calendar, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import {
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, Legend, AreaChart, Area,
} from 'recharts';
import { Card, TT, fmt } from './shared';

export default function DailyStatsTab({ dailyDays, setDailyDays, dailyLoading, dailyData, loadDailyAnalytics }) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5 text-gray-600 text-sm">
          <Calendar size={14} />
          <span>Date range:</span>
        </div>
        {[7, 14, 30, 60, 90].map(d => (
          <button key={d} onClick={() => setDailyDays(d)}
            className={`px-3.5 py-1.5 rounded-xl text-xs font-medium transition-all ${
              dailyDays === d ? 'text-white' : 'text-gray-600 hover:text-gray-800'
            }`}
            style={dailyDays === d
              ? { background: 'linear-gradient(135deg, #7c3aed, #6d28d9)', boxShadow: '0 2px 12px rgba(124,58,237,0.3)' }
              : { background: '#f9fafb', border: '1px solid #e5e7eb' }
            }>
            Last {d} days
          </button>
        ))}
        <button onClick={() => loadDailyAnalytics(dailyDays)} disabled={dailyLoading}
          className="ml-auto flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs text-gray-600 hover:text-gray-900 transition-all"
          style={{ background: '#ffffff', border: '1px solid #e5e7eb' }}>
          <RefreshCw size={12} className={dailyLoading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {dailyLoading ? (
        <div className="flex justify-center p-10">
          <Loader2 size={24} className="animate-spin text-violet-600/60" />
        </div>
      ) : dailyData ? (() => {
        const s = dailyData.summary || {};
        const daily = dailyData.daily || [];
        const hasVisitors = daily.some(d => d.visitors > 0);
        const hasSignups = daily.some(d => d.signups > 0);
        const hasMessages = daily.some(d => d.messages > 0 || d.ai_interactions > 0);

        const fmtChg = (pct) => {
          if (pct == null) return null;
          const up = pct >= 0;
          return (
            <div className={`flex items-center gap-0.5 text-xs font-semibold flex-shrink-0 ${up ? 'text-emerald-600' : 'text-red-600'}`}>
              {up ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
              {Math.abs(pct)}%
            </div>
          );
        };

        const summaryCards = [
          { icon: Globe,          label: 'Unique Visitors Today',    value: s.visitors?.today ?? 0,          chg: s.visitors?.change_pct,         color: '#06b6d4' },
          { icon: Eye,            label: 'Page Views Today',          value: s.page_views?.today ?? 0,        chg: s.page_views?.change_pct,       color: '#ec4899' },
          { icon: Users,          label: 'New Sign-ups Today',        value: s.signups?.today ?? 0,           chg: s.signups?.change_pct,          color: '#10b981' },
          { icon: MessageSquare,  label: 'Messages Today',            value: s.messages?.today ?? 0,          chg: s.messages?.change_pct,         color: '#8b5cf6' },
          { icon: Bot,            label: 'AI Interactions Today',     value: s.ai_interactions?.today ?? 0,   chg: s.ai_interactions?.change_pct,  color: '#f59e0b' },
        ];

        return (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
              {summaryCards.map(sc => (
                <div key={sc.label}
                  className="flex items-center gap-3 p-3.5 rounded-xl group transition-all duration-300 relative overflow-hidden"
                  style={{
                    background: '#ffffff',
                    border: '1px solid #e5e7eb',
                  }}
                >
                  <div className="absolute inset-0 pointer-events-none transition-opacity duration-300 opacity-0 group-hover:opacity-100" style={{
                    background: `radial-gradient(ellipse at top right, ${sc.color}0a, transparent 60%)`,
                  }} />
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 relative" style={{ background: `${sc.color}18` }}>
                    <sc.icon size={15} style={{ color: sc.color }} />
                  </div>
                  <div className="flex-1 min-w-0 relative">
                    <p className="text-gray-900 font-bold text-lg leading-none">{sc.value.toLocaleString()}</p>
                    <p className="text-gray-600 text-xs mt-0.5 leading-tight">{sc.label}</p>
                  </div>
                  {fmtChg(sc.chg)}
                </div>
              ))}
            </div>

            <Card title={`Daily Visitors & Page Views — Last ${dailyDays} Days`}
              empty={!hasVisitors}
              emptyMsg={dailyData.cf_connected ? 'No visitor data for this range yet' : 'Cloudflare analytics unavailable — check API token and Zone ID'}>
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={daily} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
                  <XAxis dataKey="date" tick={{ fill: '#4b5563', fontSize: 10 }} tickFormatter={fmt}
                    interval={Math.max(0, Math.floor(daily.length / 8) - 1)} />
                  <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} />
                  <Tooltip {...TT} />
                  <Legend wrapperStyle={{ fontSize: 11, color: '#4b5563' }} />
                  <Area type="monotone" dataKey="visitors" name="Cloudflare Visitors"
                    stroke="#f6821f" fill="rgba(246,130,31,0.15)" strokeWidth={2.5} />
                  <Area type="monotone" dataKey="page_views" name="Cloudflare Page Views"
                    stroke="#ec4899" fill="rgba(236,72,153,0.10)" strokeWidth={2} strokeDasharray="4 2" />
                </AreaChart>
              </ResponsiveContainer>
            </Card>

            <Card title={`Daily Sign-ups — Last ${dailyDays} Days`}
              empty={!hasSignups} emptyMsg="No sign-ups recorded in this range">
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={daily} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
                  <XAxis dataKey="date" tick={{ fill: '#4b5563', fontSize: 10 }} tickFormatter={fmt}
                    interval={Math.max(0, Math.floor(daily.length / 8) - 1)} />
                  <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} allowDecimals={false} />
                  <Tooltip {...TT} />
                  <Bar dataKey="signups" name="Sign-ups" fill="#10b981" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>

            <Card title={`Messages & AI Interactions — Last ${dailyDays} Days`}
              empty={!hasMessages} emptyMsg="No message data for this range yet">
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={daily} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
                  <XAxis dataKey="date" tick={{ fill: '#4b5563', fontSize: 10 }} tickFormatter={fmt}
                    interval={Math.max(0, Math.floor(daily.length / 8) - 1)} />
                  <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} allowDecimals={false} />
                  <Tooltip {...TT} />
                  <Legend wrapperStyle={{ fontSize: 11, color: '#4b5563' }} />
                  <Bar dataKey="messages"        name="Messages"        fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="ai_interactions" name="AI Interactions" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>

          </>
        );
      })() : (
        <div className="rounded-2xl p-10 text-center" style={{
          background: '#ffffff',
          border: '1px solid #e5e7eb',
        }}>
          <Calendar size={32} className="text-gray-700 mx-auto mb-3" />
          <p className="text-gray-700 text-sm">Select a date range above to load daily metrics</p>
        </div>
      )}
    </div>
  );
}
