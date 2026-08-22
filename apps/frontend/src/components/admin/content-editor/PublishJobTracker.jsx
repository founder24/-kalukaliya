import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Loader2, CheckCircle, XCircle, Clock, RefreshCw,
  Globe, Database, Zap, Link, BookOpen, Server, ChevronDown, ChevronUp,
} from 'lucide-react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';

const STEP_ICONS = {
  gcs: Server,
  cloudflare: Globe,
  status_update: Database,
  pages_rebuild: RefreshCw,
  indexnow: Zap,
  wikidata: Link,
  embeddings: BookOpen,
};

const STEP_LABELS = {
  gcs: 'Write to GCS',
  cloudflare: 'Cloudflare prerender',
  status_update: 'Update DB status',
  pages_rebuild: 'Pages rebuild',
  indexnow: 'IndexNow ping',
  wikidata: 'Wikidata enrichment',
  embeddings: 'Topic embeddings',
};

function StepRow({ step }) {
  const Icon = STEP_ICONS[step.name] || Database;
  const label = STEP_LABELS[step.name] || step.label || step.name;

  const color = step.status === 'done'
    ? 'text-emerald-500'
    : step.status === 'failed'
    ? 'text-red-500'
    : step.status === 'running'
    ? 'text-violet-500'
    : step.status === 'skipped'
    ? 'text-gray-300'
    : 'text-gray-300';

  const StatusIcon = step.status === 'done'
    ? CheckCircle
    : step.status === 'failed'
    ? XCircle
    : step.status === 'running'
    ? Loader2
    : Clock;

  return (
    <div className="flex items-center gap-2.5 py-1.5">
      <Icon size={12} className={`flex-shrink-0 ${color}`} />
      <span className={`flex-1 text-[11px] ${step.status === 'pending' ? 'text-gray-300' : 'text-gray-600'}`}>{label}</span>
      <StatusIcon
        size={12}
        className={`flex-shrink-0 ${color} ${step.status === 'running' ? 'animate-spin' : ''}`}
      />
      {step.error && (
        <span className="text-[9px] text-red-400 max-w-[120px] truncate" title={step.error}>{step.error}</span>
      )}
    </div>
  );
}

function PublishJobCard({ jobId, adminToken, onComplete }) {
  const [job, setJob] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const pollingRef = useRef(null);
  const errorCountRef = useRef(0);
  const headers = { withCredentials: true };

  const poll = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/admin/content/publish-jobs/${jobId}`, headers);
      errorCountRef.current = 0;
      setJob(res.data);
      if (res.data?.status === 'done' || res.data?.status === 'failed' || res.data?.status === 'partial') {
        clearInterval(pollingRef.current);
        if (res.data?.status === 'done' && onComplete) onComplete(jobId);
      }
    } catch {
      // Allow up to 4 consecutive errors before giving up — prevents a
      // transient network blip from killing the job tracker permanently.
      errorCountRef.current += 1;
      if (errorCountRef.current >= 4) {
        clearInterval(pollingRef.current);
      }
    }
  }, [jobId]);

  useEffect(() => {
    poll();
    pollingRef.current = setInterval(poll, 2500);
    return () => clearInterval(pollingRef.current);
  }, [poll]);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await axios.post(`${API_BASE}/admin/content/publish-jobs/${jobId}/retry`, {}, headers);
      setJob(j => j ? { ...j, status: 'pending' } : j);
      pollingRef.current = setInterval(poll, 2500);
    } catch {
    } finally {
      setRetrying(false);
    }
  };

  if (!job) return (
    <div className="flex items-center gap-2 py-2 px-3 text-[11px] text-gray-400">
      <Loader2 size={11} className="animate-spin" />
      Loading job {jobId.slice(0, 8)}…
    </div>
  );

  const doneSteps = job.steps?.filter(s => s.status === 'done').length ?? 0;
  const totalSteps = job.steps?.length ?? 0;

  const statusColor = job.status === 'done'
    ? 'border-emerald-200 bg-emerald-50/60'
    : job.status === 'failed' || job.status === 'partial'
    ? 'border-red-200 bg-red-50/60'
    : 'border-violet-200 bg-violet-50/60';

  const statusLabel = job.status === 'done'
    ? '✓ Published'
    : job.status === 'failed'
    ? '✗ Failed'
    : job.status === 'partial'
    ? '⚠ Partially published'
    : job.status === 'running'
    ? `Running (${doneSteps}/${totalSteps})`
    : 'Queued…';

  return (
    <div className={`rounded-xl border ${statusColor} overflow-hidden`}>
      <div
        className="flex items-center gap-2.5 px-3 py-2 cursor-pointer select-none"
        onClick={() => setCollapsed(c => !c)}
      >
        {job.status === 'running' || job.status === 'pending'
          ? <Loader2 size={12} className="text-violet-500 animate-spin flex-shrink-0" />
          : job.status === 'done'
          ? <CheckCircle size={12} className="text-emerald-500 flex-shrink-0" />
          : <XCircle size={12} className="text-red-500 flex-shrink-0" />
        }
        <div className="flex-1 min-w-0">
          <span className="text-[11px] font-semibold text-gray-700 truncate block">
            {job.chapter_title || 'Chapter'} — {statusLabel}
          </span>
          <span className="text-[9px] text-gray-400 font-mono">{jobId.slice(0, 12)}…</span>
        </div>
        {(job.status === 'failed' || job.status === 'partial') && (
          <button
            onClick={(e) => { e.stopPropagation(); handleRetry(); }}
            disabled={retrying}
            className="flex items-center gap-1 h-6 px-2 rounded text-[10px] font-semibold bg-red-100 text-red-700 hover:bg-red-200 border border-red-200 transition-colors disabled:opacity-50"
          >
            {retrying ? <Loader2 size={9} className="animate-spin" /> : <RefreshCw size={9} />}
            Retry
          </button>
        )}
        {collapsed ? <ChevronDown size={11} className="text-gray-400 flex-shrink-0" /> : <ChevronUp size={11} className="text-gray-400 flex-shrink-0" />}
      </div>

      {!collapsed && job.steps?.length > 0 && (
        <div className="px-3 pb-2.5 border-t border-current/10 divide-y divide-gray-100">
          {job.steps.map(step => (
            <StepRow key={step.name} step={step} />
          ))}
          {job.error && (
            <div className="pt-1.5 text-[10px] text-red-500">{job.error}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function PublishJobsPanel({ publishJobIds = [], adminToken, onJobComplete }) {
  if (publishJobIds.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 space-y-2">
      <div className="px-3 py-1.5 rounded-lg bg-white border border-gray-200 shadow-sm">
        <p className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">
          Publish Jobs ({publishJobIds.length})
        </p>
      </div>
      <div className="space-y-1.5 max-h-96 overflow-y-auto">
        {publishJobIds.map(jobId => (
          <PublishJobCard
            key={jobId}
            jobId={jobId}
            adminToken={adminToken}
            onComplete={onJobComplete}
          />
        ))}
      </div>
    </div>
  );
}
