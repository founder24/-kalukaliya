import { useState, useEffect } from 'react';
import { CreditCard, Loader2, Copy } from 'lucide-react';
import { toast } from 'sonner';
import { vertexFlashcards } from '@/utils/api';
import { card, btn, readHubCtx } from './shared';

export default function FlashcardGeneratorCard({ token }) {
  const [text, setText] = useState('');
  const [subject, setSubject] = useState('');
  const [className, setClassName] = useState('Class 11');
  const [count, setCount] = useState(10);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [flipped, setFlipped] = useState({});
  const [activeIdx, setActiveIdx] = useState(0);

  const hubCtx = readHubCtx();
  useEffect(() => {
    if (hubCtx?.subjectName) setSubject(hubCtx.subjectName);
    if (hubCtx?.className) setClassName(hubCtx.className);
  }, []);

  async function run() {
    if (!text.trim() || text.trim().length < 100) return toast.error('Paste at least 100 characters of chapter content');
    setLoading(true);
    setFlipped({});
    setActiveIdx(0);
    try {
      const r = await vertexFlashcards(token, text, subject, className, count);
      setResult(r.data);
      toast.success(`${r.data.total_cards || r.data.flashcards?.length} flashcards generated`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Generation failed');
    } finally {
      setLoading(false);
    }
  }

  function copyAll() {
    if (!result?.flashcards) return;
    const out = result.flashcards.map((c, i) => `${i + 1}. Q: ${c.front}\n   A: ${c.back}`).join('\n\n');
    navigator.clipboard.writeText(out);
    toast.success('All flashcards copied!');
  }

  const cards = result?.flashcards || [];
  const current = cards[activeIdx];
  const diffColor = { easy: '#10b981', medium: '#f59e0b', hard: '#ef4444' };

  return (
    <div style={card}>
      <div className="flex items-center gap-3 mb-4">
        <CreditCard size={18} color="#06b6d4" />
        <div>
          <div style={{ fontSize: 15, fontWeight: 800, color: '#111827' }}>Flashcard Generator</div>
          <div style={{ fontSize: 12, color: '#6b7280' }}>AI-powered Q&amp;A flashcards from chapter content for student revision</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px', gap: 8, marginBottom: 10 }}>
        <input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject"
          style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }} />
        <select value={className} onChange={e => setClassName(e.target.value)}
          style={{ background: 'rgba(30,30,40,0.95)', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }}>
          {['Class 11', 'Class 12', 'Degree 1st Year', 'Degree 2nd Year', 'Degree 3rd Year'].map(c => <option key={c}>{c}</option>)}
        </select>
        <select value={count} onChange={e => setCount(Number(e.target.value))}
          style={{ background: 'rgba(30,30,40,0.95)', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: '#111827', outline: 'none' }}>
          {[5, 8, 10, 15, 20].map(n => <option key={n} value={n}>{n} cards</option>)}
        </select>
      </div>

      <textarea value={text} onChange={e => setText(e.target.value)} placeholder="Paste chapter content (min 100 characters)…"
        rows={4}
        style={{ width: '100%', background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '10px 12px', fontSize: 13, color: '#111827', outline: 'none', resize: 'vertical', marginBottom: 10, boxSizing: 'border-box' }}
      />

      <div className="flex gap-2 mb-4">
        <button onClick={run} disabled={loading} style={{ ...btn('#06b6d4'), flex: 1, justifyContent: 'center' }}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : <CreditCard size={14} />}
          {loading ? 'Generating…' : 'Generate Flashcards'}
        </button>
        {cards.length > 0 && (
          <button onClick={copyAll} style={{ ...btn('#10b981'), padding: '8px 14px' }}>
            <Copy size={13} /> Copy All
          </button>
        )}
      </div>

      {cards.length > 0 && current && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <span style={{ fontSize: 12, color: '#6b7280' }}>{activeIdx + 1} / {cards.length}</span>
            <div style={{ display: 'flex', gap: 4 }}>
              <button onClick={() => setActiveIdx(i => Math.max(0, i - 1))} disabled={activeIdx === 0}
                style={{ ...btn('#06b6d4'), padding: '4px 10px', fontSize: 11, opacity: activeIdx === 0 ? 0.4 : 1 }}>← Prev</button>
              <button onClick={() => setActiveIdx(i => Math.min(cards.length - 1, i + 1))} disabled={activeIdx === cards.length - 1}
                style={{ ...btn('#06b6d4'), padding: '4px 10px', fontSize: 11, opacity: activeIdx === cards.length - 1 ? 0.4 : 1 }}>Next →</button>
            </div>
          </div>

          <div
            onClick={() => setFlipped(f => ({ ...f, [activeIdx]: !f[activeIdx] }))}
            style={{
              cursor: 'pointer',
              minHeight: 120,
              background: flipped[activeIdx] ? 'rgba(6,182,212,0.08)' : '#f9fafb',
              border: `1px solid ${flipped[activeIdx] ? 'rgba(6,182,212,0.35)' : '#e5e7eb'}`,
              borderRadius: 14,
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              textAlign: 'center',
              transition: 'all 0.2s',
            }}
          >
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: flipped[activeIdx] ? '#22d3ee' : '#9ca3af', marginBottom: 8 }}>
              {flipped[activeIdx] ? 'ANSWER' : 'QUESTION — tap to reveal'}
            </div>
            <div style={{ fontSize: 14, color: '#111827', lineHeight: 1.6 }}>
              {flipped[activeIdx] ? current.back : current.front}
            </div>
            <div className="flex gap-2 mt-3 flex-wrap justify-center">
              {current.difficulty && (
                <span style={{ fontSize: 10, color: diffColor[current.difficulty] || '#a855f7', background: `${diffColor[current.difficulty] || '#a855f7'}18`, border: `1px solid ${diffColor[current.difficulty] || '#a855f7'}33`, borderRadius: 20, padding: '1px 8px' }}>
                  {current.difficulty}
                </span>
              )}
              {current.type && <span style={{ fontSize: 10, color: '#94a3b8', background: '#e5e7eb', border: '1px solid #d1d5db', borderRadius: 20, padding: '1px 8px' }}>{current.type}</span>}
            </div>
          </div>

          <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
            {cards.slice(0, 8).map((_, i) => (
              <button key={i} onClick={() => { setActiveIdx(i); setFlipped(f => ({ ...f, [i]: false })); }}
                style={{ width: 28, height: 28, borderRadius: 8, border: `1px solid ${i === activeIdx ? 'rgba(6,182,212,0.5)' : '#e5e7eb'}`, background: i === activeIdx ? 'rgba(6,182,212,0.15)' : '#f9fafb', cursor: 'pointer', fontSize: 11, color: i === activeIdx ? '#22d3ee' : '#9ca3af', fontWeight: 700 }}>
                {i + 1}
              </button>
            ))}
            {cards.length > 8 && <span style={{ fontSize: 11, color: '#9ca3af', alignSelf: 'center' }}>+{cards.length - 8} more</span>}
          </div>
        </div>
      )}
    </div>
  );
}
