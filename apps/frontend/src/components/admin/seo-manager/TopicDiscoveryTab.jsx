import { useEffect, useState, useCallback, useMemo } from 'react';
import { Loader2, Play, RefreshCw, Check, X, ArrowUpRight, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import {
  adminTopicDiscoveryRuns,
  adminTopicDiscoveryCandidates,
  adminTopicDiscoveryRunNow,
  adminTopicDiscoveryOverride,
} from '@/utils/api';

const DECISION_LABELS = {
  auto_published: { label: 'Auto-published', color: '#10b981', bg: '#ecfdf5' },
  drafted:        { label: 'Drafted',        color: '#f59e0b', bg: '#fffbeb' },
  rejected:       { label: 'Rejected',       color: '#9ca3af', bg: '#f9fafb' },
  error:          { label: 'Error',          color: '#ef4444', bg: '#fef2f2' },
};

function fmt(dt) {
  if (!dt) return '—';
  try { return new Date(dt).toLocaleString(); } catch { return dt; }
}

function ScoreBars({ score }) {
  if (!score) return <span className="text-gray-300 text-[11px]">—</span>;
  const items = [
    ['Intent', score.intent_fit],
    ['Syllabus', score.syllabus_alignment],
    ['Diff.', score.difficulty],
    ['AEO', score.aeo_readability],
  ];
  return (
    <div className="flex flex-col gap-0.5 min-w-[120px]">
      {items.map(([label, v]) => (
        <div key={label} className="flex items-center gap-1.5 text-[10px]">
          <span className="text-gray-400 w-12">{label}</span>
          <div className="flex-1 h-1.5 rounded bg-gray-100 overflow-hidden">
            <div className="h-full" style={{
              width: `${Math.max(0, Math.min(100, v || 0))}%`,
              background: (v || 0) >= 70 ? '#10b981' : (v || 0) >= 40 ? '#f59e0b' : '#ef4444',
            }} />
          </div>
          <span className="text-gray-500 w-6 text-right">{v ?? 0}</span>
        </div>
      ))}
    </div>
  );
}

export default function TopicDiscoveryTab({ adminToken }) {
  const [runs, setRuns]                 = useState([]);
  const [runsLoading, setRunsLoading]   = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [candidates, setCandidates]     = useState([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [filterDecision, setFilterDecision] = useState('');
  const [running, setRunning]           = useState(false);
  const [overriding, setOverriding]     = useState(null);
  const [page, setPage]                 = useState(0);
  const PAGE_SIZE                       = 50;

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const res = await adminTopicDiscoveryRuns(adminToken, 20);
      const list = res.data?.runs || [];
      setRuns(list);
      if (!selectedRunId && list.length) setSelectedRunId(list[0].id);
    } catch {
      toast.error('Failed to load topic-discovery runs');
    } finally {
      setRunsLoading(false);
    }
  }, [adminToken, selectedRunId]);

  const loadCandidates = useCallback(async () => {
    if (!selectedRunId) { setCandidates([]); return; }
    setCandidatesLoading(true);
    try {
      const res = await adminTopicDiscoveryCandidates(adminToken, {
        runId: selectedRunId,
        decision: filterDecision || null,
        limit: PAGE_SIZE,
        skip: page * PAGE_SIZE,
      });
      setCandidates(res.data?.candidates || []);
    } catch {
      toast.error('Failed to load candidates');
    } finally {
      setCandidatesLoading(false);
    }
  }, [adminToken, selectedRunId, filterDecision, page]);

  // Reset to page 0 whenever the run/filter changes so admins don't
  // land on an empty page from the previous filter's tail.
  useEffect(() => { setPage(0); }, [selectedRunId, filterDecision]);

  useEffect(() => { loadRuns(); }, [loadRuns]);
  useEffect(() => { loadCandidates(); }, [loadCandidates]);

  const handleRunNow = async () => {
    setRunning(true);
    try {
      const res = await adminTopicDiscoveryRunNow(adminToken);
      toast.success(`Run finished — ${res.data?.totals?.deduped ?? 0} candidates graded`);
      await loadRuns();
      if (res.data?.id) setSelectedRunId(res.data.id);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Run failed');
    } finally {
      setRunning(false);
    }
  };

  const handleOverride = async (candidateId, decision) => {
    const reason = window.prompt(
      `Override decision to "${decision}". Add a short note (kept as a few-shot example):`,
      '',
    );
    if (reason === null) return;
    setOverriding(candidateId);
    try {
      await adminTopicDiscoveryOverride(adminToken, candidateId, decision, reason);
      toast.success('Decision overridden');
      await loadCandidates();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Override failed');
    } finally {
      setOverriding(null);
    }
  };

  const selectedRun = useMemo(
    () => runs.find(r => r.id === selectedRunId) || null,
    [runs, selectedRunId],
  );

  return (
    <div className="space-y-4" data-testid="topic-discovery-tab">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-base font-bold text-gray-900">Autonomous Topic Discovery</h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Nightly agent grades GSC near-misses, Suggest expansions and trending queries —
            the highest-scoring candidates are auto-enqueued for content generation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadRuns}
            disabled={runsLoading}
            className="p-1.5 rounded-lg border border-gray-200 hover:bg-gray-50 text-gray-400"
            data-testid="topic-discovery-refresh"
          >
            <RefreshCw size={14} className={runsLoading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={handleRunNow}
            disabled={running}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-50 border border-violet-200 hover:bg-violet-100 text-violet-600"
            data-testid="topic-discovery-run-now"
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            Run now
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-4">
        <div className="rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
            Recent runs
          </div>
          <div className="max-h-[480px] overflow-y-auto" data-testid="topic-discovery-runs">
            {runsLoading && runs.length === 0 ? (
              <div className="flex items-center justify-center py-6 text-gray-400 text-xs">
                <Loader2 size={14} className="animate-spin mr-1.5" /> Loading…
              </div>
            ) : runs.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-gray-400">
                No runs yet — click <strong>Run now</strong> to seed one.
              </div>
            ) : runs.map(r => {
              const t = r.totals || {};
              const isSel = r.id === selectedRunId;
              return (
                <button
                  key={r.id}
                  onClick={() => setSelectedRunId(r.id)}
                  className={`w-full text-left px-3 py-2 border-b border-gray-100 hover:bg-violet-50/40 ${
                    isSel ? 'bg-violet-50' : ''
                  }`}
                  data-testid={`topic-discovery-run-${r.id}`}
                >
                  <div className="text-[11px] font-mono text-gray-500 truncate">{r.id}</div>
                  <div className="text-[10px] text-gray-400 mt-0.5">{fmt(r.startedAt)}</div>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    <span className="text-[10px] px-1 py-0.5 rounded" style={{ background: '#ecfdf5', color: '#10b981' }}>
                      auto {t.auto_published || 0}
                    </span>
                    <span className="text-[10px] px-1 py-0.5 rounded" style={{ background: '#fffbeb', color: '#f59e0b' }}>
                      draft {t.drafted || 0}
                    </span>
                    <span className="text-[10px] px-1 py-0.5 rounded" style={{ background: '#f9fafb', color: '#9ca3af' }}>
                      rej {t.rejected || 0}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="rounded-xl border border-gray-200">
          <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 flex items-center justify-between gap-2 flex-wrap">
            <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
              Candidates {selectedRun ? `· ${fmt(selectedRun.startedAt)}` : ''}
            </div>
            <div className="flex items-center gap-1">
              {['', 'auto_published', 'drafted', 'rejected', 'error'].map(d => (
                <button
                  key={d || 'all'}
                  onClick={() => setFilterDecision(d)}
                  className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                    filterDecision === d ? 'bg-violet-600 text-white' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                  }`}
                  data-testid={`topic-discovery-filter-${d || 'all'}`}
                >
                  {d ? (DECISION_LABELS[d]?.label || d) : 'All'}
                </button>
              ))}
            </div>
          </div>

          <div className="max-h-[480px] overflow-y-auto" data-testid="topic-discovery-candidates">
            {candidatesLoading ? (
              <div className="flex items-center justify-center py-6 text-gray-400 text-xs">
                <Loader2 size={14} className="animate-spin mr-1.5" /> Loading…
              </div>
            ) : candidates.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-gray-400">
                {selectedRunId
                  ? 'No candidates match the current filter.'
                  : 'Select a run on the left to see its candidates.'}
              </div>
            ) : (
              <table className="w-full text-xs">
                <thead className="bg-white border-b border-gray-100 sticky top-0">
                  <tr className="text-left text-[10px] uppercase tracking-wide text-gray-400">
                    <th className="px-3 py-2">Query</th>
                    <th className="px-3 py-2">Score</th>
                    <th className="px-3 py-2">Decision</th>
                    <th className="px-3 py-2">Reason</th>
                    <th className="px-3 py-2 text-right">Override</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map(c => {
                    const d = DECISION_LABELS[c.decision] || DECISION_LABELS.rejected;
                    return (
                      <tr key={c.id} className="border-b border-gray-100 hover:bg-gray-50/50"
                          data-testid={`topic-discovery-candidate-${c.id}`}>
                        <td className="px-3 py-2 align-top">
                          <div className="font-medium text-gray-700">{c.query || '—'}</div>
                          <div className="text-[10px] text-gray-400 mt-0.5">
                            {(c.sources || []).join(' · ')}
                          </div>
                          {c.enqueuedTopic && (
                            <div className="flex items-center gap-1 mt-1 text-[10px] text-emerald-600">
                              <ArrowUpRight size={10} /> queued: {c.enqueuedTopic}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2 align-top">
                          <ScoreBars score={c.score} />
                          <div className="text-[10px] text-gray-400 mt-1">
                            total <strong className="text-gray-700">{c.score?.total ?? 0}</strong>
                          </div>
                        </td>
                        <td className="px-3 py-2 align-top">
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold"
                                style={{ background: d.bg, color: d.color }}>
                            {d.label}
                          </span>
                          {c.adminDecision && (
                            <div className="text-[10px] text-violet-600 mt-1">
                              admin override
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2 align-top text-gray-500">
                          {c.score?.reason || c.decisionReason || (c.decision === 'error' && (
                            <span className="flex items-center gap-1 text-rose-500"><AlertCircle size={10} /> grader failed</span>
                          ))}
                          {c.adminReason && (
                            <div className="text-[10px] text-violet-500 mt-1 italic">
                              note: {c.adminReason}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2 align-top text-right whitespace-nowrap">
                          {overriding === c.id ? (
                            <Loader2 size={12} className="animate-spin inline-block text-gray-400" />
                          ) : (
                            <div className="inline-flex gap-1">
                              {c.decision !== 'auto_published' && (
                                <button
                                  onClick={() => handleOverride(c.id, 'auto_published')}
                                  className="p-1 rounded hover:bg-emerald-50 text-emerald-600"
                                  title="Promote to auto-published"
                                  data-testid={`topic-discovery-promote-${c.id}`}
                                >
                                  <Check size={12} />
                                </button>
                              )}
                              {c.decision !== 'rejected' && (
                                <button
                                  onClick={() => handleOverride(c.id, 'rejected')}
                                  className="p-1 rounded hover:bg-rose-50 text-rose-500"
                                  title="Reject"
                                  data-testid={`topic-discovery-reject-${c.id}`}
                                >
                                  <X size={12} />
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
          {/* Pagination — backend supports skip/limit; the table page is
              50 rows. We can't know total without an extra count call,
              so Next is enabled whenever the current page is full. */}
          <div
            className="flex items-center justify-between px-3 py-2 border-t border-gray-200 text-xs text-gray-600"
            data-testid="topic-discovery-pagination"
          >
            <div>
              Showing rows {candidates.length === 0 ? 0 : page * PAGE_SIZE + 1}–
              {page * PAGE_SIZE + candidates.length}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0 || candidatesLoading}
                className="px-2 py-1 rounded border border-gray-300 disabled:opacity-50"
                data-testid="topic-discovery-page-prev"
              >
                ← Prev
              </button>
              <span data-testid="topic-discovery-page-current">
                Page {page + 1}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => p + 1)}
                disabled={candidates.length < PAGE_SIZE || candidatesLoading}
                className="px-2 py-1 rounded border border-gray-300 disabled:opacity-50"
                data-testid="topic-discovery-page-next"
              >
                Next →
              </button>
            </div>
          </div>
        </div>
      </div>

      {overrideModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="bg-white rounded-xl shadow-xl p-5 w-80 max-w-full">
            <h4 className="text-sm font-bold text-gray-900 mb-1">Override decision</h4>
            <p className="text-xs text-gray-500 mb-3">
              Set to <strong>{overrideModal.decision}</strong>. Add a short note (kept as a few-shot example):
            </p>
            <input
              ref={overrideReasonRef}
              type="text"
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submitOverride(); }}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-3 outline-none focus:border-violet-400"
              placeholder="Reason (optional)"
              data-testid="topic-discovery-override-reason"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setOverrideModal(null)}
                className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={submitOverride}
                className="px-3 py-1.5 text-xs rounded-lg bg-violet-600 text-white hover:bg-violet-700 font-semibold"
                data-testid="topic-discovery-override-submit"
              >
                Submit
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
