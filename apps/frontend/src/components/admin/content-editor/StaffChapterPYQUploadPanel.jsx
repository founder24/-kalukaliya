import { useEffect, useState } from 'react';
import { FileText, ImagePlus, Loader2, Trash2, Upload } from 'lucide-react';
import axios from 'axios';
import { toast } from 'sonner';
import { API, authHeaders } from '@/utils/adminHelpers';

const EMPTY_PAPERS = [];

export default function StaffChapterPYQUploadPanel({
  chapterId,
  adminToken,
  initialPdfUrl,
  initialPapers,
}) {
  const [pdfUrl, setPdfUrl] = useState(initialPdfUrl || '');
  const [papers, setPapers] = useState(Array.isArray(initialPapers) ? initialPapers : EMPTY_PAPERS);
  const [uploadingPdf, setUploadingPdf] = useState(false);
  const [uploadingPages, setUploadingPages] = useState(false);
  const [deletingPaper, setDeletingPaper] = useState(null);

  useEffect(() => {
    setPdfUrl(initialPdfUrl || '');
    setPapers(Array.isArray(initialPapers) ? initialPapers : EMPTY_PAPERS);
  }, [chapterId, initialPdfUrl, initialPapers]);

  const uploadPdf = async (event) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      toast.error('The PDF must be smaller than 50 MB');
      input.value = '';
      return;
    }

    setUploadingPdf(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const response = await axios.post(
        `${API}/staff/content/chapter/${chapterId}/upload-pyq`,
        formData,
        authHeaders(adminToken),
      );
      setPdfUrl(response.data?.pyq_pdf_url || '');
      toast.success('PYQ paper uploaded');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Could not upload the PYQ paper');
    } finally {
      setUploadingPdf(false);
      input.value = '';
    }
  };

  const uploadPages = async (event) => {
    const input = event.currentTarget;
    const files = Array.from(input.files || []);
    if (!files.length) return;
    if (files.some(file => file.size > 20 * 1024 * 1024)) {
      toast.error('Each scanned page must be smaller than 20 MB');
      input.value = '';
      return;
    }

    setUploadingPages(true);
    let uploaded = 0;
    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('title', file.name);
        const response = await axios.post(
          `${API}/staff/content/chapter/${chapterId}/pyq-papers`,
          formData,
          authHeaders(adminToken),
        );
        if (Array.isArray(response.data?.pyq_papers)) {
          setPapers(response.data.pyq_papers);
        } else if (response.data?.paper) {
          setPapers(previous => [...previous, response.data.paper]);
        }
        uploaded += 1;
      }
      toast.success(`${uploaded} scanned page${uploaded === 1 ? '' : 's'} uploaded`);
    } catch (error) {
      toast.error(
        error.response?.data?.detail ||
          (uploaded ? `${uploaded} page(s) uploaded; the remaining upload failed` : 'Could not upload scanned pages'),
      );
    } finally {
      setUploadingPages(false);
      input.value = '';
    }
  };

  const removePage = async (paper) => {
    if (!window.confirm(`Remove "${paper.title || 'this scanned page'}"?`)) return;
    setDeletingPaper(paper.id);
    try {
      const response = await axios.delete(
        `${API}/staff/content/chapter/${chapterId}/pyq-papers/${paper.id}`,
        authHeaders(adminToken),
      );
      setPapers(Array.isArray(response.data?.pyq_papers)
        ? response.data.pyq_papers
        : previous => previous.filter(item => item.id !== paper.id));
      toast.success('Scanned page removed');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Could not remove the scanned page');
    } finally {
      setDeletingPaper(null);
    }
  };

  return (
    <section className="space-y-4" data-testid="staff-pyq-upload-panel">
      <div>
        <h4 className="text-sm font-semibold text-gray-900">Previous year papers</h4>
        <p className="mt-1 text-xs leading-relaxed text-gray-500">
          Upload a paper PDF or scanned page images. Files are stored as uploaded; this editor does not run OCR, translation, or AI processing.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-gray-300 bg-gray-50 p-4 text-center hover:border-amber-400 hover:bg-amber-50/40">
          {uploadingPdf ? <Loader2 size={18} className="animate-spin text-amber-600" /> : <Upload size={18} className="text-amber-600" />}
          <span className="text-sm font-medium text-gray-800">{uploadingPdf ? 'Uploading paper…' : 'Upload paper PDF'}</span>
          <span className="text-xs text-gray-500">One PDF, up to 50 MB</span>
          <input type="file" accept="application/pdf,.pdf" className="sr-only" onChange={uploadPdf} disabled={uploadingPdf || uploadingPages} />
        </label>

        <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-gray-300 bg-gray-50 p-4 text-center hover:border-amber-400 hover:bg-amber-50/40">
          {uploadingPages ? <Loader2 size={18} className="animate-spin text-amber-600" /> : <ImagePlus size={18} className="text-amber-600" />}
          <span className="text-sm font-medium text-gray-800">{uploadingPages ? 'Uploading pages…' : 'Add scanned page images'}</span>
          <span className="text-xs text-gray-500">JPG, PNG, WebP, or GIF; up to 20 MB each</span>
          <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple className="sr-only" onChange={uploadPages} disabled={uploadingPdf || uploadingPages} />
        </label>
      </div>

      {pdfUrl && (
        <a
          href={pdfUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 hover:bg-amber-100"
          data-testid="staff-pyq-pdf-link"
        >
          <FileText size={15} />
          <span className="min-w-0 flex-1 truncate">Open uploaded PYQ paper</span>
          <span className="text-xs">Open</span>
        </a>
      )}

      {papers.length > 0 && (
        <div className="space-y-2">
          <h5 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Scanned pages ({papers.length})</h5>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {papers.map((paper, index) => (
              <div key={paper.id || paper.url || index} className="flex min-w-0 items-center gap-3 rounded-lg border border-gray-200 bg-white p-2.5">
                {paper.url ? (
                  <img src={paper.url} alt={paper.title || `PYQ page ${index + 1}`} className="h-12 w-12 rounded object-cover bg-gray-100" />
                ) : (
                  <FileText size={18} className="text-amber-500" />
                )}
                <div className="min-w-0 flex-1">
                  <a href={paper.url} target="_blank" rel="noopener noreferrer" className="block truncate text-xs font-medium text-gray-800 hover:text-amber-700">
                    {paper.title || `Page ${index + 1}`}
                  </a>
                  {paper.year && <span className="text-[10px] text-gray-500">{paper.year}</span>}
                </div>
                <button
                  type="button"
                  onClick={() => removePage(paper)}
                  disabled={deletingPaper === paper.id}
                  aria-label={`Remove ${paper.title || `page ${index + 1}`}`}
                  className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
                >
                  {deletingPaper === paper.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}