import { useState, useEffect } from 'react';
import { ListChecks, Loader2, Copy, ChevronDown, ChevronUp } from 'lucide-react';
import { toast } from 'sonner';
import { vertexMcqGenerator } from '@/utils/api';
import { card, btn, readHubCtx } from './shared';

export default function McqGeneratorCard({ token }) {
  const [text, setText] = useState('');
  const [subject, setSubject] = useState('');
  const [className, setClassName] = useState('Class 11');
  const [count, setCount] = useState(10);
  const [difficulty, setDifficulty] = useState('mixed');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [expanded, setExpanded] = useState(null);

  const hubCtx = readHubCtx();
  useEffect(() => {
    if (hubCtx?.subjectName) setSubject(hubCtx.subjectName);
    if (hubCtx?.className) setClassName(hubCtx.className);
  }, []);

  async function run() {
    if (!text.trim() || text.trim().length < 100) return toast.error('Paste at least 100 characters of chapter content');
    setLoading(true);
    setExpanded(null);
    try {
      const r = await vertexMcqGenerator(token, text, subject, className, count, difficulty);
      setResult(r.data);
      toast.success(`${r.data.total || r.data.mcqs?.length} MCQs generated`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Generation failed');
    } finally {
      setLoading(false);
    }
  }

  function copyAll() {
    if (!result?.mcqs) return;
    const out = result.mcqs.map((q, i) =>
      `${i + 1}. ${q.question}\nA) ${q.options?.A}  B) ${q.options?.B}  C) ${q.options?.C}  D) ${q.options?.D}\nAnswer: ${q.correct_answer}\n`
    ).join('\n');
    navigator.clipboard.writeText(out);
    toast.success('All MCQs copied!');
  }

  const mcqs = result?.mcqs || [];
  const diffColor = { easy: '#10b981', medium: '#f59e0b', hard: '#f97316', advanced: '#ef4444', mixed: '#8b5cf6' };

  return (
    <div style={card}>
      <div className="flex items-center gap-3 mb-4">
        <ListChecks size={18} color="#10b981" />
        <div>
          <div style={{ fontSize: 15, fontWeight: 800, color: '#111827' }}>MCQ Generator</div>
          <div style={{ fontSize: 12, color: '#6b7280' }}>AHSEC-pattern multiple choice questions from chapter text</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
        <input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject"
          style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }} />
        <select value={className} onChange={e => setClassName(e.target.value)}
          style={{ background: 'rgba(30,30,40,0.95)', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }}>
          {['Class 11', 'Class 12', 'Degree 1st Year', 'Degree 2nd Year', 'Degree 3rd Year'].map(c => <option key={c}>{c}</option>)}
        </select>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
        <select value={count} onChange={e => setCount(Number(e.target.value))}
          style={{ background: 'rgba(30,30,40,0.95)', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }}>
          {[5, 8, 10, 15, 20].map(n => <option key={n} value={n}>{n} questions</option>)}
        </select>
        <select value={difficulty} onChange={e => setDifficulty(e.target.value)}
          style={{ background: 'rgba(30,30,40,0.95)', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }}>
          {['mixed', 'easy', 'medium', 'hard'].map(d => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
        </select>
      </div>

      <textarea value={text} onChange={e => setText(e.target.value)} placeholder="Paste chapter content (min 100 characters)…"
        rows={4}
        style={{ width: '100%', background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '10px 12px', fontSize: 13, color: '#111827', outline: 'none', resize: 'vertical', marginBottom: 10, boxSizing: 'border-box' }}
      />

      <div className="flex gap-2 mb-4">
        <button onClick={run} disabled={loading} style={{ ...btn('#10b981'), flex: 1, justifyContent: 'center' }}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : <ListChecks size={14} />}
          {loading ? 'Generating MCQs…' : 'Generate MCQs'}
        </button>
        {mcqs.length > 0 && (
          <button onClick={copyAll} style={{ ...btn('#06b6d4'), padding: '8px 14px' }}>
            <Copy size={13} /> Copy All
          </button>
        )}
      </div>

      {mcqs.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="flex items-center gap-2 mb-1">
            <span style={{ fontSize: 12, color: '#6b7280' }}>{mcqs.length} questions generated</span>
            <span style={{ background: `${diffColor[difficulty]}18`, color: diffColor[difficulty], border: `1px solid ${diffColor[difficulty]}33`, borderRadius: 20, padding: '1px 8px', fontSize: 11, fontWeight: 700 }}>
              {difficulty.toUpperCase()}
            </span>
          </div>

          {mcqs.map((q, i) => (
            <div key={i} style={{ background: '#f9fafb', border: `1px solid ${expanded === i ? 'rgba(16,185,129,0.35)' : '#e5e7eb'}`, borderRadius: 12, padding: 14, cursor: 'pointer' }}
              onClick={() => setExpanded(expanded === i ? null : i)}>
              <div className="flex items-start justify-between gap-2">
                <div style={{ fontSize: 13, color: '#111827', lineHeight: 1.5, flex: 1 }}>
                  <span style={{ color: '#34d399', fontWeight: 700 }}>Q{i + 1}.</span> {q.question}
                </div>
                {expanded === i ? <ChevronUp size={14} color="#9ca3af" /> : <ChevronDown size={14} color="#9ca3af" />}
              </div>
              {expanded === i && (
                <div style={{ marginTop: 10 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
                    {['A', 'B', 'C', 'D'].map(opt => (
                      <div key={opt} style={{
                        background: q.correct_answer === opt ? 'rgba(16,185,129,0.12)' : '#f9fafb',
                        border: `1px solid ${q.correct_answer === opt ? 'rgba(16,185,129,0.35)' : '#e5e7eb'}`,
                        borderRadius: 8, padding: '6px 10px', fontSize: 12,
                        color: q.correct_answer === opt ? '#34d399' : '#4b5563',
                        fontWeight: q.correct_answer === opt ? 700 : 400,
                      }}>
                        <span style={{ fontWeight: 700 }}>{opt})</span> {q.options?.[opt]}
                      </div>
                    ))}
                  </div>
                  {q.explanation && (
                    <div style={{ fontSize: 12, color: '#6b7280', background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)', borderRadius: 8, padding: '8px 10px', lineHeight: 1.6 }}>
                      <strong style={{ color: '#34d399' }}>Explanation:</strong> {q.explanation}
                    </div>
                  )}
                  <div className="flex gap-2 mt-2 flex-wrap">
                    {q.topic && <span style={{ fontSize: 10, color: '#94a3b8', background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 20, padding: '1px 8px' }}>{q.topic}</span>}
                    {q.difficulty && <span style={{ fontSize: 10, color: diffColor[q.difficulty] || '#8b5cf6', background: `${diffColor[q.difficulty] || '#8b5cf6'}18`, border: `1px solid ${diffColor[q.difficulty] || '#8b5cf6'}33`, borderRadius: 20, padding: '1px 8px' }}>{q.difficulty}</span>}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
