import { Globe, Eye, BarChart2, FileText, ExternalLink, Loader2,
  CheckCircle, AlertCircle, Link as LinkIcon } from 'lucide-react';
import { Card, Stat } from './shared';

export default function SeoPagesTab({
  data, vs, ga4Status, ga4Testing, ga4TestResult,
  handleGA4Connect, handleGA4Test, onNavigate,
}) {
  const hasTopPages  = data?.top_pages?.length > 0;
  const hasReferrers = data?.top_referrers?.length > 0;

  return (
    <>
      {onNavigate && (
        <div className="flex justify-end mb-3">
          <button
            onClick={() => onNavigate('seomanager')}
            className="flex items-center gap-1.5 h-8 px-4 rounded-xl text-xs font-semibold transition-all hover:opacity-80"
            style={{ background: 'rgba(6,182,212,0.12)', color: '#67e8f9', border: '1px solid rgba(6,182,212,0.2)' }}
          >
            <Globe size={12} /> Go to SEO Manager
          </button>
        </div>
      )}

      <div
        className="rounded-2xl p-5 relative overflow-hidden"
        style={{
          background: '#ffffff',
          border: '1px solid #e5e7eb',
          
        }}
      >
        <div className="absolute inset-0 pointer-events-none" style={{
          background: 'radial-gradient(ellipse at top left, rgba(66,133,244,0.04), transparent 60%)',
        }} />
        <div className="relative">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'rgba(66,133,244,0.15)' }}>
              <Globe size={14} className="text-blue-600" />
            </div>
            <div className="flex-1">
              <h3 className="text-gray-700 font-medium text-sm">Google Analytics 4</h3>
              <p className="text-gray-700 text-xs">GA4 connection status (visitor & page-view metrics come from Cloudflare)</p>
            </div>
            {ga4Status && (
              <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                ga4Status.connected ? 'text-emerald-600' : 'text-gray-600'
              }`} style={{
                background: ga4Status.connected ? 'rgba(16,185,129,0.12)' : '#f9fafb',
              }}>
                {ga4Status.connected
                  ? <><CheckCircle size={11} /> Connected</>
                  : <><AlertCircle size={11} /> Not connected</>}
              </div>
            )}
          </div>

          {ga4Status && !ga4Status.connected && (
            <div className="space-y-3">
              <p className="text-gray-600 text-sm">
                Visitor counts and page views are sourced from Cloudflare. Connecting GA4 here only enables connection-status checks for diagnostics — it does not power the headline metrics on this dashboard.
              </p>
              <div className="rounded-xl p-3 space-y-1.5 text-xs text-gray-600" style={{ background: '#f9fafb' }}>
                <p className="font-medium text-gray-500 mb-1">Setup steps:</p>
                <p>1. Add <code className="text-violet-700">GA4_REFRESH_TOKEN</code> secret after connecting below</p>
                <p>2. Your Property ID is already saved: <code className="text-emerald-700">{ga4Status.property_id || 'not set'}</code></p>
                <p>3. OAuth credentials: {ga4Status.client_id_set ? '✓ Client ID' : '✗ Client ID'} · {ga4Status.client_secret_set ? '✓ Secret' : '✗ Secret'}</p>
              </div>
              <button onClick={handleGA4Connect}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-white text-sm font-medium transition-all hover:opacity-90"
                style={{ background: 'linear-gradient(135deg, #3b82f6, #2563eb)', boxShadow: '0 2px 12px rgba(59,130,246,0.3)' }}>
                <LinkIcon size={13} /> Connect Google Analytics
              </button>
            </div>
          )}

          {ga4Status?.connected && (
            <div className="flex items-center gap-3 flex-wrap">
              <p className="text-gray-600 text-sm flex-1">Property <code className="text-emerald-700">{ga4Status.property_id}</code> · Data flows automatically</p>
              <button onClick={handleGA4Test} disabled={ga4Testing}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs text-gray-600 hover:text-gray-900 transition-all"
                style={{ background: '#f9fafb', border: '1px solid #e5e7eb' }}>
                {ga4Testing ? <Loader2 size={11} className="animate-spin" /> : <BarChart2 size={11} />} Test Connection
              </button>
            </div>
          )}

          {ga4TestResult && (
            <div className={`mt-3 p-3 rounded-xl text-xs ${ga4TestResult.ok ? 'text-emerald-700' : 'text-red-700'}`}
              style={{
                background: ga4TestResult.ok ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
                border: ga4TestResult.ok ? '1px solid rgba(16,185,129,0.15)' : '1px solid rgba(239,68,68,0.15)',
              }}>
              {ga4TestResult.ok
                ? `✓ GA4 connection healthy`
                : `✗ ${ga4TestResult.reason}`}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Stat icon={Eye}       label="Total Visitors"  value={data?.cf_connected ? (vs.total_visitors ?? 0).toLocaleString() : '—'} color="#8b5cf6" sub="Cloudflare" />
        <Stat icon={BarChart2} label="Pages Tracked"   value={hasTopPages ? data.top_pages.length : 0}  color="#06b6d4" />
        <Stat icon={Globe}     label="Traffic Sources" value={hasReferrers ? data.top_referrers.length : 0} color="#10b981" />
      </div>

      <Card title="Top Visited Pages" empty={!hasTopPages}
        emptyMsg={data?.cf_connected === false ? 'Cloudflare analytics unavailable — check API token and Zone ID' : 'No page visit data yet'}
        action={
          <a href="/api/seo/sitemap.xml" target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs text-violet-600 hover:underline">
            <ExternalLink size={11} /> Sitemap
          </a>
        }>
        <div className="space-y-1.5">
          {(data.top_pages || []).map((pg, i) => (
            <div key={i} className="flex items-center gap-2 px-2.5 py-2 rounded-xl transition-colors"
              style={{ background: i % 2 === 0 ? '#f9fafb' : 'transparent' }}>
              <span className="text-gray-700 text-xs w-5 text-right">{i + 1}</span>
              <FileText size={11} className="text-violet-600 flex-shrink-0" />
              <span className="text-gray-500 text-xs flex-1 truncate font-mono">{pg.path}</span>
              <span className="text-gray-700 text-xs flex-shrink-0">{(pg.views ?? 0).toLocaleString()} views</span>
              {pg.unique_visitors != null && (
                <span className="text-gray-700 text-xs flex-shrink-0">{pg.unique_visitors.toLocaleString()} uniq</span>
              )}
            </div>
          ))}
        </div>
      </Card>

      <Card title="Traffic Sources (Referrers)" empty={!hasReferrers}
        emptyMsg="Referrer breakdown is not available on the current Cloudflare plan (free tier)">
        <div className="space-y-2">
          {(data.top_referrers || []).map((ref, i) => (
            <div key={i} className="flex items-center gap-2">
              <Globe size={11} className="text-cyan-700 flex-shrink-0" />
              <span className="text-gray-500 text-sm flex-1 truncate">{ref.source || 'Direct'}</span>
              <span className="text-xs text-gray-700">{ref.count} visits</span>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
