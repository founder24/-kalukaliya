import { useState, useRef } from 'react';
import { Eye, Loader2, Copy, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { vertexOcr } from '@/utils/api';
import { card, btn } from './shared';

export default function VisionOcrCard({ token }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const fileRef = useRef();

  function handleFile(f) {
    if (!f) return;
    setFile(f);
    setResult(null);
    const reader = new FileReader();
    reader.onload = e => setPreview(e.target.result);
    reader.readAsDataURL(f);
  }

  async function run() {
    if (!file) return toast.error('Upload an image first');
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await vertexOcr(token, fd);
      setResult(r.data);
      toast.success('OCR complete');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'OCR failed');
    } finally {
      setLoading(false);
    }
  }

  function copy(text) {
    navigator.clipboard.writeText(text);
    toast.success('Copied!');
  }

  return (
    <div style={card}>
      <div className="flex items-center gap-3 mb-4">
        <Eye size={18} color="#f97316" />
        <div>
          <div style={{ fontSize: 15, fontWeight: 800, color: '#111827' }}>Vision OCR</div>
          <div style={{ fontSize: 12, color: '#6b7280' }}>Cloud Vision API · Extract text from AHSEC question papers &amp; textbook pages</div>
        </div>
      </div>

      <div
        style={{ border: '2px dashed rgba(249,115,22,0.3)', borderRadius: 12, padding: 20, textAlign: 'center', cursor: 'pointer', marginBottom: 14, background: 'rgba(249,115,22,0.04)' }}
        onClick={() => fileRef.current?.click()}
        onDragOver={e => e.preventDefault()}
        onDrop={e => { e.preventDefault(); handleFile(e.dataTransfer.files[0]); }}
      >
        <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={e => handleFile(e.target.files[0])} />
        {preview ? (
          <img src={preview} alt="preview" style={{ maxHeight: 180, maxWidth: '100%', borderRadius: 8, objectFit: 'contain' }} />
        ) : (
          <>
            <Upload size={28} color="rgba(249,115,22,0.5)" style={{ margin: '0 auto 8px' }} />
            <div style={{ fontSize: 13, color: '#6b7280' }}>Drop image here or click to upload</div>
            <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>JPEG · PNG · WebP · max 10MB</div>
          </>
        )}
      </div>

      <button onClick={run} disabled={loading || !file} style={{ ...btn('#f97316'), width: '100%', justifyContent: 'center', marginBottom: 14, opacity: !file ? 0.5 : 1 }}>
        {loading ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
        {loading ? 'Extracting Text…' : 'Run OCR'}
      </button>

      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ background: 'rgba(249,115,22,0.15)', color: '#fb923c', border: '1px solid rgba(249,115,22,0.3)', borderRadius: 20, padding: '2px 10px', fontSize: 11, fontWeight: 700 }}>
              {result.content_type || 'Extracted'}
            </span>
            <span style={{ background: 'rgba(16,185,129,0.1)', color: '#34d399', border: '1px solid rgba(16,185,129,0.25)', borderRadius: 20, padding: '2px 10px', fontSize: 11 }}>
              {result.word_count || 0} words
            </span>
            {result.questions?.length > 0 && (
              <span style={{ background: 'rgba(139,92,246,0.12)', color: '#a78bfa', border: '1px solid rgba(139,92,246,0.25)', borderRadius: 20, padding: '2px 10px', fontSize: 11 }}>
                {result.questions.length} questions found
              </span>
            )}
          </div>

          {result.raw_text && (
            <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 12 }}>
              <div className="flex items-center justify-between mb-2">
                <span style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase' }}>Extracted Text</span>
                <button onClick={() => copy(result.raw_text)} style={{ ...btn('#f97316'), padding: '4px 10px', fontSize: 11 }}>
                  <Copy size={11} /> Copy
                </button>
              </div>
              <pre style={{ fontSize: 12, color: '#374151', whiteSpace: 'pre-wrap', maxHeight: 200, overflowY: 'auto', lineHeight: 1.7 }}>
                {result.raw_text}
              </pre>
            </div>
          )}

          {result.questions?.length > 0 && (
            <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', marginBottom: 8 }}>Structured Questions</div>
              {result.questions.slice(0, 5).map((q, i) => (
                <div key={i} style={{ borderBottom: '1px solid #f3f4f6', padding: '6px 0', fontSize: 12, color: '#374151' }}>
                  <span style={{ color: '#fb923c', fontWeight: 700 }}>Q{q.number || i + 1}.</span> {q.text}
                  {q.marks && <span style={{ color: '#34d399', marginLeft: 8, fontSize: 11 }}>[{q.marks} marks]</span>}
                </div>
              ))}
              {result.questions.length > 5 && (
                <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>+{result.questions.length - 5} more questions</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
