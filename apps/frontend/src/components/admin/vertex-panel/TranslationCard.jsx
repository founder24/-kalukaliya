import { useState, useEffect } from 'react';
import { Languages, Loader2, Copy } from 'lucide-react';
import { toast } from 'sonner';
import { vertexTranslate, API_BASE } from '@/utils/api';
import axios from 'axios';
import { card, btn, Badge } from './shared';

const FALLBACK_LANGS = [
  { code: 'as', label: 'Assamese (অসমীয়া)' },
  { code: 'hi', label: 'Hindi (हिन्दी)' },
  { code: 'bn', label: 'Bengali (বাংলা)' },
  { code: 'bho', label: 'Bodo (बड़ो)' },
];

export default function TranslationCard({ token }) {
  const [text, setText] = useState('');
  const [lang, setLang] = useState('as');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  const [langs, setLangs] = useState(FALLBACK_LANGS);

  useEffect(() => {
    axios.get(`${API_BASE}/admin/translation/languages`, { withCredentials: true })
      .then(r => {
        const list = (r.data || []).filter(l => l.code && l.label);
        if (list.length > 0) setLangs(list);
      })
      .catch(() => {});
  }, []);

  async function run() {
    if (!text.trim()) return;
    setLoading(true);
    try {
      const r = await vertexTranslate(token, text.trim(), lang);
      setResult(r.data.translated_text || r.data.translated || '');
      toast.success('Translation complete');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Translation failed');
    } finally { setLoading(false); }
  }

  return (
    <div style={card}>
      <div className="flex items-center gap-2 mb-4">
        <Languages size={16} color="#10b981" />
        <span style={{ fontWeight: 700, color: '#111827' }}>Regional Language Translation</span>
        <Badge label="Gemini Multilingual" color="#10b981" />
      </div>
      <p style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>
        Translate educational content into Assamese, Hindi, Bengali, or Bodo. Keeps all technical terms intact.
      </p>
      <div className="flex gap-2 mb-3">
        <select value={lang} onChange={e => setLang(e.target.value)}
          style={{ background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '8px 12px', color: '#111827', fontSize: 13 }}>
          {langs.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}
        </select>
        <button onClick={run} disabled={loading || !text.trim()} style={btn('#10b981')}>
          {loading ? <Loader2 size={13} className="animate-spin" /> : <Languages size={13} />}
          Translate
        </button>
      </div>
      <textarea value={text} onChange={e => setText(e.target.value)} rows={4}
        placeholder="Paste English content here to translate..."
        style={{ width: '100%', background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '10px 14px', color: '#111827', fontSize: 13, resize: 'vertical', fontFamily: 'inherit' }}
      />
      {result && (
        <div style={{ marginTop: 12, background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.2)', borderRadius: 10, padding: 14 }}>
          <div className="flex items-center justify-between mb-2">
            <span style={{ fontSize: 11, fontWeight: 700, color: '#10b981', textTransform: 'uppercase' }}>Translation</span>
            <button onClick={() => { navigator.clipboard.writeText(result); toast.success('Copied!'); }}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#10b981' }}>
              <Copy size={13} />
            </button>
          </div>
          <p style={{ fontSize: 14, color: '#111827', lineHeight: 1.7 }}>{result}</p>
        </div>
      )}
    </div>
  );
}
