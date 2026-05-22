import { useState, useEffect } from 'react';
import { Brain, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { vertexNlpConcepts } from '@/utils/api';
import { card, btn, readHubCtx } from './shared';

export default function NlpConceptsCard({ token }) {
  const [text, setText] = useState('');
  const [subject, setSubject] = useState('');
  const [className, setClassName] = useState('Class 11');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const hubCtx = readHubCtx();

  useEffect(() => {
    if (hubCtx?.subjectName) setSubject(hubCtx.subjectName);
    if (hubCtx?.className) setClassName(hubCtx.className);
  }, []);

  async function run() {
    if (!text.trim() || text.trim().length < 50) return toast.error('Paste at least 50 characters of chapter content');
    setLoading(true);
    try {
      const r = await vertexNlpConcepts(token, text, subject, className);
      setResult(r.data);
      toast.success('NLP analysis complete');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Analysis failed');
    } finally {
      setLoading(false);
    }
  }

  const diffColor = { easy: '#10b981', medium: '#f59e0b', hard: '#f97316', advanced: '#ef4444' };

  return (
    <div style={card}>
      <div className="flex items-center gap-3 mb-4">
        <Brain size={18} color="#a855f7" />
        <div>
          <div style={{ fontSize: 15, fontWeight: 800, color: '#111827' }}>NLP Key Concepts</div>
          <div style={{ fontSize: 12, color: '#6b7280' }}>Cloud Natural Language API · Extract entities, terms &amp; exam weightage</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
        <input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject (e.g. Physics)"
          style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }} />
        <select value={className} onChange={e => setClassName(e.target.value)}
          style={{ background: 'rgba(30,30,40,0.95)', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }}>
          {['Class 11', 'Class 12', 'Degree 1st Year', 'Degree 2nd Year', 'Degree 3rd Year'].map(c => <option key={c}>{c}</option>)}
        </select>
      </div>

      <textarea value={text} onChange={e => setText(e.target.value)} placeholder="Paste chapter content here (min 50 characters)…"
        rows={5}
        style={{ width: '100%', background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '10px 12px', fontSize: 13, color: '#111827', outline: 'none', resize: 'vertical', marginBottom: 10, boxSizing: 'border-box' }}
      />

      <button onClick={run} disabled={loading} style={{ ...btn('#a855f7'), width: '100%', justifyContent: 'center', marginBottom: 14 }}>
        {loading ? <Loader2 size={14} className="animate-spin" /> : <Brain size={14} />}
        {loading ? 'Analysing…' : 'Extract Key Concepts'}
      </button>

      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {result.difficulty_level && (
              <span style={{ background: `${diffColor[result.difficulty_level] || '#a855f7'}22`, color: diffColor[result.difficulty_level] || '#a855f7', border: `1px solid ${diffColor[result.difficulty_level] || '#a855f7'}44`, borderRadius: 20, padding: '2px 10px', fontSize: 11, fontWeight: 700 }}>
                {result.difficulty_level?.toUpperCase()} DIFFICULTY
              </span>
            )}
            {result.exam_weightage && (
              <span style={{ background: 'rgba(249,115,22,0.12)', color: '#fb923c', border: '1px solid rgba(249,115,22,0.25)', borderRadius: 20, padding: '2px 10px', fontSize: 11, fontWeight: 700 }}>
                {result.exam_weightage?.toUpperCase()} EXAM WEIGHT
              </span>
            )}
          </div>

          {result.chapter_summary && (
            <div style={{ background: 'rgba(168,85,247,0.06)', border: '1px solid rgba(168,85,247,0.2)', borderRadius: 10, padding: 12, fontSize: 13, color: '#374151', lineHeight: 1.7 }}>
              {result.chapter_summary}
            </div>
          )}

          {result.key_terms?.length > 0 && (
            <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', marginBottom: 8 }}>Key Terms ({result.key_terms.length})</div>
              {result.key_terms.map((t, i) => (
                <div key={i} style={{ borderBottom: '1px solid #f3f4f6', padding: '6px 0' }}>
                  <span style={{ fontSize: 13, color: '#111827', fontWeight: 700 }}>{t.term}</span>
                  <span style={{ fontSize: 11, marginLeft: 8, color: t.importance === 'high' ? '#10b981' : t.importance === 'medium' ? '#f59e0b' : '#9ca3af' }}>
                    [{t.importance}]
                  </span>
                  {t.definition && <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2, lineHeight: 1.5 }}>{t.definition}</div>}
                </div>
              ))}
            </div>
          )}

          {result.prerequisite_topics?.length > 0 && (
            <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', marginBottom: 6 }}>Prerequisites</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {result.prerequisite_topics.map((t, i) => (
                  <span key={i} style={{ background: 'rgba(168,85,247,0.12)', color: '#c084fc', border: '1px solid rgba(168,85,247,0.25)', borderRadius: 20, padding: '2px 10px', fontSize: 11 }}>{t}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
