import { useState, useRef, useCallback, useEffect } from 'react';
import {
  Upload, FileText, Loader2, Trash2, Play,
  Download, ExternalLink, ChevronDown, ChevronUp,
  Type, Image, Maximize2, Minimize2,
} from 'lucide-react';
import axios from 'axios';
import { toast } from 'sonner';
import { API, authHeaders } from '@/utils/adminHelpers';

const STATUS_MAP = {
  uploaded:    { label: 'Uploaded',   color: 'text-blue-500',   bg: 'bg-blue-500/10' },
  ocr_running: { label: 'Processing', color: 'text-amber-500',  bg: 'bg-amber-500/10' },
  ocr_done:    { label: 'Done',       color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
  ocr_error:   { label: 'Error',      color: 'text-red-500',    bg: 'bg-red-500/10' },
  fetch_error: { label: 'Fetch Error', color: 'text-red-500',   bg: 'bg-red-500/10' },
};

const SIZE_PRESETS = [
  { label: 'S', value: 25 },
  { label: 'M', value: 50 },
  { label: 'L', value: 75 },
  { label: 'Full', value: 100 },
];

function ImageCard({ pyq, onProcess, onDelete, isProcessing }) {
  const [scale, setScale] = useState(50);
  const [preview, setPreview] = useState(false);
  const st = STATUS_MAP[pyq.processing_status] || STATUS_MAP.uploaded;
  const hasImage = pyq.is_image && pyq.file_url;

  return (
    <div className="group relative rounded-xl border border-gray-200 bg-white overflow-hidden transition-shadow hover:shadow-md">
      {hasImage ? (
        <div className="relative bg-[#f8f8f8]" style={{ minHeight: 80 }}>
          <div className="flex items-center justify-center p-2" style={{ maxHeight: 300, overflow: 'hidden' }}>
            <img
              src={pyq.file_url}
              alt={pyq.filename}
              className="rounded-lg object-contain transition-all duration-200"
              style={{ width: `${scale}%`, maxHeight: 280 }}
              onClick={() => setPreview(true)}
            />
          </div>
          <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent px-3 py-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="flex items-center gap-1.5">
              {SIZE_PRESETS.map(p => (
                <button
                  key={p.label}
                  onClick={(e) => { e.stopPropagation(); setScale(p.value); }}
                  className={`px-2 py-0.5 rounded text-[10px] font-semibold transition-colors ${
                    scale === p.value
                      ? 'bg-amber-500 text-white'
                      : 'bg-white/20 text-white hover:bg-white/40'
                  }`}
                >
                  {p.label}
                </button>
              ))}
              <input
                type="range"
                min={10}
                max={100}
                value={scale}
                onChange={(e) => { e.stopPropagation(); setScale(Number(e.target.value)); }}
                className="flex-1 h-1 accent-amber-500 cursor-pointer"
                onClick={(e) => e.stopPropagation()}
              />
              <span className="text-[10px] text-white/80 font-mono w-8 text-right">{scale}%</span>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-center py-6 bg-gray-50">
          {pyq.is_text ? (
            <Type size={28} className="text-amber-400" />
          ) : (
            <FileText size={28} className="text-gray-300" />
          )}
        </div>
      )}

      <div className="px-3 py-2 border-t border-gray-100">
        <div className="flex items-center gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-[11px] font-medium text-gray-900 truncate">
              {pyq.is_text ? 'Text PYQ' : pyq.filename}
            </p>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className={`text-[9px] px-1.5 py-0.5 rounded ${st.bg} ${st.color} font-medium`}>
                {st.label}
              </span>
              <span className="text-[9px] text-gray-400">{pyq.exam_year}</span>
              {pyq.question_count > 0 && (
                <span className="text-[9px] text-gray-400">{pyq.question_count}Q</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-0.5">
            {pyq.processing_status === 'uploaded' && (pyq.is_pdf || pyq.is_image) && (
              <button
                onClick={() => onProcess(pyq.id)}
                disabled={isProcessing}
                title="Process (OCR)"
                className="p-1.5 rounded-lg hover:bg-amber-500/10 text-amber-500 disabled:opacity-50"
              >
                {isProcessing ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
              </button>
            )}
            {pyq.seo_url && (
              <a href={pyq.seo_url} target="_blank" rel="noopener noreferrer" title="View" className="p-1.5 rounded-lg hover:bg-blue-500/10 text-blue-500">
                <ExternalLink size={11} />
              </a>
            )}
            {pyq.file_url && !pyq.file_url.startsWith('data:') && (
              <a href={pyq.file_url} target="_blank" rel="noopener noreferrer" title="Download" className="p-1.5 rounded-lg hover:bg-emerald-500/10 text-emerald-500">
                <Download size={11} />
              </a>
            )}
            <button onClick={() => onDelete(pyq.id)} title="Delete" className="p-1.5 rounded-lg hover:bg-red-500/10 text-red-400">
              <Trash2 size={11} />
            </button>
          </div>
        </div>
      </div>

      {preview && hasImage && (
        <div
          className="fixed inset-0 z-[9999] bg-black/80 flex items-center justify-center p-8 cursor-pointer"
          onClick={() => setPreview(false)}
        >
          <img src={pyq.file_url} alt={pyq.filename} className="max-w-full max-h-full object-contain rounded-xl shadow-2xl" />
          <button className="absolute top-6 right-6 p-2 rounded-full bg-white/10 text-white hover:bg-white/20">
            <Minimize2 size={18} />
          </button>
        </div>
      )}
    </div>
  );
}

export default function PYQUploadPanel({
  adminToken, chapterId, subjectId, boardId, classId, streamId, examYear: defaultYear,
}) {
  const [pyqs, setPyqs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(new Set());
  const [batchProcessing, setBatchProcessing] = useState(false);
  const [examYear, setExamYear] = useState(defaultYear || new Date().getFullYear());
  const [dragging, setDragging] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const [showTextInput, setShowTextInput] = useState(false);
  const [textContent, setTextContent] = useState('');
  const [submittingText, setSubmittingText] = useState(false);
  const [viewMode, setViewMode] = useState('grid');
  const fileInputRef = useRef(null);
  const dropRef = useRef(null);
  const uploadingRef = useRef(false);

  const loadPyqs = useCallback(async () => {
    if (!chapterId) return;
    setLoading(true);
    try {
      const res = await axios.get(
        `${API}/admin/pyq/by-chapter/${chapterId}`,
        authHeaders(adminToken)
      );
      setPyqs(res.data?.pyqs || []);
    } catch {
      setPyqs([]);
    } finally {
      setLoading(false);
    }
  }, [chapterId, adminToken]);

  useEffect(() => { loadPyqs(); }, [loadPyqs]);

  const uploadFiles = useCallback(async (fileList) => {
    if (!fileList || fileList.length === 0) return;
    const allowedExts = ['.pdf','.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.tif'];
    const valid = Array.from(fileList).filter(
      f => f.type === 'application/pdf' || f.type.startsWith('image/') || allowedExts.some(ext => f.name.toLowerCase().endsWith(ext))
    );
    if (valid.length === 0) {
      toast.error('Only PDF and image files (JPG, PNG, WebP) are supported');
      return;
    }
    if (valid.some(f => f.size > 50 * 1024 * 1024)) {
      toast.error('Max file size is 50 MB');
      return;
    }
    setUploading(true);
    uploadingRef.current = true;
    try {
      const formData = new FormData();
      valid.forEach(f => formData.append('files', f));
      formData.append('exam_year', String(examYear));
      formData.append('paper_type', 'major');
      formData.append('subject_id', subjectId || '');
      formData.append('board_id', boardId || '');
      formData.append('class_id', classId || '');
      formData.append('stream_id', streamId || '');
      formData.append('chapter_id', chapterId || '');

      const res = await axios.post(`${API}/admin/pyq/upload`, formData, {
        ...authHeaders(adminToken),
        headers: { ...authHeaders(adminToken).headers, 'Content-Type': 'multipart/form-data' },
      });
      const imgCount = valid.filter(f => f.type.startsWith('image/')).length;
      const pdfCount = valid.length - imgCount;
      const parts = [];
      if (pdfCount > 0) parts.push(`${pdfCount} PDF(s)`);
      if (imgCount > 0) parts.push(`${imgCount} image(s)`);
      toast.success(`${parts.join(' + ')} uploaded`);
      await loadPyqs();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
      uploadingRef.current = false;
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [examYear, subjectId, boardId, classId, streamId, chapterId, adminToken, loadPyqs]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    if (uploadingRef.current) {
      toast.warning('Please wait for the current upload to finish');
      return;
    }
    uploadFiles(e.dataTransfer.files);
  }, [uploadFiles]);

  const handleDragOver = useCallback((e) => { e.preventDefault(); setDragging(true); }, []);
  const handleDragLeave = useCallback(() => setDragging(false), []);

  const processOne = useCallback(async (pyqId) => {
    setProcessing(prev => new Set([...prev, pyqId]));
    try {
      await axios.post(
        `${API}/admin/pyq/agentic-process`,
        { pyq_id: pyqId },
        authHeaders(adminToken)
      );
      toast.success('PYQ processed — OCR complete');
      await loadPyqs();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Processing failed');
    } finally {
      setProcessing(prev => { const s = new Set(prev); s.delete(pyqId); return s; });
    }
  }, [adminToken, loadPyqs]);

  const processAll = useCallback(async () => {
    const pending = pyqs.filter(p => p.processing_status === 'uploaded' && (p.is_pdf || p.is_image));
    if (pending.length === 0) {
      toast.info('No unprocessed files to process');
      return;
    }
    setBatchProcessing(true);
    try {
      const res = await axios.post(
        `${API}/admin/pyq/batch-process`,
        { pyq_ids: pending.map(p => p.id) },
        authHeaders(adminToken)
      );
      toast.success(`Processed ${res.data?.succeeded || 0} / ${res.data?.total || 0} files`);
      await loadPyqs();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Batch processing failed');
    } finally {
      setBatchProcessing(false);
    }
  }, [pyqs, adminToken, loadPyqs]);

  const deleteOne = useCallback(async (pyqId) => {
    try {
      await axios.delete(`${API}/admin/pyq/${pyqId}`, authHeaders(adminToken));
      toast.success('PYQ deleted');
      setPyqs(prev => prev.filter(p => p.id !== pyqId));
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Delete failed');
    }
  }, [adminToken]);

  const submitText = useCallback(async () => {
    if (!textContent.trim()) {
      toast.error('Please paste some question paper text');
      return;
    }
    setSubmittingText(true);
    try {
      await axios.post(`${API}/admin/pyq/upload-text`, {
        text: textContent.trim(),
        exam_year: examYear,
        paper_type: 'major',
        subject_id: subjectId || '',
        board_id: boardId || '',
        class_id: classId || '',
        stream_id: streamId || '',
        chapter_id: chapterId || '',
      }, authHeaders(adminToken));
      toast.success('Text PYQ uploaded & processed');
      setTextContent('');
      setShowTextInput(false);
      await loadPyqs();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Text upload failed');
    } finally {
      setSubmittingText(false);
    }
  }, [textContent, examYear, subjectId, boardId, classId, streamId, chapterId, adminToken, loadPyqs]);

  const pendingCount = pyqs.filter(p => p.processing_status === 'uploaded' && (p.is_pdf || p.is_image)).length;
  const imageCount = pyqs.filter(p => p.is_image).length;
  const currentYear = new Date().getFullYear();

  return (
    <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 overflow-hidden">
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-amber-500/10 transition-colors"
      >
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-amber-500" />
          <span className="text-sm font-semibold text-gray-900">PYQ Papers</span>
          {pyqs.length > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-600">
              {pyqs.length}
            </span>
          )}
          {imageCount > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-500">
              {imageCount} img
            </span>
          )}
        </div>
        {expanded ? <ChevronUp size={14} className="text-gray-400" /> : <ChevronDown size={14} className="text-gray-400" />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-1.5">
              <label className="text-xs text-gray-500">Year:</label>
              <select
                value={examYear}
                onChange={(e) => setExamYear(Number(e.target.value))}
                className="h-8 px-2 rounded-lg text-xs bg-white border border-gray-200 text-gray-900 outline-none focus:border-amber-500"
              >
                {Array.from({ length: 15 }, (_, i) => currentYear - i).map(y => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </div>
            {pyqs.length > 0 && (
              <div className="flex items-center gap-1 ml-auto">
                <button
                  onClick={() => setViewMode('grid')}
                  className={`p-1.5 rounded-lg text-xs transition-colors ${viewMode === 'grid' ? 'bg-amber-500/15 text-amber-600' : 'text-gray-400 hover:text-gray-600'}`}
                  title="Grid view"
                >
                  <Maximize2 size={12} />
                </button>
                <button
                  onClick={() => setViewMode('list')}
                  className={`p-1.5 rounded-lg text-xs transition-colors ${viewMode === 'list' ? 'bg-amber-500/15 text-amber-600' : 'text-gray-400 hover:text-gray-600'}`}
                  title="List view"
                >
                  <FileText size={12} />
                </button>
              </div>
            )}
            {pendingCount > 0 && (
              <button
                onClick={processAll}
                disabled={batchProcessing}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
              >
                {batchProcessing ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                Process All ({pendingCount})
              </button>
            )}
          </div>

          <div
            ref={dropRef}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => !uploading && fileInputRef.current?.click()}
            className={`relative flex flex-col items-center justify-center gap-3 py-8 rounded-xl border-2 border-dashed cursor-pointer transition-all ${
              dragging
                ? 'border-amber-500 bg-amber-500/10'
                : 'border-gray-200 hover:border-amber-500/50 hover:bg-amber-500/5'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,.webp,.gif,.bmp,.tiff,.tif,image/*"
              multiple
              className="hidden"
              onChange={(e) => uploadFiles(e.target.files)}
            />
            {uploading ? (
              <>
                <Loader2 size={28} className="text-amber-500 animate-spin" />
                <span className="text-sm text-gray-500">Uploading...</span>
              </>
            ) : (
              <>
                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-amber-400/20 to-orange-400/20 flex items-center justify-center">
                  <div className="flex items-center gap-1">
                    <Image size={18} className="text-amber-500" />
                    <Upload size={14} className="text-amber-400" />
                  </div>
                </div>
                <div className="text-center">
                  <span className="text-sm font-medium text-gray-700 block">
                    Drop images or PDFs here
                  </span>
                  <span className="text-xs text-gray-400 mt-0.5 block">
                    or <span className="text-amber-600 font-medium">click to browse</span>
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">JPG</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">PNG</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">WebP</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">PDF</span>
                </div>
              </>
            )}
          </div>

          <div className="flex items-center justify-center">
            <button
              onClick={() => setShowTextInput(v => !v)}
              className="flex items-center gap-1.5 text-xs text-amber-600 hover:text-amber-700 font-medium transition-colors"
            >
              <Type size={12} />
              {showTextInput ? 'Hide text input' : 'Or paste question text directly'}
            </button>
          </div>

          {showTextInput && (
            <div className="space-y-2">
              <textarea
                value={textContent}
                onChange={(e) => setTextContent(e.target.value)}
                placeholder={"Paste question paper text here...\n\nExample:\n1. What is photosynthesis? [5]\n2. Explain Newton's third law. [3]\na) Give an example.\nb) State the formula."}
                rows={8}
                className="w-full px-3 py-2 rounded-xl border border-gray-200 bg-white text-sm text-gray-900 placeholder:text-gray-400 outline-none focus:border-amber-500 resize-y"
              />
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-gray-400">
                  {textContent.trim() ? `${textContent.trim().split('\n').length} lines` : 'No text entered'}
                </span>
                <button
                  onClick={submitText}
                  disabled={submittingText || !textContent.trim()}
                  className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
                >
                  {submittingText ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                  Upload Text PYQ
                </button>
              </div>
            </div>
          )}

          {loading && (
            <div className="flex items-center justify-center py-4">
              <Loader2 size={16} className="text-gray-400 animate-spin" />
            </div>
          )}

          {!loading && pyqs.length > 0 && viewMode === 'grid' && (
            <div className="grid grid-cols-2 gap-2">
              {pyqs.map(pyq => (
                <ImageCard
                  key={pyq.id}
                  pyq={pyq}
                  onProcess={processOne}
                  onDelete={deleteOne}
                  isProcessing={processing.has(pyq.id) || pyq.processing_status === 'ocr_running'}
                />
              ))}
            </div>
          )}

          {!loading && pyqs.length > 0 && viewMode === 'list' && (
            <div className="space-y-1.5">
              {pyqs.map(pyq => {
                const st = STATUS_MAP[pyq.processing_status] || STATUS_MAP.uploaded;
                const isProcessing = processing.has(pyq.id) || pyq.processing_status === 'ocr_running';
                return (
                  <div key={pyq.id} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white border border-gray-100 group">
                    {pyq.is_image && pyq.file_url ? (
                      <img src={pyq.file_url} alt="" className="w-8 h-8 rounded object-cover flex-shrink-0 border border-gray-200" />
                    ) : pyq.is_text ? (
                      <Type size={14} className="text-amber-500 flex-shrink-0" />
                    ) : (
                      <FileText size={14} className="text-gray-400 flex-shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-gray-900 truncate">{pyq.is_text ? 'Text PYQ' : pyq.filename}</p>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${st.bg} ${st.color} font-medium`}>
                          {st.label}
                        </span>
                        <span className="text-[10px] text-gray-400">{pyq.exam_year}</span>
                        {pyq.question_count > 0 && (
                          <span className="text-[10px] text-gray-400">{pyq.question_count} Q</span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      {pyq.processing_status === 'uploaded' && (pyq.is_pdf || pyq.is_image) && (
                        <button
                          onClick={() => processOne(pyq.id)}
                          disabled={isProcessing}
                          title="Process (OCR)"
                          className="p-1.5 rounded-lg hover:bg-amber-500/10 text-amber-500 disabled:opacity-50"
                        >
                          {isProcessing ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                        </button>
                      )}
                      {pyq.seo_url && (
                        <a href={pyq.seo_url} target="_blank" rel="noopener noreferrer" title="View PYQ Page" className="p-1.5 rounded-lg hover:bg-blue-500/10 text-blue-500">
                          <ExternalLink size={12} />
                        </a>
                      )}
                      {pyq.file_url && !pyq.file_url.startsWith('data:') && (
                        <a href={pyq.file_url} target="_blank" rel="noopener noreferrer" title="Download" className="p-1.5 rounded-lg hover:bg-emerald-500/10 text-emerald-500">
                          <Download size={12} />
                        </a>
                      )}
                      <button onClick={() => deleteOne(pyq.id)} title="Delete" className="p-1.5 rounded-lg hover:bg-red-500/10 text-red-400">
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {!loading && pyqs.length === 0 && !uploading && (
            <p className="text-xs text-gray-400 text-center py-2">No PYQ papers uploaded yet</p>
          )}
        </div>
      )}
    </div>
  );
}
