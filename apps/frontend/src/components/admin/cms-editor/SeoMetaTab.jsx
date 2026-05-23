import {
  Loader2, Globe, Sparkles, Zap, Link2, Copy, CheckCircle,
} from 'lucide-react';
import { toast } from 'sonner';

export default function SeoMetaTab({
  form, setForm, editDoc,
  seoGenerating, handleGenerateSeoMeta,
  seoResult, setSeoResult, applySeoResult,
  handleAutoKeyword,
}) {
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl mx-auto space-y-6">

        <div className="rounded-xl p-4 border" style={{ background: 'rgba(139,92,246,0.06)', borderColor: 'rgba(139,92,246,0.20)' }}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Sparkles size={13} style={{ color: '#a78bfa' }} />
              <span className="text-xs font-semibold" style={{ color: '#7c3aed' }}>AI SEO &amp; GEO Generator</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: 'rgba(139,92,246,0.18)', color: '#a78bfa' }}>Beta</span>
            </div>
            <button
              onClick={handleGenerateSeoMeta}
              disabled={seoGenerating}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-opacity disabled:opacity-50"
              style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)', color: '#fff' }}>
              {seoGenerating
                ? <><Loader2 size={11} className="animate-spin" /> Generating…</>
                : <><Zap size={11} /> Generate Title + Meta</>}
            </button>
          </div>
          <p className="text-[11px] leading-relaxed" style={{ color: '#9ca3af' }}>
            Generates a 55–65 char SEO title + 148–158 char GEO-rich meta description optimised for Google ranking and AI citation (Perplexity, ChatGPT search). Uses your current title, keyword, content, and linked syllabus scope as context.
          </p>

          {seoResult && (
            <div className="mt-4 space-y-3 pt-4 border-t" style={{ borderColor: 'rgba(139,92,246,0.18)' }}>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: '#9ca3af' }}>SEO Title</span>
                  <span className="text-[10px]" style={{ color: seoResult.char_counts?.title > 65 ? '#dc2626' : '#16a34a' }}>
                    {seoResult.char_counts?.title || seoResult.seo_title?.length || 0} / 65 chars
                  </span>
                </div>
                <p className="text-sm font-medium px-3 py-2 rounded-lg" style={{ background: '#f3f4f6', color: '#111827' }}>
                  {seoResult.seo_title}
                </p>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: '#9ca3af' }}>Meta Description</span>
                  <span className="text-[10px]" style={{ color: seoResult.char_counts?.meta > 160 ? '#dc2626' : seoResult.char_counts?.meta >= 140 ? '#16a34a' : '#f59e0b' }}>
                    {seoResult.char_counts?.meta || seoResult.meta_description?.length || 0} / 160 chars
                  </span>
                </div>
                <p className="text-xs leading-relaxed px-3 py-2 rounded-lg" style={{ background: '#f3f4f6', color: '#374151' }}>
                  {seoResult.meta_description}
                </p>
              </div>
              <div>
                <span className="text-[10px] font-semibold uppercase tracking-wide block mb-1" style={{ color: '#9ca3af' }}>Primary Keyword</span>
                <p className="text-xs font-mono px-3 py-1.5 rounded-lg" style={{ background: '#f3f4f6', color: '#a78bfa' }}>
                  {seoResult.primary_keyword}
                </p>
              </div>
              {seoResult.geo_phrases?.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold uppercase tracking-wide block mb-1.5" style={{ color: '#9ca3af' }}>GEO Authority Phrases</span>
                  <div className="flex flex-wrap gap-2">
                    {seoResult.geo_phrases.map((p, i) => (
                      <span key={i} className="text-[10px] px-2 py-1 rounded-lg" style={{ background: 'rgba(16,185,129,0.10)', color: '#34d399', border: '1px solid rgba(16,185,129,0.18)' }}>
                        {p}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {seoResult.seo_tags && (
                <div>
                  <span className="text-[10px] font-semibold uppercase tracking-wide block mb-1.5" style={{ color: '#9ca3af' }}>SEO Tags</span>
                  <div className="flex flex-wrap gap-1.5">
                    {seoResult.seo_tags.split(',').map(t => t.trim()).filter(Boolean).map((t, i) => (
                      <span key={i} className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: '#e5e7eb', color: '#6b7280' }}>
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex gap-2 pt-1">
                <button onClick={applySeoResult}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold"
                  style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)', color: '#fff' }}>
                  <CheckCircle size={12} /> Apply All to Page
                </button>
                <button onClick={() => setSeoResult(null)}
                  className="px-3 py-2 rounded-lg text-xs"
                  style={{ background: '#f3f4f6', color: '#6b7280' }}>
                  Dismiss
                </button>
              </div>
            </div>
          )}
        </div>

        <div>
          <p className="text-xs font-medium mb-2" style={{ color: '#6b7280' }}>Google Search Preview</p>
          <div className="rounded-xl p-4" style={{ background: '#ffffff' }}>
            <div className="flex items-center gap-2 mb-1.5">
              <div className="w-5 h-5 rounded-full flex-shrink-0" style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)' }} />
              <div className="min-w-0">
                <p className="text-xs font-medium truncate" style={{ color: '#202124' }}>syrabit.ai</p>
                <p className="text-[10px] truncate" style={{ color: '#4d5156' }}>https://syrabit.ai/{form.seo_slug || 'your-slug-here'}</p>
              </div>
            </div>
            <p className="text-base leading-tight mb-1" style={{ color: '#1a0dab', fontFamily: 'arial,sans-serif' }}>
              {form.title ? `${form.title} | Syrabit.ai` : 'Your Page Title — Syrabit.ai'}
            </p>
            <p className="text-sm leading-snug" style={{ color: '#4d5156', fontFamily: 'arial,sans-serif' }}>
              {form.meta_description
                ? (form.meta_description.length > 160 ? form.meta_description.slice(0, 157) + '…' : form.meta_description)
                : 'Your meta description will appear here. Write 120–160 characters to maximise click-through.'}
            </p>
            {form.meta_description && (
              <div className="mt-2 flex items-center gap-2">
                <div className="flex-1 h-1 rounded-full" style={{ background: '#e5e7eb' }}>
                  <div className="h-1 rounded-full transition-all" style={{
                    width: `${Math.min(100, (form.meta_description.length / 160) * 100)}%`,
                    background: form.meta_description.length > 160 ? '#dc2626' : form.meta_description.length > 110 ? '#16a34a' : '#f59e0b',
                  }} />
                </div>
                <span className="text-[10px] flex-shrink-0" style={{ color: form.meta_description.length > 160 ? '#dc2626' : '#6b7280' }}>
                  {form.meta_description.length}/160
                </span>
              </div>
            )}
          </div>
        </div>

        <div>
          <p className="text-xs font-medium mb-2" style={{ color: '#6b7280' }}>Perplexity AI Citation Preview</p>
          <div className="rounded-xl p-4" style={{ background: '#0d1117', border: '1px solid rgba(139,92,246,0.25)' }}>
            <div className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5" style={{ background: 'linear-gradient(135deg,#6366f1,#8b5cf6)' }}>
                <Sparkles size={11} className="text-white" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold mb-1" style={{ color: '#e2e8f0' }}>
                  {form.primary_keyword
                    ? `${form.primary_keyword} — Assamboard Study Guide`
                    : form.title || 'Your document title will appear here as the AI answer heading'}
                </p>
                <p className="text-[11px] leading-relaxed mb-2" style={{ color: '#94a3b8' }}>
                  {form.meta_description
                    ? form.meta_description.slice(0, 180)
                    : 'Your meta description appears here as the AI-generated answer excerpt. Perplexity cites pages with clear educational intent and board-aligned content.'}
                </p>
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg text-[10px]" style={{ background: 'rgba(139,92,246,0.15)', color: '#a78bfa' }}>
                    <Globe size={9} />
                    syrabit.ai/{form.seo_slug || 'slug'}
                  </div>
                  {form.seo_tags && form.seo_tags.split(',').slice(0, 3).map(tag => (
                    <span key={tag} className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: '#f3f4f6', color: '#9ca3af' }}>
                      {tag.trim()}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex-shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ background: 'rgba(139,92,246,0.20)', color: '#a78bfa' }}>
                [1]
              </div>
            </div>
          </div>
        </div>

        {(editDoc?.canonical_url || editDoc?.linked_scope) && (
          <div className="p-3 rounded-xl flex items-center gap-3" style={{ background: 'rgba(16,185,129,0.07)', border: '1px solid rgba(16,185,129,0.18)' }}>
            <Link2 size={14} style={{ color: '#34d399', flexShrink: 0 }} />
            <div className="flex-1 min-w-0">
              <p className="text-[10px] font-medium mb-0.5" style={{ color: '#34d399' }}>Canonical URL</p>
              <p className="text-xs font-mono truncate" style={{ color: '#6b7280' }}>
                {'<link rel="canonical" href="' + (editDoc.canonical_url || `/${editDoc.linked_scope?.replace(/\//g, '/')}`) + '" />'}
              </p>
            </div>
            <button onClick={() => { navigator.clipboard.writeText(editDoc.canonical_url || ''); toast.success('Copied'); }} style={{ color: '#9ca3af' }}>
              <Copy size={12} />
            </button>
          </div>
        )}

        <div>
          <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>URL Slug</label>
          <div className="flex items-center gap-2 h-10 rounded-xl overflow-hidden px-3" style={{ background: '#f3f4f6', border: '1px solid #e5e7eb' }}>
            <Link2 size={13} style={{ color: '#9ca3af' }} className="flex-shrink-0" />
            <span className="text-sm" style={{ color: '#d1d5db' }}>/learn/</span>
            <input
              value={form.seo_slug}
              onChange={e => setForm(f => ({ ...f, seo_slug: e.target.value }))}
              placeholder="auto-from-title"
              className="flex-1 h-full text-sm bg-transparent outline-none font-mono"
              style={{ color: '#374151' }}
            />
          </div>
        </div>

        <div>
          <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>
            Meta Description <span style={{ color: '#d1d5db' }}>({form.meta_description?.length || 0}/160)</span>
          </label>
          <textarea
            value={form.meta_description}
            onChange={e => setForm(f => ({ ...f, meta_description: e.target.value.slice(0, 160) }))}
            placeholder="160-character description for Google snippets…"
            rows={3}
            className="w-full px-4 py-2.5 rounded-xl text-sm outline-none resize-none"
            style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
          />
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs" style={{ color: '#6b7280' }}>Primary Keyword</label>
            <button onClick={handleAutoKeyword}
              className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-lg border"
              style={{ color: '#a78bfa', borderColor: 'rgba(167,139,250,0.25)', background: 'rgba(167,139,250,0.08)' }}>
              <Zap size={9} /> Auto-fill
            </button>
          </div>
          <input
            value={form.primary_keyword}
            onChange={e => setForm(f => ({ ...f, primary_keyword: e.target.value }))}
            placeholder="e.g. Assamboard Class 12 Physics Notes"
            className="w-full h-10 px-4 rounded-xl text-sm outline-none"
            style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
          />
        </div>

        <div>
          <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>SEO Tags <span style={{ color: '#d1d5db' }}>(comma-separated)</span></label>
          <input
            value={form.seo_tags}
            onChange={e => setForm(f => ({ ...f, seo_tags: e.target.value }))}
            placeholder="assamboard, class 12, physics, optics, notes"
            className="w-full h-10 px-4 rounded-xl text-sm outline-none"
            style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
          />
        </div>

        <div>
          <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>Category Path</label>
          <input
            value={form.category}
            onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
            placeholder="ahsec/class12/science/physics"
            className="w-full h-10 px-4 rounded-xl text-sm font-mono outline-none"
            style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
          />
        </div>

        <div>
          <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>Schema Type</label>
          <div className="flex gap-2 flex-wrap">
            {['Article', 'FAQPage', 'HowTo', 'EducationalOccupationalProgram'].map(s => (
              <button key={s} onClick={() => setForm(f => ({ ...f, schema_type: s }))}
                className="px-3 py-1.5 rounded-lg text-xs font-medium border transition-all"
                style={form.schema_type === s
                  ? { borderColor: '#7c3aed', background: 'rgba(149,117,224,0.18)', color: '#7c3aed' }
                  : { borderColor: '#e5e7eb', background: '#f9fafb', color: '#6b7280' }}>
                {s}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>Long Description</label>
          <textarea
            value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="Optional extended description…"
            rows={4}
            className="w-full px-4 py-2.5 rounded-xl text-sm outline-none resize-none"
            style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>Thumbnail URL</label>
            <input
              value={form.thumbnail_url}
              onChange={e => setForm(f => ({ ...f, thumbnail_url: e.target.value }))}
              placeholder="https://…"
              className="w-full h-10 px-4 rounded-xl text-sm outline-none"
              style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
            />
          </div>
          <div>
            <label className="text-xs block mb-1.5" style={{ color: '#6b7280' }}>Alt Text</label>
            <input
              value={form.alt_text}
              onChange={e => setForm(f => ({ ...f, alt_text: e.target.value }))}
              placeholder="Image alt text"
              className="w-full h-10 px-4 rounded-xl text-sm outline-none"
              style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
