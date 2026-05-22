import { memo } from 'react';
import { Plus, Trash2, FileText, Globe, Lock, Search } from 'lucide-react';

const STATUS_COLORS = {
  published: { bg: 'rgba(16,185,129,0.15)', border: 'rgba(16,185,129,0.35)', text: '#34d399', icon: Globe },
  draft:     { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.35)', text: '#94a3b8', icon: Lock },
};

const FILTER_OPTIONS = [
  { id: 'all',      label: 'All' },
  { id: 'published',label: 'Live' },
  { id: 'draft',    label: 'Draft' },
  { id: 'syllabus', label: 'Syllabus' },
  { id: 'revision', label: 'Revisions' },
];

const DocumentList = memo(function DocumentList({
  docs, loading, filtered, searchQ, setSearchQ,
  filterType, setFilterType, editDoc, openNew, openEdit, handleDelete,
}) {
  return (
    <div className="w-72 flex-shrink-0 border-r flex flex-col" style={{ background: '#ffffff', borderColor: '#e5e7eb' }}>
      <div className="px-3 py-3 border-b space-y-2" style={{ borderColor: '#e5e7eb' }}>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: '#9ca3af' }} />
            <input
              value={searchQ}
              onChange={e => setSearchQ(e.target.value)}
              placeholder="Search documents…"
              className="w-full h-8 pl-8 pr-3 rounded-lg text-xs outline-none"
              style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
            />
          </div>
          <button onClick={openNew} className="h-8 px-2 rounded-lg flex items-center gap-1 text-xs font-medium flex-shrink-0" style={{ background: '#7c3aed', color: 'white' }}>
            <Plus size={13} /> New
          </button>
        </div>
        <div className="flex gap-1 flex-wrap">
          {FILTER_OPTIONS.map(opt => (
            <button
              key={opt.id}
              onClick={() => setFilterType(opt.id)}
              className="px-2 py-0.5 rounded-md text-[10px] font-medium transition-colors"
              style={filterType === opt.id
                ? { background: 'rgba(124,58,237,0.12)', color: '#7c3aed', border: '1px solid rgba(124,58,237,0.15)' }
                : { background: '#f9fafb', color: '#9ca3af', border: '1px solid #e5e7eb' }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {loading ? (
          <div className="space-y-1.5 p-3">
            {[...Array(5)].map((_, i) => <div key={i} className="h-14 rounded-xl animate-pulse" style={{ background: '#f9fafb' }} />)}
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-6 text-center">
            <FileText size={28} className="mx-auto mb-3" style={{ color: '#e5e7eb' }} />
            <p className="text-xs" style={{ color: '#9ca3af' }}>{searchQ || filterType !== 'all' ? 'No results' : 'No documents yet'}</p>
            {!searchQ && filterType === 'all' && <button onClick={openNew} className="mt-3 text-xs" style={{ color: '#7c3aed' }}>Create first →</button>}
          </div>
        ) : filtered.map(doc => {
          const st = STATUS_COLORS[doc.status] || STATUS_COLORS.draft;
          const StIcon = st.icon;
          const isActive = editDoc?.id === doc.id;
          return (
            <div
              key={doc.id}
              onClick={() => openEdit(doc)}
              className="mx-2 mb-1 p-3 rounded-xl cursor-pointer group transition-colors"
              style={{ border: isActive ? '1px solid rgba(124,58,237,0.15)' : '1px solid transparent', background: isActive ? 'rgba(124,58,237,0.06)' : 'transparent' }}
            >
              <div className="flex items-start gap-2">
                <StIcon size={12} className="flex-shrink-0 mt-0.5" style={{ color: st.text }} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate leading-tight" style={{ color: isActive ? '#7c3aed' : '#374151' }}>
                    {doc.title || 'Untitled'}
                  </p>
                  <p className="text-[10px] truncate mt-0.5 font-mono" style={{ color: '#9ca3af' }}>{doc.seo_slug || '—'}</p>
                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                    <span className="text-[10px]" style={{ color: st.text }}>{doc.status}</span>
                    {doc.type === 'syllabus' && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(16,185,129,0.12)', color: '#34d399' }}>syllabus</span>}
                    {doc.is_revision && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(245,158,11,0.12)', color: '#fbbf24' }}>rev</span>}
                    {doc.word_count > 0 && <span className="text-[10px]" style={{ color: '#d1d5db' }}>{doc.word_count}w</span>}
                  </div>
                </div>
                <button
                  onClick={e => handleDelete(doc.id, e)}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded transition-all flex-shrink-0"
                  style={{ color: '#d1d5db' }}
                  onMouseEnter={e => e.currentTarget.style.color = '#f87171'}
                  onMouseLeave={e => e.currentTarget.style.color = '#d1d5db'}
                >
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="px-4 py-2 border-t" style={{ borderColor: '#e5e7eb' }}>
        <p className="text-[10px] text-center" style={{ color: '#d1d5db' }}>{docs.length} documents</p>
      </div>
    </div>
  );
});

export default DocumentList;
