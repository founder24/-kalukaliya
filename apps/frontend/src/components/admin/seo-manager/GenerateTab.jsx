import { Loader2, Sparkles, XCircle, ChevronRight } from 'lucide-react';

const PAGE_TYPES = [
  { id: 'notes',               label: 'Notes',               color: '#7c3aed' },
  { id: 'definition',          label: 'Definitions',         color: '#0891b2' },
  { id: 'important-questions', label: 'Important Questions', color: '#d97706' },
  { id: 'mcqs',                label: 'MCQs',                color: '#16a34a' },
  { id: 'examples',            label: 'Examples',            color: '#e11d48' },
];

export default function GenerateTab({
  selectedTopics, selectedTypes, topics, generating,
  toggleTopic, toggleType, handleGenerate, setTab,
}) {
  return (
    <div className="space-y-5">
      <div className="rounded-xl p-4 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm font-semibold" style={{ color: '#374151' }}>Selected Topics</p>
          <button onClick={() => setTab('topics')} className="text-xs" style={{ color: '#a78bfa' }}>
            {selectedTopics.size === 0 ? 'Select topics →' : `${selectedTopics.size} selected — change`}
          </button>
        </div>
        {selectedTopics.size === 0 ? (
          <p className="text-xs" style={{ color: '#9ca3af' }}>No topics selected. Go to the Topics tab to pick topics.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {[...selectedTopics].map(tid => {
              const t = topics.find(x => (x._id || x.id) === tid);
              return t ? (
                <span key={tid} className="px-2 py-0.5 rounded-full text-xs flex items-center gap-1"
                  style={{ background: 'rgba(124,58,237,0.12)', color: '#a78bfa', border: '1px solid rgba(124,58,237,0.25)' }}>
                  {t.title}
                  <button onClick={() => toggleTopic(tid)}><XCircle size={10} /></button>
                </span>
              ) : null;
            })}
          </div>
        )}
      </div>

      <div className="rounded-xl p-4 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <p className="text-sm font-semibold mb-3" style={{ color: '#374151' }}>Page Types to Generate</p>
        <div className="flex flex-wrap gap-2">
          {PAGE_TYPES.map(({ id, label, color }) => {
            const sel = selectedTypes.has(id);
            return (
              <button key={id} onClick={() => toggleType(id)}
                className="h-8 px-3 rounded-xl text-xs font-medium border transition-all"
                style={sel ? { background: color + '20', borderColor: color + '60', color } : { borderColor: '#d1d5db', color: '#6b7280' }}>
                {label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="rounded-xl p-4 border" style={{ background: 'rgba(124,58,237,0.05)', borderColor: 'rgba(124,58,237,0.20)' }}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold" style={{ color: '#374151' }}>
              Will generate: <span style={{ color: '#a78bfa' }}>{selectedTopics.size * selectedTypes.size} pages</span>
            </p>
            <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>
              {selectedTopics.size} topics × {selectedTypes.size} page types · Runs in background
            </p>
          </div>
          <button onClick={handleGenerate} disabled={generating || !selectedTopics.size || !selectedTypes.size}
            className="h-10 px-5 rounded-xl text-sm font-semibold flex items-center gap-2 disabled:opacity-40"
            style={{ background: '#7c3aed', color: '#fff' }}>
            {generating ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
            Generate Content
          </button>
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: '#9ca3af' }}>How it works</p>
        {[
          ['1. Extract Topics', 'Topics tab → Auto-Extract — pulls topic names from all uploaded chapters'],
          ['2. Select Topics', 'Check topics you want to generate pages for'],
          ['3. Choose Page Types', 'Notes, Definitions, MCQs, Important Questions, or Examples'],
          ['4. Generate', 'AI generates structured, exam-aligned content with GEO authority signals'],
          ['5. Publish', 'Pages go live at /{board}/{class}/{subject}/{topic}/{type}'],
        ].map(([h, d]) => (
          <div key={h} className="flex items-start gap-3 p-3 rounded-xl" style={{ background: '#f9fafb' }}>
            <ChevronRight size={13} className="flex-shrink-0 mt-0.5" style={{ color: '#7c3aed' }} />
            <div>
              <p className="text-xs font-semibold" style={{ color: '#4b5563' }}>{h}</p>
              <p className="text-[11px] mt-0.5" style={{ color: '#9ca3af' }}>{d}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
