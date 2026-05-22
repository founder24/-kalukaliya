import { useState, useRef, useCallback } from 'react';
import { Wand2, Upload, Loader2, X, RefreshCw, CheckCircle } from 'lucide-react';
import { API, authHeaders } from '@/utils/adminHelpers';
import { log } from '@/utils/logger';
import axios from 'axios';
import { toast } from 'sonner';

export default function ThumbnailStudio({ adminToken, selSubject, subjectData, onReload }) {
  const [thumbnailLoading, setThumbnailLoading] = useState(false);
  const [aiThumbLoading, setAiThumbLoading] = useState(false);
  const [thumbVariants, setThumbVariants] = useState([]);
  const [thumbAnalysis, setThumbAnalysis] = useState(null);
  const [selectedThumbVariant, setSelectedThumbVariant] = useState(0);
  const thumbnailInputRef = useRef(null);

  const handleUploadThumbnail = async (file) => {
    if (!file || !selSubject) return;
    setThumbnailLoading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const h = authHeaders(adminToken);
      await axios.post(`${API}/admin/content/subjects/${selSubject}/thumbnail`, form, { ...h, headers: { ...h.headers, 'Content-Type': 'multipart/form-data' } });
      toast.success('Thumbnail uploaded');
      await onReload();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to upload thumbnail');
    } finally {
      setThumbnailLoading(false);
      if (thumbnailInputRef.current) thumbnailInputRef.current.value = '';
    }
  };

  const handleGenerateAiThumbnails = useCallback(async (sourceFile = null) => {
    if (!selSubject) return;
    setAiThumbLoading(true);
    setThumbVariants([]);
    try {
      const form = new FormData();
      form.append('subject_id', selSubject);
      if (sourceFile) form.append('file', sourceFile);
      const h = authHeaders(adminToken);
      const res = await axios.post(`${API}/admin/thumbnail/generate`, form, { ...h, headers: { ...h.headers, 'Content-Type': 'multipart/form-data' } });
      setThumbVariants(res.data.variants || []);
      setThumbAnalysis(res.data.analysis || null);
      setSelectedThumbVariant(res.data.auto_selected ?? 0);
      if (res.data.original_url) await onReload();
      const regionsFound = res.data.text_regions_found || 0;
      if (regionsFound > 0) {
        toast.success(`${regionsFound} text region${regionsFound > 1 ? 's' : ''} detected and removed — pick your favourite!`);
      } else {
        toast.info('No text detected — replicas are identical to original');
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'AI thumbnail generation failed');
    } finally {
      setAiThumbLoading(false);
    }
  }, [selSubject, adminToken, onReload]);

  const handleApplyVariant = useCallback(async (variantIndex) => {
    if (!selSubject || variantIndex == null) return;
    try {
      await axios.post(`${API}/admin/thumbnail/apply`, { subject_id: selSubject, variant_index: variantIndex }, authHeaders(adminToken));
      await onReload();
      toast.success('Variant applied as thumbnail!');
    } catch (err) {
      log.error('Apply thumbnail variant failed', { error: err.message, status: err.response?.status, subjectId: selSubject });
      toast.error('Failed to apply variant');
    }
  }, [selSubject, adminToken, onReload]);

  const handleClearThumbnail = async () => {
    if (!selSubject) return;
    try {
      await axios.patch(`${API}/admin/content/subjects/${selSubject}`, { thumbnail_url: '' }, authHeaders(adminToken));
      toast.success('Thumbnail removed');
      await onReload();
    } catch { toast.error('Failed to clear thumbnail'); }
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <div className="flex items-center gap-2">
          <Wand2 size={13} className="text-violet-400" />
          <span className="text-sm font-semibold text-gray-900">AI Thumbnail Studio</span>
          <span className="text-[10px] text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full">background on Library card</span>
        </div>
        {subjectData?.thumbnailUrl && (
          <button onClick={handleClearThumbnail} className="text-[11px] text-red-400/70 hover:text-red-400 transition-colors flex items-center gap-1">
            <X size={11} /> Remove
          </button>
        )}
      </div>
      <div className="p-4 space-y-4">
        <div className="flex items-start gap-4">
          <div className="w-20 h-[72px] rounded-lg flex-shrink-0 flex items-center justify-center overflow-hidden"
            style={{ border: '1px solid #e5e7eb', background: '#f9fafb' }}>
            {subjectData?.thumbnailUrl ? (
              <img src={subjectData.thumbnailUrl} alt="thumbnail" className="w-full h-full object-cover" />
            ) : (
              <span className="text-gray-300 text-[10px] text-center px-1">No image</span>
            )}
          </div>
          <div className="flex-1 space-y-2">
            <p className="text-xs text-gray-400">Upload a book cover (PNG, JPG, WebP — max 2 MB). The AI will detect all text on it and generate 3 clean replicas with text removed.</p>
            <input ref={thumbnailInputRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                await handleUploadThumbnail(file);
                await handleGenerateAiThumbnails(file);
              }} />
            <div className="flex items-center gap-2 flex-wrap">
              <button onClick={() => thumbnailInputRef.current?.click()} disabled={thumbnailLoading || aiThumbLoading}
                className="flex items-center gap-2 h-9 px-4 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90 active:scale-95 disabled:opacity-50"
                style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', boxShadow: '0 2px 8px rgba(124,58,237,0.30)' }}>
                {thumbnailLoading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                {thumbnailLoading ? 'Uploading…' : subjectData?.thumbnailUrl ? 'Replace' : 'Upload Cover'}
              </button>
              {subjectData?.thumbnailUrl && (
                <button onClick={() => handleGenerateAiThumbnails()} disabled={aiThumbLoading}
                  className="flex items-center gap-2 h-9 px-4 rounded-lg text-xs font-semibold disabled:opacity-50 transition-all hover:opacity-90"
                  style={{ background: 'rgba(139,92,246,0.20)', border: '1px solid rgba(139,92,246,0.35)', color: '#7c3aed' }}>
                  {aiThumbLoading ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                  {aiThumbLoading ? 'Analyzing…' : thumbVariants.length > 0 ? 'Regenerate' : 'Generate AI Variants'}
                </button>
              )}
            </div>
          </div>
        </div>

        {aiThumbLoading && (
          <div className="rounded-xl p-4 flex items-center gap-3" style={{ background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.20)' }}>
            <Loader2 size={16} className="animate-spin flex-shrink-0" style={{ color: '#a78bfa' }} />
            <div>
              <p className="text-xs font-semibold" style={{ color: '#7c3aed' }}>Vision model detecting text regions…</p>
              <p className="text-[10px] mt-0.5" style={{ color: 'rgba(167,139,250,0.60)' }}>Locating text → removing it → generating 3 clean replicas</p>
            </div>
          </div>
        )}

        {thumbVariants.length > 0 && !aiThumbLoading && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold" style={{ color: '#7c3aed' }}>Text-Free Replicas</span>
                {thumbAnalysis?.style && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: 'rgba(139,92,246,0.15)', color: '#a78bfa' }}>
                    {thumbAnalysis.style} · {thumbAnalysis.mood}
                  </span>
                )}
              </div>
              <button onClick={() => handleGenerateAiThumbnails()} disabled={aiThumbLoading}
                className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-lg"
                style={{ color: '#9ca3af', background: '#e5e7eb' }}>
                <RefreshCw size={9} /> New set
              </button>
            </div>
            {thumbAnalysis?.dominant_colors && (
              <div className="flex items-center gap-1.5">
                <span className="text-[10px]" style={{ color: '#9ca3af' }}>Palette:</span>
                {[...(thumbAnalysis.dominant_colors || []), ...(thumbAnalysis.secondary_colors || [])].slice(0, 5).map((hex, i) => (
                  <div key={i} title={hex} className="w-4 h-4 rounded-full border border-gray-200 flex-shrink-0" style={{ background: hex }} />
                ))}
              </div>
            )}
            <div className="grid grid-cols-3 gap-2">
              {thumbVariants.map((varUrl, i) => (
                <div key={i}
                  className="relative group rounded-xl overflow-hidden cursor-pointer transition-all"
                  style={{ border: `2px solid ${selectedThumbVariant === i ? '#7c3aed' : '#e5e7eb'}` }}
                  onClick={() => setSelectedThumbVariant(i)}>
                  <img src={varUrl} alt={`Variant ${i + 1}`} className="w-full object-cover" style={{ aspectRatio: '2/3' }} />
                  <div className="absolute inset-0 flex flex-col justify-end p-2 opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.85) 0%, transparent 60%)' }}>
                    <button onClick={e => { e.stopPropagation(); handleApplyVariant(i); }}
                      className="w-full py-1.5 rounded-lg text-[10px] font-bold text-white"
                      style={{ background: '#7c3aed' }}>
                      Use This
                    </button>
                  </div>
                  {selectedThumbVariant === i && (
                    <div className="absolute top-1.5 right-1.5 w-5 h-5 rounded-full flex items-center justify-center" style={{ background: '#7c3aed' }}>
                      <CheckCircle size={11} className="text-white" />
                    </div>
                  )}
                  <div className="absolute bottom-0 left-0 right-0 text-center py-1 text-[8px] font-medium"
                    style={{ background: 'rgba(0,0,0,0.65)', color: '#6b7280' }}>
                    {['Smooth Fill', 'Gradient Fill', 'Median Fill'][i]}
                  </div>
                </div>
              ))}
            </div>
            <button onClick={() => handleApplyVariant(selectedThumbVariant)}
              className="w-full py-2.5 rounded-xl text-sm font-semibold text-gray-900"
              style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', boxShadow: '0 2px 10px rgba(124,58,237,0.30)' }}>
              Apply "{['Smooth Fill', 'Gradient Fill', 'Median Fill'][selectedThumbVariant]}" as Thumbnail
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
