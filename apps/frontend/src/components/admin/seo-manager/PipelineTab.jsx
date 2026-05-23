import {
  Loader2, RefreshCw, BookOpen, FileText, Globe, Search,
  Play, GitBranch, Database, Cpu, Activity, Zap,
} from 'lucide-react';
import JobProgress from './JobProgress';

export default function PipelineTab({
  subjectCoverage, coverageLoading, loadCoverage,
  subjectJobs, handleRunSubject, handleAutoRun,
  activeJob, setActiveJob, pipelineSearch, setPipelineSearch,
  handleBackfillNotes, backfilling,
  handleDiagnoseTopics, diagnostics, diagnosticsLoading,
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <p className="text-sm font-semibold" style={{ color: '#111827' }}>Subject → Topic → SEO Page Pipeline</p>
          <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>
            One click per subject: AI extracts topics → generates 5 page types per topic → RAG-ready
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={loadCoverage} disabled={coverageLoading}
            className="h-8 px-3 rounded-xl text-xs flex items-center gap-1.5 border disabled:opacity-40"
            style={{ color: '#6b7280', borderColor: '#e5e7eb' }}>
            <RefreshCw size={12} className={coverageLoading ? 'animate-spin' : ''} /> Refresh
          </button>
          {handleDiagnoseTopics && (
            <button onClick={handleDiagnoseTopics} disabled={diagnosticsLoading}
              className="h-8 px-3 rounded-xl text-xs flex items-center gap-1.5 border disabled:opacity-40"
              style={{ color: '#6b7280', borderColor: '#e5e7eb' }}>
              <Activity size={12} className={diagnosticsLoading ? 'animate-spin' : ''} /> Diagnose Topics
            </button>
          )}
          {handleBackfillNotes && (
            <button onClick={handleBackfillNotes} disabled={backfilling || (activeJob && activeJob.status !== 'done' && activeJob.status !== 'error')}
              className="h-8 px-3 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-40"
              style={{ background: '#fef3c7', color: '#92400e', border: '1px solid #fde68a' }}>
              <Zap size={12} /> Backfill Notes
            </button>
          )}
          <button onClick={handleAutoRun}
            disabled={activeJob && activeJob.status !== 'done' && activeJob.status !== 'error'}
            className="h-8 px-3 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-40"
            style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)', color: '#fff' }}>
            <Cpu size={12} /> Run All Subjects
          </button>
        </div>
      </div>

      {diagnostics && (
        <div style={{ borderRadius: 10, border: '1px solid #fde68a', background: '#fffbeb', padding: '10px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#92400e' }}>
              Topic diagnostic — {diagnostics.summary?.ready ?? 0} ready · {diagnostics.summary?.blocked ?? 0} blocked · {diagnostics.summary?.fully_covered ?? 0} fully covered
              <span style={{ color: '#a16207', fontWeight: 500, marginLeft: 6 }}>
                (of {diagnostics.summary?.total ?? 0} topics)
              </span>
            </div>
          </div>
          {diagnostics.summary?.reasons && Object.keys(diagnostics.summary.reasons).length > 0 && (
            <div style={{ fontSize: 10, color: '#78350f', marginTop: 4 }}>
              {Object.entries(diagnostics.summary.reasons).map(([r, n], i) => (
                <span key={r}>{i ? ' · ' : ''}<strong>{n}</strong> {r}</span>
              ))}
            </div>
          )}
          {Array.isArray(diagnostics.items) && diagnostics.items.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: 'pointer', fontSize: 11, color: '#92400e' }}>
                Show {Math.min(diagnostics.items.length, 25)} blocked topics
              </summary>
              <ul style={{ marginTop: 6, paddingLeft: 16, fontSize: 10.5, color: '#78350f' }}>
                {diagnostics.items.slice(0, 25).map(it => (
                  <li key={it.topic_id} style={{ margin: '3px 0' }}>
                    <strong>{it.topic_title || it.topic_id}</strong>
                    {' — '}{(it.blockers || []).join(', ') || 'unknown'}
                    {' '}<span style={{ color: '#a16207' }}>({it.subject_name || 'no subject'} · {it.page_count} pages · status={it.status})</span>
                    {it.last_error && (
                      <div style={{ color: '#b91c1c', marginLeft: 12, fontSize: 10 }}>
                        ↳ last {it.last_error.status} ({it.last_error.page_type}): {it.last_error.reason || '—'}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', padding: '8px 12px', borderRadius: 8, background: 'rgba(139,92,246,0.06)', border: '1px solid rgba(139,92,246,0.15)' }}>
        {[
          { icon: BookOpen,  label: 'Chapter',   color: '#60a5fa' },
          { label: '→' },
          { icon: GitBranch, label: 'Topics (AI)', color: '#a78bfa' },
          { label: '→' },
          { icon: FileText,  label: '5 SEO Pages', color: '#34d399' },
          { label: '→' },
          { icon: Database,  label: 'RAG Card',   color: '#fbbf24' },
          { label: '→' },
          { icon: Globe,     label: 'SERP URLs',  color: '#f87171' },
        ].map((s, i) => {
          if (s.label === '→') return <span key={i} style={{ color: '#d1d5db', fontSize: 12 }}>→</span>;
          const Icon = s.icon;
          return (
            <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, color: s.color }}>
              <Icon size={10} />{s.label}
            </span>
          );
        })}
      </div>

      {activeJob && <JobProgress job={activeJob} onDismiss={() => setActiveJob(null)} />}

      {subjectCoverage.length > 0 && (() => {
        const complete = subjectCoverage.filter(s => s.status === 'complete').length;
        const partial  = subjectCoverage.filter(s => s.status === 'partial').length;
        const noPages  = subjectCoverage.filter(s => s.status === 'no_pages').length;
        const totalPages = subjectCoverage.reduce((a, s) => a + s.seo_pages, 0);
        return (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8 }}>
            {[
              { label: 'Subjects',    value: subjectCoverage.length, color: '#111827' },
              { label: '✅ Complete', value: complete,  color: '#34d399' },
              { label: '⚡ Partial',  value: partial,   color: '#fbbf24' },
              { label: '🔴 No Pages', value: noPages,   color: '#f87171' },
              { label: 'SEO Pages',   value: totalPages, color: '#a78bfa' },
            ].map(s => (
              <div key={s.label} style={{ padding: '10px 12px', borderRadius: 10, background: '#f9fafb', border: '1px solid #e5e7eb', textAlign: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: s.color }}>{s.value}</div>
                <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 2 }}>{s.label}</div>
              </div>
            ))}
          </div>
        );
      })()}

      <div style={{ position: 'relative' }}>
        <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#9ca3af' }} />
        <input value={pipelineSearch} onChange={e => setPipelineSearch(e.target.value)}
          placeholder="Search subjects…" style={{
            width: '100%', height: 36, paddingLeft: 32, paddingRight: 12, borderRadius: 10,
            background: '#f9fafb', border: '1px solid #e5e7eb',
            color: '#111827', fontSize: 13, outline: 'none',
          }} />
      </div>

      {coverageLoading ? (
        <div style={{ textAlign: 'center', padding: 32, color: '#9ca3af', fontSize: 13, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          <Loader2 size={16} className="animate-spin" /> Loading subjects…
        </div>
      ) : subjectCoverage.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 32, color: '#d1d5db', fontSize: 13 }}>
          No subjects found. Import a syllabus PDF first.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {subjectCoverage.filter(s =>
            !pipelineSearch.trim() ||
            (s.subject_name || '').toLowerCase().includes(pipelineSearch.toLowerCase()) ||
            (s.board_name || '').toLowerCase().includes(pipelineSearch.toLowerCase()) ||
            (s.class_name || '').toLowerCase().includes(pipelineSearch.toLowerCase())
          ).map(subj => {
            const job = subjectJobs[subj.subject_id];
            const isRunning = job && (job.status === 'queued' || job.status === 'extracting' || job.status === 'generating');
            const isDone    = job?.status === 'done';
            const isError   = job?.status === 'error';

            const statusColor = {
              complete:  '#34d399',
              partial:   '#fbbf24',
              no_pages:  '#f87171',
              no_topics: '#94a3b8',
            }[subj.status] || '#94a3b8';

            const statusLabel = {
              complete:  '✅ Complete',
              partial:   '⚡ Partial',
              no_pages:  '🔴 No Pages',
              no_topics: '🟡 No Topics',
            }[subj.status] || '?';

            const progress = job?.total > 0 ? (job.done / job.total) : 0;

            return (
              <div key={subj.subject_id} style={{
                border: `1px solid ${isRunning ? 'rgba(139,92,246,0.35)' : isDone ? 'rgba(52,211,153,0.25)' : '#e5e7eb'}`,
                borderRadius: 10,
                background: isRunning ? 'rgba(139,92,246,0.05)' : isDone ? 'rgba(52,211,153,0.04)' : '#ffffff',
                overflow: 'hidden',
                transition: 'all 0.25s',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#111827' }}>{subj.subject_name}</div>
                    <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 2 }}>
                      {[subj.board_name, subj.class_name, subj.stream].filter(Boolean).join(' / ')}
                    </div>
                  </div>

                  {[
                    { label: 'Chapters',   val: subj.chapters,  icon: BookOpen   },
                    { label: 'Topics',     val: subj.topics,    icon: GitBranch  },
                    { label: 'SEO Pages',  val: subj.seo_pages, icon: FileText   },
                  ].map(st => {
                    const Icon = st.icon;
                    return (
                      <div key={st.label} style={{ textAlign: 'center', minWidth: 52 }}>
                        <div style={{ fontSize: 14, fontWeight: 700, color: '#111827' }}>{st.val}</div>
                        <div style={{ fontSize: 9, color: '#9ca3af', display: 'flex', alignItems: 'center', gap: 2, justifyContent: 'center' }}>
                          <Icon size={8} />{st.label}
                        </div>
                      </div>
                    );
                  })}

                  <div style={{ minWidth: 80, textAlign: 'center' }}>
                    <div style={{ fontSize: 14, fontWeight: 700, color: statusColor }}>{subj.coverage_pct}%</div>
                    <div style={{ height: 3, borderRadius: 2, background: '#e5e7eb', overflow: 'hidden', marginTop: 4 }}>
                      <div style={{ height: '100%', borderRadius: 2, background: statusColor, width: `${Math.min(subj.coverage_pct, 100)}%`, transition: 'width 0.4s' }} />
                    </div>
                    <div style={{ fontSize: 9, color: statusColor, marginTop: 2, fontWeight: 600 }}>{statusLabel}</div>
                  </div>

                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    {isRunning ? (
                      <div style={{ padding: '4px 10px', borderRadius: 6, fontSize: 10, color: '#c4b5fd', background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)', display: 'flex', alignItems: 'center', gap: 5 }}>
                        <Loader2 size={10} className="animate-spin" /> {job.status === 'extracting' ? 'Extracting…' : 'Generating…'}
                      </div>
                    ) : (
                      <>
                        <button
                          onClick={() => handleRunSubject(subj.subject_id, subj.subject_name, false)}
                          style={{ padding: '5px 10px', borderRadius: 6, fontSize: 10, fontWeight: 700, color: '#fff', background: 'linear-gradient(135deg,#7c3aed,#a855f7)', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                          <Play size={9} /> Run
                        </button>
                        {subj.seo_pages > 0 && (
                          <button
                            onClick={() => handleRunSubject(subj.subject_id, subj.subject_name, true)}
                            title="Force regenerate all topics + pages"
                            style={{ padding: '5px 10px', borderRadius: 6, fontSize: 10, fontWeight: 600, color: '#6b7280', background: '#f9fafb', border: '1px solid #e5e7eb', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                            <RefreshCw size={9} /> Regen
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>

                {isRunning && job?.total > 0 && (
                  <div style={{ padding: '0 14px 10px' }}>
                    <div style={{ height: 3, borderRadius: 2, background: '#e5e7eb', overflow: 'hidden' }}>
                      <div style={{ height: '100%', background: '#8b5cf6', borderRadius: 2, width: `${progress * 100}%`, transition: 'width 0.5s' }} />
                    </div>
                    <div style={{ fontSize: 9, color: '#9ca3af', marginTop: 3 }}>{job.current}</div>
                  </div>
                )}

                {isDone && (
                  <div style={{ padding: '0 14px 8px', fontSize: 10, color: '#34d399' }}>
                    ✅ {job.current}
                  </div>
                )}
                {isError && (
                  <div style={{ padding: '0 14px 8px', fontSize: 10, color: '#f87171' }}>
                    ❌ {job.current}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div style={{ padding: '10px 14px', borderRadius: 10, background: '#f9fafb', border: '1px solid #e5e7eb', fontSize: 11 }}>
        <div style={{ fontWeight: 700, color: '#6b7280', marginBottom: 6 }}>Generated URL Pattern</div>
        <code style={{ color: '#a78bfa', fontSize: 10.5 }}>
          /seo/html/<span style={{ color: '#60a5fa' }}>{'{board}'}</span>/<span style={{ color: '#34d399' }}>{'{class}'}</span>/<span style={{ color: '#fbbf24' }}>{'{subject}'}</span>/<span style={{ color: '#f87171' }}>{'{topic}'}</span>/<span style={{ color: '#94a3b8' }}>[notes|definition|important-questions|mcqs|examples]</span>
        </code>
        <div style={{ marginTop: 6, color: '#d1d5db', fontSize: 10 }}>
          Example: /seo/html/degree/semester-1/economics/law-of-demand/notes
        </div>
      </div>
    </div>
  );
}
