import { useState, useEffect } from 'react';
import { TrendingUp, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { vertexSeoMeta } from '@/utils/api';
import { card, btn, Badge, readHubCtx } from './shared';

export default function SeoMetaCard({ token }) {
  const hubCtx = readHubCtx();
  const [form, setForm] = useState({
    topic:           '',
    subject:         '',
    class_name:      'Class 11',
    page_type:       'notes',
    board:           'AHSEC',
    content_preview: '',
  });

  useEffect(() => {
    const ctx = readHubCtx();
    if (!ctx) return;
    setForm(f => ({
      ...f,
      subject:    ctx.subjectName || f.subject,
      class_name: ctx.className   || f.class_name,
    }));
  }, []);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!form.topic) return;
    setLoading(true);
    try {
      const r = await vertexSeoMeta(token, form);
      setResult(r.data);
      toast.success('SEO metadata generated');
    } catch {
      toast.error('SEO meta generation failed');
    } finally { setLoading(false); }
  }

  const inp = { background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '7px 12px', color: '#111827', fontSize: 13 };

  return (
    <div style={card}>
      <div className="flex items-center gap-2 mb-4">
        <TrendingUp size={16} color="#06b6d4" />
        <span style={{ fontWeight: 700, color: '#111827' }}>SEO Meta Generator</span>
        <Badge label="Structured Output" color="#06b6d4" />
      </div>
      <p style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>
        Generate title (60 chars), meta description (160 chars), keywords, OG tags — all optimised for AssamBoard search intent.
      </p>
      <div className="grid grid-cols-2 gap-2 mb-2">
        <input value={form.topic} onChange={e => setForm(f => ({ ...f, topic: e.target.value }))} placeholder="Topic *" style={inp} />
        <input value={form.subject} onChange={e => setForm(f => ({ ...f, subject: e.target.value }))} placeholder="Subject" style={inp} />
        <select value={form.class_name} onChange={e => setForm(f => ({ ...f, class_name: e.target.value }))} style={inp}>
          {['Class 11', 'Class 12', 'Degree 1st Year'].map(c => <option key={c}>{c}</option>)}
        </select>
        <select value={form.page_type} onChange={e => setForm(f => ({ ...f, page_type: e.target.value }))} style={inp}>
          {['notes', 'mcqs', 'definition', 'important-questions', 'examples', 'syllabus'].map(t => <option key={t}>{t}</option>)}
        </select>
      </div>
      <textarea value={form.content_preview} onChange={e => setForm(f => ({ ...f, content_preview: e.target.value }))} rows={2}
        placeholder="Optional: paste first 200 chars of content for better meta..."
        style={{ ...inp, width: '100%', resize: 'none', marginBottom: 10, fontFamily: 'inherit' }}
      />
      <button onClick={run} disabled={loading || !form.topic} style={btn('#06b6d4')}>
        {loading ? <Loader2 size={13} className="animate-spin" /> : <TrendingUp size={13} />}
        Generate Meta
      </button>
      {result && (
        <div style={{ marginTop: 14, background: 'rgba(6,182,212,0.05)', border: '1px solid rgba(6,182,212,0.2)', borderRadius: 10, padding: 16 }}>
          {[['title', 'Title', '#06b6d4'], ['meta_description', 'Meta Description', '#10b981'], ['og_title', 'OG Title', '#a855f7'], ['og_description', 'OG Description', '#f59e0b']].map(([key, label, color]) => (
            result[key] && (
              <div key={key} style={{ marginBottom: 10 }}>
                <div className="flex items-center justify-between mb-1">
                  <span style={{ fontSize: 10, fontWeight: 700, color, textTransform: 'uppercase' }}>{label}</span>
                  <span style={{ fontSize: 10, color: '#9ca3af' }}>{result[key].length} chars</span>
                </div>
                <div style={{ fontSize: 13, color: '#111827', background: '#f9fafb', borderRadius: 8, padding: '8px 10px' }}>
                  {result[key]}
                </div>
              </div>
            )
          ))}
          {result.keywords?.length > 0 && (
            <div>
              <div style={{ fontSize: 10, fontWeight: 700, color: '#8b5cf6', textTransform: 'uppercase', marginBottom: 6 }}>Keywords</div>
              <div className="flex flex-wrap gap-1">
                {result.keywords.map((kw, i) => (
                  <span key={i} style={{ background: 'rgba(139,92,246,0.12)', border: '1px solid rgba(139,92,246,0.25)', color: '#a78bfa', borderRadius: 20, padding: '2px 8px', fontSize: 11 }}>{kw}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
