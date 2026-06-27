import { useState, useEffect } from 'react';
import { Loader2, RefreshCw, Activity, Edit2, Upload, Database, Globe, BarChart3 } from 'lucide-react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';

const ACTION_ICONS = {
  save: Edit2,
  create: Edit2,
  rag_save: Database,
  rag_reindex: Database,
  publish: Globe,
  delete: Activity,
};

const ACTION_COLORS = {
  save: 'bg-violet-50 text-violet-700 border-violet-200',
  create: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  rag_save: 'bg-teal-50 text-teal-700 border-teal-200',
  rag_reindex: 'bg-teal-50 text-teal-700 border-teal-200',
  publish: 'bg-blue-50 text-blue-700 border-blue-200',
  delete: 'bg-red-50 text-red-700 border-red-200',
};

export default function AdminActionsTab({ adminToken, days = 30 }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [rangeDays, setRangeDays] = useState(days);

  const load = async (d = rangeDays) => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/analytics/admin-actions`, {
        params: { days: d },
        withCredentials: true,
      });
      setData(res.data);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(rangeDays); }, [rangeDays]);

  if (loading) return (
    <div className="flex justify-center p-10">
      <Loader2 size={22} className="animate-spin text-violet-500" />
    </div>
  );

  if (!data) return (
    <div className="p-6 text-center text-gray-400 text-sm">
      Could not load admin action data.
      <button onClick={() => load()} className="ml-2 text-violet-600 hover:underline">Retry</button>
    </div>
  );

  const byAction = data.by_action || [];
  const byDay = data.by_day || [];
  const total = data.total ?? byAction.reduce((s, a) => s + a.count, 0);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Admin Actions</h3>
          <p className="text-xs text-gray-400 mt-0.5">{total} actions in last {rangeDays} days from ContentAuditLog</p>
        </div>
        <div className="flex items-center gap-2">
          {[7, 30, 90].map(d => (
            <button key={d}
              onClick={() => setRangeDays(d)}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${rangeDays === d ? 'bg-violet-600 text-white' : 'bg-gray-100 text-gray-500 hover:text-gray-700'}`}>
              {d}d
            </button>
          ))}
          <button onClick={() => load()} className="p-1.5 rounded-lg border border-gray-200 text-gray-400 hover:text-gray-600 transition-all">
            <RefreshCw size={12} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        {byAction.map(item => {
          const Icon = ACTION_ICONS[item.action] || Activity;
          const color = ACTION_COLORS[item.action] || 'bg-gray-50 text-gray-700 border-gray-200';
          return (
            <div key={item.action} className={`rounded-xl p-4 border ${color} flex items-center gap-3`}>
              <Icon size={18} className="flex-shrink-0 opacity-70" />
              <div>
                <p className="text-lg font-bold">{item.count.toLocaleString()}</p>
                <p className="text-[11px] font-medium capitalize opacity-80">{item.action.replace(/_/g, ' ')}</p>
              </div>
            </div>
          );
        })}
        {byAction.length === 0 && (
          <div className="col-span-4 text-center py-8 text-gray-400 text-sm">
            <Activity size={28} className="mx-auto mb-2 opacity-20" />
            No admin actions logged yet in this period
          </div>
        )}
      </div>

      {byDay.length > 0 && (
        <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <BarChart3 size={14} className="text-violet-500" />
            <span className="text-sm font-semibold text-gray-900">Daily Action Breakdown</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-gray-600">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left pb-2 font-semibold text-gray-400">Date</th>
                  <th className="text-right pb-2 font-semibold text-gray-400">Actions</th>
                  <th className="text-left pb-2 pl-4 font-semibold text-gray-400">Breakdown</th>
                </tr>
              </thead>
              <tbody>
                {byDay.slice(0, 20).map((row, i) => (
                  <tr key={i} className="border-b border-gray-50 hover:bg-gray-50 transition-colors">
                    <td className="py-2 font-mono text-[11px] text-gray-500">{row.date}</td>
                    <td className="py-2 text-right font-semibold text-gray-900">{row.total}</td>
                    <td className="py-2 pl-4">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {Object.entries(row.by_action || {}).map(([action, count]) => (
                          <span key={action} className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-gray-100 text-gray-500">
                            {action.replace(/_/g, ' ')}: {count}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
