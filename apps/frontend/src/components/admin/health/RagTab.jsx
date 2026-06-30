import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { RefreshCw, CheckCircle, XCircle, AlertTriangle, ChevronDown, ChevronUp, Terminal } from 'lucide-react';
import { API_BASE } from '@/utils/api';
import { adminHeaders } from './shared';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';

const REQUIRED_INDEXES = ['subjectId', 'chapterId', 'topicId', 'medium', 'sourceType', 'chunkType'];

const DISABLE_CMD =
  'gcloud run services update syrabit-backend \\\n  --update-env-vars RAG_LEGACY_FALLBACK_ENABLED=false \\\n  --region asia-south1';

const ROLLBACK_CMD =
  'gcloud run services update syrabit-backend \\\n  --update-env-vars RAG_LEGACY_FALLBACK_ENABLED=true \\\n  --region asia-south1';

function CodeBlock({ code }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative rounded-lg bg-gray-900 text-gray-100 text-[11px] font-mono p-3 pr-10 overflow-x-auto">
      <pre className="whitespace-pre">{code}</pre>
      <button
        onClick={() => { navigator.clipboard.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }); }}
        className="absolute top-2 right-2 text-gray-400 hover:text-white transition-colors text-[10px]"
      >
        {copied ? '✓' : 'Copy'}
      </button>
    </div>
  );
}

function IndexPill({ name, present }) {
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border ${
      present
        ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
        : 'bg-red-50 border-red-200 text-red-700'
    }`}>
      {present
        ? <CheckCircle size={11} className="text-emerald-500 flex-shrink-0" />
        : <XCircle size={11} className="text-red-500 flex-shrink-0" />}
      {name}
    </div>
  );
}

function ChecklistRow({ done, label, sub }) {
  return (
    <div className="flex items-start gap-2.5 py-1.5">
      <div className={`mt-0.5 flex-shrink-0 w-4 h-4 rounded-full flex items-center justify-center border ${
        done ? 'bg-emerald-500 border-emerald-500' : 'bg-white border-gray-300'
      }`}>
        {done && <CheckCircle size={10} className="text-white" />}
      </div>
      <div>
        <p className={`text-xs font-medium ${done ? 'text-gray-700' : 'text-gray-500'}`}>{label}</p>
        {sub && <p className="text-[11px] text-gray-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

export default function RagTab({ adminToken }) {
  const [coverageData, setCoverageData] = useState(null);
  const [coverageLoading, setCoverageLoading] = useState(false);
  const [vectorizeData, setVectorizeData] = useState(null);
  const [vectorizeLoading, setVectorizeLoading] = useState(false);
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const [unindexedOpen, setUnindexedOpen] = useState(false);

  const loadCoverage = useCallback(() => {
    setCoverageLoading(true);
    axios.get(`${API_BASE}/admin/rag/coverage`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setCoverageData(r.data))
      .catch(() => setCoverageData(null))
      .finally(() => setCoverageLoading(false));
  }, [adminToken]);

  const loadVectorize = useCallback(() => {
    setVectorizeLoading(true);
    axios.get(`${API_BASE}/admin/rag/vectorize/info`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setVectorizeData(r.data))
      .catch(() => setVectorizeData(null))
      .finally(() => setVectorizeLoading(false));
  }, [adminToken]);

  useEffect(() => {
    loadCoverage();
    loadVectorize();
  }, [loadCoverage, loadVectorize]);

  const vHealth = vectorizeData?.health;
  const presentSet = new Set(vHealth?.present || []);
  const indexStatus = vHealth?.status || (vectorizeLoading ? 'loading' : 'unconfigured');

  const coverage = coverageData?.coverage_pct ?? null;
  const flagEnabled = coverageData?.flag_enabled;
  const readyToDisable = coverageData?.ready_to_disable === true;

  const indexesOk = indexStatus === 'ok';
  const coverageOk = coverage !== null && coverage >= 100.0;
  const allChecksPassed = indexesOk && coverageOk;

  return (
    <div className="space-y-4">
      {/* ── Vectorize Index Health ─────────────────────────────────────────── */}
      <SectionErrorBoundary name="Vectorize Index Health">
        <div className="rounded-2xl border border-gray-200 bg-white p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <p className="text-sm font-semibold text-gray-800">Vectorize Metadata Indexes</p>
              {!vectorizeLoading && (
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide ${
                  indexStatus === 'ok'           ? 'bg-emerald-100 text-emerald-700' :
                  indexStatus === 'degraded'     ? 'bg-red-100 text-red-700' :
                  indexStatus === 'unconfigured' ? 'bg-gray-100 text-gray-500' :
                                                   'bg-gray-100 text-gray-400'
                }`}>
                  {indexStatus}
                </span>
              )}
            </div>
            <button
              onClick={loadVectorize}
              disabled={vectorizeLoading}
              className="text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-40"
            >
              <RefreshCw size={13} className={vectorizeLoading ? 'animate-spin' : ''} />
            </button>
          </div>

          {vectorizeLoading ? (
            <div className="flex flex-wrap gap-2">
              {REQUIRED_INDEXES.map((n) => (
                <div key={n} className="h-7 w-24 rounded-full bg-gray-100 animate-pulse" />
              ))}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap gap-2">
                {REQUIRED_INDEXES.map((n) => (
                  <IndexPill key={n} name={n} present={presentSet.has(n)} />
                ))}
              </div>
              {vHealth?.summary && (
                <p className="text-[11px] text-gray-400 mt-2">{vHealth.summary}</p>
              )}
              {vectorizeData?.index_info && (
                <div className="mt-2 flex gap-3 text-[11px] text-gray-500">
                  <span>{vectorizeData.index_info.config?.dimensions ?? '—'} dimensions</span>
                  <span className="text-gray-300">|</span>
                  <span>{vectorizeData.index_info.config?.metric ?? '—'} metric</span>
                  {vectorizeData.index_info.vectorsCount !== undefined && (
                    <>
                      <span className="text-gray-300">|</span>
                      <span>{vectorizeData.index_info.vectorsCount?.toLocaleString()} vectors</span>
                    </>
                  )}
                </div>
              )}
              {vectorizeData?.index_info_error && (
                <p className="text-[11px] text-amber-600 mt-2 flex items-center gap-1">
                  <AlertTriangle size={11} /> CF API unreachable in dev — expected (no CF_ACCOUNT_ID)
                </p>
              )}
            </>
          )}
        </div>
      </SectionErrorBoundary>

      {/* ── V2 Reindex Coverage ───────────────────────────────────────────── */}
      <SectionErrorBoundary name="V2 Reindex Coverage">
        <div className="rounded-2xl border border-gray-200 bg-white p-4">
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm font-semibold text-gray-800">Chapter V2 Reindex Coverage</p>
            <button
              onClick={loadCoverage}
              disabled={coverageLoading}
              className="text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-40"
            >
              <RefreshCw size={13} className={coverageLoading ? 'animate-spin' : ''} />
            </button>
          </div>

          {coverageLoading ? (
            <div className="space-y-2">
              <div className="h-4 w-3/4 rounded bg-gray-100 animate-pulse" />
              <div className="h-3 w-32 rounded bg-gray-100 animate-pulse" />
            </div>
          ) : coverageData ? (
            <>
              <div className="flex items-center gap-3 mb-2">
                <div className="flex-1 h-3 rounded-full bg-gray-100 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      coverageOk ? 'bg-emerald-500' : coverage >= 80 ? 'bg-amber-400' : 'bg-red-400'
                    }`}
                    style={{ width: `${Math.min(coverage, 100)}%` }}
                  />
                </div>
                <span className={`text-sm font-bold font-mono ${
                  coverageOk ? 'text-emerald-600' : coverage >= 80 ? 'text-amber-600' : 'text-red-600'
                }`}>
                  {coverage}%
                </span>
              </div>
              <p className="text-[11px] text-gray-500">
                {coverageData.indexed_chapters} / {coverageData.total_chapters} chapters indexed on v2
              </p>
              {!coverageOk && coverageData.unindexed_chapter_ids?.length > 0 && (
                <div className="mt-2">
                  <button
                    onClick={() => setUnindexedOpen((v) => !v)}
                    className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 transition-colors"
                  >
                    {unindexedOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                    {coverageData.unindexed_chapter_ids.length} unindexed chapter IDs
                  </button>
                  {unindexedOpen && (
                    <div className="mt-1.5 max-h-32 overflow-y-auto rounded-lg bg-gray-50 border border-gray-200 p-2">
                      {coverageData.unindexed_chapter_ids.map((id) => (
                        <p key={id} className="text-[10px] font-mono text-gray-500">{id}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <p className="text-xs text-gray-400">Could not load coverage data</p>
          )}
        </div>
      </SectionErrorBoundary>

      {/* ── Flag Status + Disable Checklist ──────────────────────────────── */}
      <SectionErrorBoundary name="Flag Status">
        <div className={`rounded-2xl border p-4 ${
          flagEnabled === undefined ? 'border-gray-200 bg-white' :
          flagEnabled ? 'border-amber-200 bg-amber-50' : 'border-emerald-200 bg-emerald-50'
        }`}>
          <div className="flex items-start justify-between mb-3">
            <div>
              <p className="text-sm font-semibold text-gray-800">RAG_LEGACY_FALLBACK_ENABLED</p>
              <p className="text-[11px] text-gray-500 mt-0.5">
                Controls whether the legacy Atlas rag_chunks path is used when Vectorize returns nothing.
              </p>
            </div>
            {coverageData && (
              <span className={`flex-shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide ${
                flagEnabled ? 'bg-amber-200 text-amber-800' : 'bg-emerald-200 text-emerald-800'
              }`}>
                {flagEnabled ? 'ON' : 'OFF'}
              </span>
            )}
          </div>

          <div className="space-y-0.5 mb-3 border-t border-gray-200/60 pt-3">
            <ChecklistRow
              done={indexesOk}
              label="All 6 Vectorize metadata indexes present"
              sub={indexesOk ? undefined : `Missing: ${vHealth?.missing?.join(', ') || 'unknown — check CF creds'}`}
            />
            <ChecklistRow
              done={coverageOk}
              label="All chapters reindexed on v2 (coverage = 100%)"
              sub={!coverageOk && coverage !== null ? `Current: ${coverage}% — run bulk reindex for remaining chapters` : undefined}
            />
            <ChecklistRow
              done={!flagEnabled && flagEnabled !== undefined}
              label="Flag disabled on Cloud Run (RAG_LEGACY_FALLBACK_ENABLED=false)"
              sub={flagEnabled ? 'Use the command below to disable it' : undefined}
            />
          </div>

          {allChecksPassed && flagEnabled ? (
            <>
              <div className="flex items-center gap-1.5 mb-2 text-emerald-700">
                <CheckCircle size={13} />
                <p className="text-xs font-semibold">All preconditions met — safe to disable the flag</p>
              </div>
              <CodeBlock code={DISABLE_CMD} />
            </>
          ) : flagEnabled ? (
            <div className="flex items-center gap-1.5 text-amber-700">
              <AlertTriangle size={13} />
              <p className="text-xs font-medium">Keep flag ON — complete checklist above before disabling</p>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 text-emerald-700">
              <CheckCircle size={13} />
              <p className="text-xs font-medium">Flag is already OFF — legacy rag_chunks path is disabled</p>
            </div>
          )}
        </div>
      </SectionErrorBoundary>

      {/* ── Rollback Runbook ─────────────────────────────────────────────── */}
      <SectionErrorBoundary name="Rollback Runbook">
        <div className="rounded-2xl border border-gray-200 bg-white">
          <button
            onClick={() => setRollbackOpen((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-3 text-left"
          >
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
              <Terminal size={14} className="text-gray-400" />
              Rollback Runbook
            </div>
            {rollbackOpen ? <ChevronUp size={14} className="text-gray-400" /> : <ChevronDown size={14} className="text-gray-400" />}
          </button>

          {rollbackOpen && (
            <div className="px-4 pb-4 space-y-3 border-t border-gray-100">
              <p className="text-xs text-gray-500 pt-3">
                If a retrieval regression is detected after disabling the fallback (e.g. empty answers,
                "no context found" errors, or a drop in chat satisfaction scores), re-enable it immediately:
              </p>
              <CodeBlock code={ROLLBACK_CMD} />
              <div className="rounded-lg bg-gray-50 border border-gray-200 p-3 space-y-1.5 text-[11px] text-gray-600">
                <p className="font-semibold text-gray-700">Post-rollback steps</p>
                <p>1. Check retrieval logs: filter on <code className="bg-gray-200 px-1 rounded">path_used=empty</code> — that's the failure signal.</p>
                <p>2. Identify which chapters are producing empty results (look for <code className="bg-gray-200 px-1 rounded">chapterId</code> in the log context).</p>
                <p>3. Trigger a targeted reindex: <code className="bg-gray-200 px-1 rounded">POST /admin/rag/reindex/chapter/{'<id>'}</code>.</p>
                <p>4. Verify coverage reaches 100%, then attempt to disable the flag again.</p>
              </div>
              <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-[11px] text-amber-700">
                <p className="font-semibold mb-1">⚠ Verification window</p>
                <p>After disabling, monitor retrieval logs for at least 30 minutes covering both English and Assamese queries before declaring the migration complete.</p>
              </div>
            </div>
          )}
        </div>
      </SectionErrorBoundary>
    </div>
  );
}
