import { useState, useEffect, useRef, useCallback } from 'react';
import { Loader2, CheckCheck, AlertTriangle, X, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import axios from 'axios';
import { API, authHeaders } from '@/utils/adminHelpers';

const POLL_INTERVAL_MS = 2500;
const AUTO_DISMISS_DONE_MS = 12000;
const AUTO_DISMISS_FAILED_MS = 30000;

function statusColor(status) {
  if (status === 'done') return '#34d399';
  if (status === 'failed') return '#f87171';
  return '#a78bfa';
}

function statusLabel(status) {
  if (status === 'done') return 'Done';
  if (status === 'failed') return 'Failed';
  if (status === 'running') return 'Indexing…';
  return 'Queued';
}

function jobLabel(job) {
  if (job.job_type === 'reindex_chapter') return `Chapter reindex`;
  if (job.job_type === 'bulk_reindex_chapters') {
    const total = job.total_chunks || 0;
    return `Bulk reindex (${total} chapter${total === 1 ? '' : 's'})`;
  }
  if (job.job_type === 'bulk_reindex_subject') return 'Subject bulk reindex';
  return job.job_type;
}

function JobRow({ job, onDismiss }) {
  const pct = job.status === 'done' ? 100
    : job.total_chunks > 0 ? Math.min(99, Math.round((job.processed_chunks / job.total_chunks) * 100))
    : (job.progress || 0);
  const col = statusColor(job.status);
  const isDone = job.status === 'done';
  const isFailed = job.status === 'failed';
  const isActive = !isDone && !isFailed;

  return (
    <div className="px-3 py-2.5 border-b last:border-b-0" style={{ borderColor: 'rgba(124,58,237,0.12)' }}>
      <div className="flex items-center gap-2 mb-1.5">
        {isDone
          ? <CheckCheck size={12} style={{ color: col, flexShrink: 0 }} />
          : isFailed
          ? <AlertTriangle size={12} style={{ color: col, flexShrink: 0 }} />
          : <Loader2 size={12} className="animate-spin flex-shrink-0" style={{ color: col }} />}
        <span className="text-[11px] font-semibold flex-1 truncate" style={{ color: '#374151' }}>
          {jobLabel(job)}
        </span>
        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full flex-shrink-0"
          style={{ background: isDone ? 'rgba(52,211,153,0.15)' : isFailed ? 'rgba(248,113,113,0.15)' : 'rgba(167,139,250,0.15)', color: col }}>
          {statusLabel(job.status)}
        </span>
        {(isDone || isFailed) && (
          <button onClick={() => onDismiss(job.job_id)}
            className="ml-1 rounded hover:opacity-70 transition-opacity"
            title="Dismiss">
            <X size={11} style={{ color: '#9ca3af' }} />
          </button>
        )}
      </div>

      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: '#e5e7eb' }}>
          <div
            className="h-1.5 rounded-full transition-all duration-500"
            style={{ width: `${isDone ? 100 : pct}%`, background: col }}
          />
        </div>
        <span className="text-[10px] font-mono flex-shrink-0" style={{ color: '#9ca3af' }}>
          {isDone ? '100%' : `${pct}%`}
        </span>
      </div>

      {isFailed && job.error_message && (
        <p className="text-[10px] mt-1 truncate" style={{ color: '#f87171' }}>
          {job.error_message}
        </p>
      )}

      {isActive && job.processed_chunks > 0 && job.total_chunks > 0 && (
        <p className="text-[10px] mt-1" style={{ color: '#9ca3af' }}>
          {job.processed_chunks} / {job.total_chunks} chapters
        </p>
      )}

      {isDone && job.result && (
        <p className="text-[10px] mt-1" style={{ color: '#6b7280' }}>
          ✓ {job.result.succeeded ?? job.total_chunks} succeeded
          {job.result.errors?.length > 0 && (
            <span style={{ color: '#f87171' }}> · {job.result.errors.length} errors</span>
          )}
        </p>
      )}
    </div>
  );
}

export default function RagJobsPanel({ trackedJobIds, adminToken, onJobComplete }) {
  const [jobs, setJobs] = useState({});
  const [collapsed, setCollapsed] = useState(false);
  const dismissedRef = useRef(new Set());
  const autoDismissTimers = useRef({});
  const pollingRef = useRef(null);

  const fetchJob = useCallback(async (jobId) => {
    try {
      const res = await axios.get(`${API}/admin/rag/jobs/${jobId}`, authHeaders(adminToken));
      return res.data;
    } catch {
      return null;
    }
  }, [adminToken]);

  const dismissJob = useCallback((jobId) => {
    dismissedRef.current.add(jobId);
    setJobs(prev => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    if (autoDismissTimers.current[jobId]) {
      clearTimeout(autoDismissTimers.current[jobId]);
      delete autoDismissTimers.current[jobId];
    }
  }, []);

  const scheduleAutoDismiss = useCallback((jobId, status) => {
    if (autoDismissTimers.current[jobId]) return;
    const delay = status === 'done' ? AUTO_DISMISS_DONE_MS : AUTO_DISMISS_FAILED_MS;
    autoDismissTimers.current[jobId] = setTimeout(() => {
      dismissJob(jobId);
    }, delay);
  }, [dismissJob]);

  useEffect(() => {
    if (!trackedJobIds?.length) return;

    const poll = async () => {
      const activeIds = trackedJobIds.filter(id => !dismissedRef.current.has(id));
      if (!activeIds.length) return;

      const fetches = await Promise.all(activeIds.map(fetchJob));
      const updates = {};
      fetches.forEach((data, i) => {
        if (!data) return;
        updates[data.job_id] = data;
        if (data.status === 'done' || data.status === 'failed') {
          scheduleAutoDismiss(data.job_id, data.status);
          if (data.status === 'done' && onJobComplete) {
            onJobComplete(data.job_id);
          }
        }
      });
      if (Object.keys(updates).length) {
        setJobs(prev => ({ ...prev, ...updates }));
      }
    };

    poll();
    pollingRef.current = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      clearInterval(pollingRef.current);
    };
  }, [trackedJobIds, fetchJob, scheduleAutoDismiss, onJobComplete]);

  useEffect(() => {
    const timers = autoDismissTimers.current;
    return () => {
      Object.values(timers).forEach(clearTimeout);
    };
  }, []);

  const visibleJobs = Object.values(jobs).filter(j => !dismissedRef.current.has(j.job_id));
  const activeCount = visibleJobs.filter(j => j.status === 'running' || j.status === 'pending').length;

  if (!visibleJobs.length) return null;

  return (
    <div
      className="fixed bottom-4 right-4 z-50 rounded-xl shadow-xl border overflow-hidden"
      style={{
        width: 300,
        background: '#fff',
        borderColor: 'rgba(124,58,237,0.25)',
        boxShadow: '0 8px 32px rgba(124,58,237,0.18), 0 2px 8px rgba(0,0,0,0.08)',
      }}
    >
      <button
        onClick={() => setCollapsed(c => !c)}
        className="w-full flex items-center justify-between px-3 py-2 transition-colors"
        style={{ background: 'rgba(124,58,237,0.06)', borderBottom: collapsed ? 'none' : '1px solid rgba(124,58,237,0.12)' }}
      >
        <div className="flex items-center gap-2">
          {activeCount > 0
            ? <RefreshCw size={12} className="animate-spin" style={{ color: '#7c3aed' }} />
            : <CheckCheck size={12} style={{ color: '#34d399' }} />}
          <span className="text-[11px] font-semibold" style={{ color: '#7c3aed' }}>
            RAG Indexing
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full"
            style={{ background: 'rgba(124,58,237,0.12)', color: '#7c3aed' }}>
            {visibleJobs.length}
          </span>
        </div>
        {collapsed
          ? <ChevronUp size={12} style={{ color: '#9ca3af' }} />
          : <ChevronDown size={12} style={{ color: '#9ca3af' }} />}
      </button>

      {!collapsed && (
        <div style={{ maxHeight: 280, overflowY: 'auto' }}>
          {visibleJobs
            .sort((a, b) => {
              const order = { running: 0, pending: 1, failed: 2, done: 3 };
              return (order[a.status] ?? 9) - (order[b.status] ?? 9);
            })
            .map(job => (
              <JobRow key={job.job_id} job={job} onDismiss={dismissJob} />
            ))}
        </div>
      )}
    </div>
  );
}
