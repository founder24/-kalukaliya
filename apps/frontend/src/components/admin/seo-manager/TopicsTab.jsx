import { Loader2, Search, BookOpen, CheckCircle2, Trash2, RefreshCw, Zap, ArrowRight } from 'lucide-react';

export default function TopicsTab({
  loading, filteredTopics, topics, topicSearch, setTopicSearch,
  selectedTopics, toggleTopic, extracting, handleExtract,
  handleDeleteTopic, hubCtx, scopeSubjectOnly, setScopeSubjectOnly,
  onNavigate, setTab,
}) {
  return (
    <div className="space-y-3">
      {hubCtx?.subjectId && (
        <div className="flex items-center justify-between px-4 py-2.5 rounded-xl"
          style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.22)' }}>
          <div className="flex items-center gap-2">
            <BookOpen size={13} style={{ color: '#a78bfa' }} />
            <span className="text-xs font-semibold" style={{ color: '#c4b5fd' }}>
              Active subject:
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full font-medium"
              style={{ background: 'rgba(139,92,246,0.20)', color: '#ddd6fe' }}>
              {[hubCtx.boardName, hubCtx.className, hubCtx.streamName, hubCtx.subjectName]
                .filter(Boolean).join(' › ')}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={scopeSubjectOnly}
                onChange={e => setScopeSubjectOnly(e.target.checked)}
                className="rounded"
              />
              <span className="text-[11px]" style={{ color: '#6b7280' }}>
                Show this subject only
              </span>
            </label>
          </div>
        </div>
      )}

      <div className="flex gap-2 flex-wrap items-center">
        <div className="relative flex-1 min-w-48">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: '#9ca3af' }} />
          <input value={topicSearch} onChange={e => setTopicSearch(e.target.value)} placeholder="Search topics…"
            className="w-full h-9 pl-8 pr-3 rounded-xl text-sm outline-none"
            style={{ background: '#f9fafb', border: '1px solid #e5e7eb', color: '#374151' }}
          />
        </div>
        <button onClick={() => handleExtract(false)} disabled={extracting}
          className="h-9 px-4 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
          style={{ background: '#7c3aed', color: '#fff' }}>
          {extracting ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
          {hubCtx?.subjectName
            ? `Auto-Extract from ${hubCtx.subjectName}`
            : 'Auto-Extract from Chapters'}
        </button>
        {hubCtx?.subjectId && (
          <button onClick={() => handleExtract(true)} disabled={extracting}
            title="Re-extract and replace existing topics"
            className="h-9 px-3 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
            style={{ background: 'rgba(239,68,68,0.12)', color: '#fca5a5', border: '1px solid rgba(239,68,68,0.25)' }}>
            <RefreshCw size={12} />
            Re-extract
          </button>
        )}
      </div>

      {loading ? (
        <div className="space-y-2">{[...Array(6)].map((_, i) => <div key={i} className="h-14 rounded-xl animate-pulse" style={{ background: '#f9fafb' }} />)}</div>
      ) : filteredTopics.length === 0 ? (
        <div className="rounded-xl p-10 text-center border" style={{ background: '#ffffff', borderColor: '#e5e7eb' }}>
          <BookOpen size={28} className="mx-auto mb-3" style={{ color: '#e5e7eb' }} />
          <p className="text-sm" style={{ color: '#9ca3af' }}>
            {topics.length === 0
              ? 'No topics yet. Click "Auto-Extract from Chapters" to bootstrap.'
              : 'No topics match your search.'}
          </p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {filteredTopics.map(topic => {
            const tid = topic._id || topic.id;
            const isSel = selectedTopics.has(tid);
            return (
              <div key={tid} onClick={() => toggleTopic(tid)}
                className="flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all"
                style={{
                  background: isSel ? 'rgba(124,58,237,0.08)' : '#f9fafb',
                  borderColor: isSel ? 'rgba(124,58,237,0.35)' : '#e5e7eb',
                }}>
                <div className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 border transition-all ${isSel ? 'border-violet-500' : ''}`}
                  style={isSel ? { background: '#7c3aed', borderColor: '#7c3aed' } : { borderColor: '#d1d5db' }}>
                  {isSel && <CheckCircle2 size={10} className="text-white" />}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate" style={{ color: '#374151' }}>{topic.title}</p>
                  <p className="text-xs truncate" style={{ color: '#9ca3af' }}>
                    {[topic.subject_name, topic.chapter_title].filter(Boolean).join(' › ')}
                  </p>
                </div>
                <span className="text-[10px] font-mono hidden sm:inline" style={{ color: '#d1d5db' }}>{topic.slug}</span>
                {onNavigate && (
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      try {
                        localStorage.setItem('syrabit_cms_prefill', JSON.stringify({
                          subjectId:  topic.subject_id  || '',
                          chapterId:  topic.chapter_id  || '',
                          topicTitle: topic.title       || '',
                          topicSlug:  topic.slug        || '',
                          timestamp:  Date.now(),
                        }));
                      } catch (err) {
                        console.warn('TopicsTab: failed to stash editor prefill in localStorage:', err);
                      }
                      onNavigate('editor');
                    }}
                    className="flex-shrink-0 flex items-center gap-1 h-6 px-2 rounded text-[10px] font-semibold transition-all hover:opacity-80"
                    style={{ background: 'rgba(6,182,212,0.12)', color: '#67e8f9', border: '1px solid rgba(6,182,212,0.22)' }}
                    title="Open in Content Editor"
                  >
                    Write →
                  </button>
                )}
                <button onClick={e => { e.stopPropagation(); handleDeleteTopic(topic); }}
                  className="flex-shrink-0 p-1 rounded transition-colors" style={{ color: '#d1d5db' }}>
                  <Trash2 size={13} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {selectedTopics.size > 0 && (
        <div className="flex items-center justify-between p-3 rounded-xl border"
          style={{ background: 'rgba(124,58,237,0.08)', borderColor: 'rgba(124,58,237,0.30)' }}>
          <span className="text-sm" style={{ color: '#7c3aed' }}>{selectedTopics.size} topic{selectedTopics.size !== 1 ? 's' : ''} selected</span>
          <button onClick={() => setTab('generate')}
            className="h-8 px-3 rounded-lg text-xs font-semibold flex items-center gap-1"
            style={{ background: '#7c3aed', color: '#fff' }}>
            Generate Content <ArrowRight size={12} />
          </button>
        </div>
      )}
    </div>
  );
}
