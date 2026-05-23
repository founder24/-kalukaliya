import { Loader2, Zap } from 'lucide-react';

export default function SchemaTab({
  schemaSlug, setSchemaSlug, schemaLoading, schemaResult,
  handleSchemaInjectSingle, handleSchemaBulk, publishedCount,
}) {
  return (
    <div className="space-y-5">
      <div className="rounded-xl border p-5 space-y-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <div>
          <p className="text-sm font-semibold text-gray-900 mb-1">Inject Schema for Single Page</p>
          <p className="text-xs mb-3" style={{ color: '#9ca3af' }}>Add structured data (schema.org) to a specific page to improve rich snippet eligibility</p>
          <div className="flex gap-2">
            <input value={schemaSlug} onChange={e => setSchemaSlug(e.target.value)}
              placeholder="page-slug"
              className="flex-1 h-9 px-3 rounded-xl text-sm outline-none font-mono"
              style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', color: '#374151' }} />
            <button onClick={handleSchemaInjectSingle} disabled={schemaLoading || !schemaSlug.trim()}
              className="px-4 h-9 rounded-xl text-sm font-semibold disabled:opacity-40"
              style={{ background: '#0891b2', color: '#fff' }}>
              {schemaLoading ? <Loader2 size={14} className="animate-spin" /> : 'Inject'}
            </button>
          </div>
          {schemaResult && (
            <div className="mt-3 rounded-lg p-3 border text-xs font-mono overflow-x-auto" style={{ background: 'rgba(8,145,178,0.07)', borderColor: 'rgba(8,145,178,0.20)', color: '#67e8f9' }}>
              {JSON.stringify(schemaResult, null, 2).slice(0, 600)}
            </div>
          )}
        </div>
      </div>
      <div className="rounded-xl border p-5" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <p className="text-sm font-semibold text-gray-900 mb-1">Bulk Schema Injection</p>
        <p className="text-xs mb-4" style={{ color: '#9ca3af' }}>
          Auto-generate and inject schema.org JSON-LD markup into all {publishedCount} published pages. 
          Uses EducationalOrganization + Article schema types.
        </p>
        <button onClick={handleSchemaBulk} disabled={schemaLoading}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold disabled:opacity-40"
          style={{ background: 'linear-gradient(135deg,#0891b2,#06b6d4)', color: '#fff' }}>
          {schemaLoading ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
          Bulk Inject Schema ({publishedCount} pages)
        </button>
      </div>
    </div>
  );
}
