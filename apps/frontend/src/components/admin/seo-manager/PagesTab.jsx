import { Search, FileText, Eye, EyeOff, Globe, Play } from 'lucide-react';

const PAGE_TYPES = [
  { id: 'notes',               label: 'Notes',               color: '#7c3aed' },
  { id: 'definition',          label: 'Definitions',         color: '#0891b2' },
  { id: 'important-questions', label: 'Important Questions', color: '#d97706' },
  { id: 'mcqs',                label: 'MCQs',                color: '#16a34a' },
  { id: 'examples',            label: 'Examples',            color: '#e11d48' },
];

const STATUS_COLORS = {
  published: { text: '#34d399', bg: 'rgba(16,185,129,0.10)', border: 'rgba(52,211,153,0.20)' },
  draft:     { text: '#fbbf24', bg: 'rgba(245,158,11,0.10)',  border: 'rgba(251,191,36,0.20)' },
  archived:  { text: '#9ca3af', bg: 'rgba(156,163,175,0.10)', border: 'rgba(156,163,175,0.20)' },
};

const SORT_OPTIONS = [
  { id: 'combined', label: 'Combined ↓' },
  { id: 'geo',      label: 'GEO ↓' },
  { id: 'seo',      label: 'SEO ↓' },
  { id: 'recent',   label: 'Recent' },
];

const COMBINED_THRESHOLDS = [0, 40, 60, 70, 80];

function scoreColor(n) {
  if (n >= 70) return { bg: 'rgba(34,197,94,0.15)',  fg: '#16a34a' };
  if (n >= 40) return { bg: 'rgba(245,158,11,0.15)', fg: '#d97706' };
  return { bg: 'rgba(239,68,68,0.15)', fg: '#dc2626' };
}

function getSeoScore(p)      { return p.quality_score?.score ?? p.quality?.score ?? null; }
function getGeoScore(p)      { return p.geo_score?.score ?? p.quality?.geo_score ?? null; }
function getCombinedScore(p) {
  if (typeof p.combined_score === 'number') return p.combined_score;
  if (typeof p.quality?.combined_score === 'number') return p.quality.combined_score;
  const s = getSeoScore(p), g = getGeoScore(p);
  if (s == null && g == null) return null;
  return Math.round(((s ?? 0) + (g ?? 0)) / 2);
}

export default function PagesTab({
  loading, filteredPages, pages, publishedCount, draftCount,
  pageSearch, setPageSearch, pageFilter, setPageFilter,
  pageSort, setPageSort, minCombined, setMinCombined,
  handleToggleStatus, handleAutoRun,
}) {
  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap">
        <div className="relative flex-1 min-w-48">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: '#9ca3af' }} />
          <input value={pageSearch} onChange={e => setPageSearch(e.target.value)} placeholder="Search pages or summaries…"
            className="w-full h-9 pl-8 pr-3 rounded-xl text-sm outline-none"
            style={{ background: '#f9fafb', border: '1px solid #e5e7eb', color: '#374151' }}
          />
        </div>
        {['all', 'published', 'draft'].map(f => (
          <button key={f} onClick={() => setPageFilter(f)}
            className="h-9 px-3 rounded-xl text-xs capitalize font-medium transition-all"
            style={pageFilter === f
              ? { background: '#7c3aed', color: '#fff' }
              : { color: '#6b7280', border: '1px solid #e5e7eb' }}>
            {f === 'all' ? 'All' : f === 'published' ? `Published (${publishedCount})` : `Draft (${draftCount})`}
          </button>
        ))}
        <select value={pageSort} onChange={e => setPageSort(e.target.value)}
          className="h-9 px-2 rounded-xl text-xs font-medium outline-none"
          style={{ background: '#f9fafb', border: '1px solid #e5e7eb', color: '#374151' }}>
          {SORT_OPTIONS.map(o => <option key={o.id} value={o.id}>Sort: {o.label}</option>)}
        </select>
        <select value={minCombined} onChange={e => setMinCombined(Number(e.target.value))}
          className="h-9 px-2 rounded-xl text-xs font-medium outline-none"
          style={{ background: '#f9fafb', border: '1px solid #e5e7eb', color: '#374151' }}>
          {COMBINED_THRESHOLDS.map(n => (
            <option key={n} value={n}>{n === 0 ? 'Combined: any' : `Combined ≥ ${n}`}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="space-y-2">{[...Array(5)].map((_, i) => <div key={i} className="h-16 rounded-xl animate-pulse" style={{ background: '#f9fafb' }} />)}</div>
      ) : filteredPages.length === 0 ? (
        <div className="rounded-xl p-10 text-center border" style={{ background: '#ffffff', borderColor: '#e5e7eb' }}>
          <FileText size={28} className="mx-auto mb-3" style={{ color: '#e5e7eb' }} />
          <p className="text-sm" style={{ color: '#9ca3af' }}>
            {pages.length === 0
              ? 'No SEO pages yet. Click Auto-Run All to start the pipeline.'
              : 'No pages match your filter.'}
          </p>
          {pages.length === 0 && (
            <button onClick={handleAutoRun} className="mt-4 h-9 px-5 rounded-xl text-xs font-semibold flex items-center gap-2 mx-auto"
              style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)', color: '#fff' }}>
              <Play size={13} /> Auto-Run All
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-1.5">
          {filteredPages.map(page => {
            const pid = page._id || page.id;
            const sc = STATUS_COLORS[page.status] || STATUS_COLORS.draft;
            const typeInfo = PAGE_TYPES.find(p => p.id === page.page_type);
            const seo = getSeoScore(page);
            const geo = getGeoScore(page);
            const combined = getCombinedScore(page);
            const wc = page.quality_score?.word_count;
            const summary = (page.answer_summary || '').trim();
            const factsCount = Array.isArray(page.key_facts) ? page.key_facts.length : 0;
            return (
              <div key={pid} className="flex items-start gap-3 p-3 rounded-xl border transition-colors"
                style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
                {typeInfo && (
                  <div className="w-1.5 h-8 rounded-full flex-shrink-0 mt-1" style={{ background: typeInfo.color }} />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate" style={{ color: '#374151' }}>{page.title || page.topic_title || '—'}</p>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    <p className="text-xs truncate" style={{ color: '#9ca3af' }}>
                      {[page.board_name, page.class_name, page.subject_name, page.page_type].filter(Boolean).join(' · ')}
                    </p>
                    {seo != null && (() => { const c = scoreColor(seo); return (
                      <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold"
                        style={{ background: c.bg, color: c.fg }}
                        title="SEO score">
                        SEO:{seo}{wc != null ? ` · ${wc}w` : ''}
                      </span>
                    ); })()}
                    {geo != null && (() => { const c = scoreColor(geo); return (
                      <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold"
                        style={{ background: c.bg, color: c.fg }}
                        title="GEO (generative-answer) score">
                        GEO:{geo}{factsCount ? ` · ${factsCount}f` : ''}
                      </span>
                    ); })()}
                    {combined != null && (() => { const c = scoreColor(combined); return (
                      <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold border"
                        style={{ background: c.bg, color: c.fg, borderColor: c.fg }}
                        title="Combined SEO + GEO score">
                        C:{combined}
                      </span>
                    ); })()}
                  </div>
                  {summary && (
                    <p className="text-[11px] mt-1 line-clamp-2" style={{ color: '#6b7280' }} title={summary}>
                      {summary}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1 flex-shrink-0 mt-0.5">
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold border"
                    style={{ color: sc.text, background: sc.bg, borderColor: sc.border }}>
                    {page.status || 'draft'}
                  </span>
                  <button onClick={() => handleToggleStatus(page)} title={page.status === 'published' ? 'Unpublish' : 'Publish'}
                    className="p-1.5 rounded-lg transition-colors"
                    style={{ color: '#9ca3af' }}>
                    {page.status === 'published' ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                  {page.url && (
                    <a href={page.url} target="_blank" rel="noopener"
                      className="p-1.5 rounded-lg transition-colors"
                      style={{ color: '#9ca3af' }}>
                      <Globe size={14} />
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
