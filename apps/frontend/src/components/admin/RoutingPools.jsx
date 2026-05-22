import { useEffect, useState } from 'react';
import { Loader2, Layers, AlertCircle } from 'lucide-react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';

const adminHeaders = (token) => {
  const isRealJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isRealJwt ? { Authorization: `Bearer ${token}` } : {};
};

const FEATURE_LABELS = {
  english_rag_chat: 'English chat (RAG)',
  assamese_rag_chat: 'Assamese chat (RAG)',
  content: 'Long-form content',
  assamese_content: 'Assamese content',
  tts: 'Text-to-speech',
  stt: 'Speech-to-text',
  voice: 'Voice (combined)',
  embed: 'Embeddings',
  rerank: 'Reranking',
  vector_search: 'Vector search',
  translate: 'Translation (en→as)',
  vision: 'Vision / OCR',
  safety: 'Safety checks',
  search_rag: 'RAG web search',
  live_search: 'Live web search',
};

const ROLE_BADGE = {
  primary: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  secondary: 'bg-violet-50 text-violet-700 border-violet-200',
  fallback_only: 'bg-gray-50 text-gray-500 border-gray-200',
};

export default function RoutingPools({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    axios
      .get(`${API_BASE}/admin/routing-config`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
      })
      .then((res) => { if (alive) setData(res.data); })
      .catch((e) => { if (alive) setErr(e.response?.data?.detail || e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [adminToken]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-gray-400 text-sm py-8">
        <Loader2 size={16} className="animate-spin" /> Loading routing configuration…
      </div>
    );
  }
  if (err) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-600 flex items-center gap-2">
        <AlertCircle size={14} /> Failed to load routing config: {err}
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="routing-pools">
      <div className="flex items-center gap-2">
        <Layers size={16} className="text-violet-600" />
        <h3 className="text-sm font-bold text-gray-900">Routing & Pools</h3>
        <span className="text-xs text-gray-400">live from <code className="font-mono">/admin/routing-config</code></span>
      </div>
      <p className="text-xs text-gray-500">
        Snapshot of <code className="font-mono">PROVIDER_PRIORITY</code> × <code className="font-mono">POOL_WEIGHTS</code>.
        Strict-primary pools (10× weight gap) lock the primary at 100% draw.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {data.pools.map((pool) => (
          <div key={pool.feature} className="rounded-2xl border border-gray-200 bg-white p-3 shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-semibold text-gray-900">{FEATURE_LABELS[pool.feature] || pool.feature}</p>
              {pool.strict_primary_lock && (
                <span className="text-[10px] font-bold uppercase tracking-wide text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5">
                  STRICT PRIMARY
                </span>
              )}
            </div>
            <div className="space-y-1.5">
              {pool.providers.map((p) => (
                <div key={p.name} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-gray-700">{p.name}</span>
                  <div className="flex items-center gap-2">
                    <span className={`px-1.5 py-0.5 rounded-full border text-[10px] font-medium ${ROLE_BADGE[p.role]}`}>
                      {p.role}
                    </span>
                    <span className="font-mono text-gray-500 w-16 text-right">{p.share_pct.toFixed(1)}%</span>
                    <span className="font-mono text-gray-400 w-14 text-right">w={p.weight}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <details className="rounded-xl border border-gray-200 bg-gray-50 p-3 text-xs">
        <summary className="cursor-pointer font-semibold text-gray-700">Raw credits / pool weights</summary>
        <pre className="mt-2 text-[11px] font-mono text-gray-600 overflow-x-auto">{JSON.stringify({ credits: data.credits, pool_weights: data.pool_weights }, null, 2)}</pre>
      </details>
    </div>
  );
}
