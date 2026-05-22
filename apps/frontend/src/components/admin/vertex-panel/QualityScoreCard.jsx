import { useState } from 'react';
import { BarChart2, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { vertexQualityScore } from '@/utils/api';
import { card, btn, Badge, ScoreBar } from './shared';

export default function QualityScoreCard({ token }) {
  const [content, setContent] = useState('');
  const [pageType, setPageType] = useState('notes');
  const [topic, setTopic] = useState('');
  const [subject, setSubject] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (!content.trim()) return;
    setLoading(true);
    try {
      const r = await vertexQualityScore(token, content.trim(), pageType, topic, subject);
      setResult(r.data);
    } catch {
      toast.error('Quality scoring failed');
    } finally { setLoading(false); }
  }

  return (
    <div style={card}>
      <div className="flex items-center gap-2 mb-4">
        <BarChart2 size={16} color="#f59e0b" />
        <span style={{ fontWeight: 700, color: '#111827' }}>Content Quality Scorer</span>
        <Badge label="Gemini Review" color="#f59e0b" />
      </div>
      <p style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>
        Score accuracy, completeness, clarity and exam relevance before publishing.
      </p>
      <div className="flex gap-2 mb-3">
        <input value={topic} onChange={e => setTopic(e.target.value)} placeholder="Topic"
          style={{ flex: 1, background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '7px 12px', color: '#111827', fontSize: 13 }} />
        <input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject"
          style={{ flex: 1, background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '7px 12px', color: '#111827', fontSize: 13 }} />
        <select value={pageType} onChange={e => setPageType(e.target.value)}
          style={{ background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '7px 12px', color: '#111827', fontSize: 13 }}>
          {['notes', 'mcqs', 'definition', 'important-questions', 'examples'].map(t =>
            <option key={t} value={t}>{t}</option>
          )}
        </select>
      </div>
      <textarea value={content} onChange={e => setContent(e.target.value)} rows={4}
        placeholder="Paste content to score..."
        style={{ width: '100%', background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '10px 14px', color: '#111827', fontSize: 13, resize: 'vertical', fontFamily: 'inherit', marginBottom: 10 }}
      />
      <button onClick={run} disabled={loading || !content.trim()} style={btn('#f59e0b')}>
        {loading ? <Loader2 size={13} className="animate-spin" /> : <BarChart2 size={13} />}
        Score Content
      </button>
      {result && (
        <div style={{ marginTop: 14, background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.2)', borderRadius: 10, padding: 16 }}>
          <div className="flex items-center gap-3 mb-4">
            <div style={{ fontSize: 32, fontWeight: 900, color: result.overall >= 7 ? '#10b981' : result.overall >= 5 ? '#f59e0b' : '#ef4444' }}>
              {result.overall}/10
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#6b7280', fontWeight: 600 }}>Overall Score</div>
              <div style={{ fontSize: 11, color: result.overall >= 7 ? '#10b981' : '#f59e0b' }}>
                {result.overall >= 8 ? 'Ready to publish' : result.overall >= 6 ? 'Needs minor edits' : 'Needs improvement'}
              </div>
            </div>
          </div>
          <ScoreBar label="Accuracy" value={result.accuracy || 0} />
          <ScoreBar label="Completeness" value={result.completeness || 0} />
          <ScoreBar label="Clarity" value={result.clarity || 0} />
          <ScoreBar label="Exam Relevance" value={result.exam_relevance || 0} />
          {result.issues?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#ef4444', marginBottom: 4 }}>ISSUES</div>
              {result.issues.map((iss, i) => <div key={i} style={{ fontSize: 12, color: '#6b7280', paddingLeft: 10 }}>• {iss}</div>)}
            </div>
          )}
          {result.strengths?.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#10b981', marginBottom: 4 }}>STRENGTHS</div>
              {result.strengths.map((s, i) => <div key={i} style={{ fontSize: 12, color: '#6b7280', paddingLeft: 10 }}>✓ {s}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
