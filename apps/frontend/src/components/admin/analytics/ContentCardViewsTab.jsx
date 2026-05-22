import { useState, useEffect, useCallback } from 'react';
import { Eye, Users, Loader2 } from 'lucide-react';
import { Card } from './shared';
import { adminGetContentCardViews } from '@/utils/api';
import { toast } from 'sonner';

const PERIODS = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: 'All', days: 0 },
];

export default function ContentCardViewsTab({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeDays, setActiveDays] = useState(30);

  const load = useCallback(async (days) => {
    setLoading(true);
    try {
      const r = await adminGetContentCardViews(adminToken, days);
      setData(r.data);
    } catch {
      toast.error('Failed to load content card views');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    load(activeDays);
  }, [activeDays, load]);

  const cards = data?.content_card_views || [];
  const maxViews = cards[0]?.page_views || 1;

  return (
    <Card title="Content Card Opens (internal product analytics)"
      action={
        <div className="flex gap-1 rounded-lg p-0.5 bg-gray-100">
          {PERIODS.map(p => (
            <button key={p.days} onClick={() => setActiveDays(p.days)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
                activeDays === p.days
                  ? 'text-white bg-violet-600 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}>
              {p.label}
            </button>
          ))}
        </div>
      }
      empty={!loading && !cards.length}
      emptyMsg="No content card view data yet">
      {loading ? (
        <div className="flex justify-center py-8">
          <Loader2 size={20} className="animate-spin text-violet-500" />
        </div>
      ) : (
        <div className="space-y-1.5">
          {cards.map((c, i) => (
            <div key={c.subject_id} className="flex items-center gap-2 px-2.5 py-2 rounded-xl transition-colors"
              style={{ background: i % 2 === 0 ? '#f9fafb' : 'transparent' }}>
              <span className="text-gray-700 text-xs w-5 text-right flex-shrink-0">{i + 1}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-gray-700 text-sm font-medium truncate">{c.name}</span>
                  {(c.board || c.class_name) && (
                    <span className="text-gray-700 text-[10px] flex-shrink-0 truncate">
                      {[c.board, c.class_name].filter(Boolean).join(' · ')}
                    </span>
                  )}
                </div>
                <div className="w-full h-1.5 rounded-full overflow-hidden mt-1 bg-gray-100">
                  <div className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.max(Math.round((c.page_views / maxViews) * 100), 2)}%`,
                      background: (c.page_views / maxViews) > 0.7 ? '#ef4444' : (c.page_views / maxViews) > 0.4 ? '#f59e0b' : '#3b82f6',
                    }} />
                </div>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                <span className="flex items-center gap-1 text-gray-500 text-xs" title="Card opens (internal product event)">
                  <Eye size={11} className="text-violet-600" />
                  {c.page_views.toLocaleString()} opens
                </span>
                <span className="flex items-center gap-1 text-gray-600 text-xs" title="Unique users who opened this card">
                  <Users size={11} className="text-blue-600" />
                  {c.unique_visitors.toLocaleString()} users
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
