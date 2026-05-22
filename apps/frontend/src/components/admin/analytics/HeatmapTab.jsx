import { Search, Zap } from 'lucide-react';
import { Card, InsightBar } from './shared';

export default function HeatmapTab({ heatmap, aiInsight, widgetErrors, load }) {
  if (!heatmap) {
    return (
      <Card title="Content Heatmap" error={!!widgetErrors.heatmap} onRetry={() => load(true)}
        empty={!widgetErrors.heatmap} emptyMsg="Heatmap data loading…" />
    );
  }

  return (
    <div className="space-y-4">
      {aiInsight && (
        <div className="flex items-start gap-3 p-4 rounded-xl"
          style={{ background: 'rgba(139,92,246,0.07)', border: '1px solid rgba(139,92,246,0.15)' }}>
          <Zap size={15} className="text-violet-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-xs font-semibold text-violet-700/80 mb-0.5">AI Content Insight</p>
            <p className="text-gray-500 text-sm leading-relaxed">{aiInsight}</p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Top Subjects by Activity"
          empty={!heatmap.top_subjects?.length} emptyMsg="No subject activity yet">
          {heatmap.top_subjects?.length > 0 && (
            <div className="space-y-2">
              {heatmap.top_subjects.map((s, i) => (
                <InsightBar key={i} label={s.name} value={s.views}
                  max={heatmap.top_subjects[0]?.views || 1} />
              ))}
            </div>
          )}
        </Card>
        <Card title="Top Search Queries"
          empty={!heatmap.top_searches?.length} emptyMsg="No search data yet">
          {heatmap.top_searches?.length > 0 && (
            <div className="space-y-2">
              {heatmap.top_searches.map((s, i) => (
                <div key={i} className="flex items-center gap-2 p-2 rounded-lg transition-colors hover:bg-gray-50">
                  <Search size={12} className="text-blue-600 flex-shrink-0" />
                  <span className="text-gray-500 text-sm flex-1 truncate">{s.query}</span>
                  <span className="text-gray-700 text-xs flex-shrink-0">{s.count}x</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
