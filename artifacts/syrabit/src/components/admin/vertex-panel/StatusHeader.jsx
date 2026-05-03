import { useState, useEffect } from 'react';
import { Cpu, CheckCircle, AlertTriangle, Loader2 } from 'lucide-react';
import { vertexHealth } from '@/utils/api';

export default function StatusHeader({ token }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    vertexHealth(token)
      .then(r => setStatus(r.data))
      .catch(() => setStatus({ ok: false, reason: 'Could not reach API' }))
      .finally(() => setLoading(false));
  }, [token]);

  const services = status?.services || [];

  return (
    <div style={{ background: 'linear-gradient(135deg, rgba(139,92,246,0.12), rgba(59,130,246,0.08))', border: '1px solid rgba(139,92,246,0.25)', borderRadius: 16, padding: 20, marginBottom: 24 }}>
      <div className="flex items-center gap-3 mb-4">
        <div style={{ width: 36, height: 36, borderRadius: 10, background: 'rgba(139,92,246,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Cpu size={18} color="#8b5cf6" />
        </div>
        <div>
          <div style={{ fontSize: 16, fontWeight: 800, color: '#111827' }}>AI Studio</div>
          <div style={{ fontSize: 12, color: '#6b7280' }}>Multi-provider router · Gemini · Cloudflare Workers AI · Google Translate · Google Vision · Cohere · AWS Textract · Azure Document Intelligence — Translation · NLP · Vision · OCR · MCQ · Flashcards · SEO</div>
        </div>
        {loading ? <Loader2 size={16} className="animate-spin ml-auto" color="#8b5cf6" /> : (
          <div className="ml-auto flex items-center gap-2">
            {status?.ok ? <CheckCircle size={16} color="#10b981" /> : <AlertTriangle size={16} color="#ef4444" />}
            <span style={{ fontSize: 13, fontWeight: 700, color: status?.ok ? '#10b981' : '#ef4444' }}>
              {status?.ok ? 'All Systems Active' : status?.reason || 'Offline'}
            </span>
          </div>
        )}
      </div>
      {services.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {services.map(s => (
            <span key={s} style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.25)', color: '#34d399', borderRadius: 20, padding: '2px 10px', fontSize: 11, fontWeight: 600 }}>
              ✓ {s.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}
      {status && !status.ok && (
        <div style={{ marginTop: 10, padding: 12, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 10, fontSize: 12, color: '#fca5a5', lineHeight: 1.8 }}>
          <strong style={{ color: '#f87171', display: 'block', marginBottom: 6 }}>⚠ Primary provider (Gemini) is unreachable</strong>
          AI Studio fans each feature out across several providers — Gemini, Cloudflare Workers AI, Google Translate, Google Vision, Cohere embeddings, AWS Textract, and Azure Document Intelligence. Gemini is the default for content generation (enhance / topic-suggester / SEO meta / MCQ / flashcards) and is currently failing; the Workers AI / Translate / Vision / Textract / Doc Intelligence paths still work for their respective features. See <strong style={{ color: '#111827' }}>API Config</strong> for per-feature provider routing.
          <br /><br />
          <strong style={{ color: '#111827' }}>Restoring Gemini</strong> — add a key to Replit Secrets as <code style={{ background: '#e5e7eb', padding: '1px 5px', borderRadius: 4 }}>GEMINI_API_KEY</code>, then restart the API:
          <br /><br />
          <strong style={{ color: '#111827' }}>Option A — Google AI Studio key</strong> (free, instant)
          <br />
          Get it at <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer" style={{ color: '#818cf8', textDecoration: 'underline' }}>aistudio.google.com/app/apikey</a> · starts with <code style={{ background: '#e5e7eb', padding: '1px 5px', borderRadius: 4 }}>AIza...</code>
          <br /><br />
          <strong style={{ color: '#111827' }}>Option B — Google Cloud Vertex AI service-account JSON</strong> (production)
          <br />
          Paste the full JSON from Google Cloud Console → IAM → Service Accounts. The account must hold the <em>Vertex AI User</em> role. (This is the credential format for the Vertex AI provider specifically — Workers AI, Cohere, Textract, Doc Intelligence and Google Translate use their own keys configured in API Config.)
        </div>
      )}
    </div>
  );
}
