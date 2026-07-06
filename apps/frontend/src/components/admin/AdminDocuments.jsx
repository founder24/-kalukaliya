import { useState, useEffect, useCallback, useRef } from 'react';
import {
  FileText, Upload, Edit2, Trash2, Search, Plus, X, Check,
  ChevronLeft, ChevronRight, RefreshCw, Image, Eye, EyeOff,
  AlertTriangle, Download, Loader2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import {
  adminGetDocuments, adminCreateDocument, adminUpdateDocument,
  adminDeleteDocument, adminUploadDocumentPdf, adminUploadDocumentCover,
  adminGetDocumentCategories,
} from '@/utils/api';

const PAGE_SIZE = 15;

function formatBytes(bytes) {
  if (!bytes) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}
function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

// ── Upload PDF modal ──────────────────────────────────────────────────────────
function DocumentModal({ doc, adminToken, onClose, onSaved, categories }) {
  const isNew = !doc;
  const [title, setTitle] = useState(doc?.title || '');
  const [description, setDescription] = useState(doc?.description || '');
  const [category, setCategory] = useState(doc?.category || '');
  const [customCategory, setCustomCategory] = useState('');
  const [status, setStatus] = useState(doc?.status || 'draft');

  // PDF state
  const [pdfFile, setPdfFile] = useState(null);
  const [pdfUrl, setPdfUrl] = useState(doc?.pdf_url || '');
  const [pdfFilename, setPdfFilename] = useState(doc?.pdf_filename || '');
  const [pdfSizeBytes, setPdfSizeBytes] = useState(doc?.pdf_size_bytes || 0);
  const [uploadingPdf, setUploadingPdf] = useState(false);

  // Cover state
  const [coverFile, setCoverFile] = useState(null);
  const [coverUrl, setCoverUrl] = useState(doc?.cover_url || '');
  const [uploadingCover, setUploadingCover] = useState(false);

  const [saving, setSaving] = useState(false);
  const pdfRef = useRef();
  const coverRef = useRef();

  const activeCategory = category === '__custom__' ? customCategory : category;

  const handlePdfSelect = async (file) => {
    if (!file) return;
    if (file.type !== 'application/pdf') { toast.error('Only PDF files are allowed'); return; }
    if (file.size > 50 * 1024 * 1024) { toast.error('PDF too large (max 50 MB)'); return; }
    setPdfFile(file);
    setUploadingPdf(true);
    try {
      const res = await adminUploadDocumentPdf(adminToken, file);
      setPdfUrl(res.data.url);
      setPdfFilename(res.data.filename);
      setPdfSizeBytes(res.data.size_bytes);
      toast.success('PDF uploaded');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'PDF upload failed');
      setPdfFile(null);
    } finally { setUploadingPdf(false); }
  };

  const handleCoverSelect = async (file) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) { toast.error('Only image files are allowed'); return; }
    if (file.size > 5 * 1024 * 1024) { toast.error('Cover image too large (max 5 MB)'); return; }
    setCoverFile(file);
    setUploadingCover(true);
    try {
      const res = await adminUploadDocumentCover(adminToken, file);
      setCoverUrl(res.data.url);
      toast.success('Cover uploaded');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Cover upload failed');
      setCoverFile(null);
    } finally { setUploadingCover(false); }
  };

  const handleSave = async () => {
    if (!title.trim()) { toast.error('Title is required'); return; }
    if (!pdfUrl) { toast.error('Please upload a PDF file'); return; }
    setSaving(true);
    const payload = {
      title: title.trim(),
      description: description.trim() || null,
      category: activeCategory.trim() || null,
      status,
      pdf_url: pdfUrl,
      pdf_filename: pdfFilename,
      pdf_size_bytes: pdfSizeBytes,
      cover_url: coverUrl || null,
    };
    try {
      let res;
      if (isNew) {
        res = await adminCreateDocument(adminToken, payload);
      } else {
        res = await adminUpdateDocument(adminToken, doc.id, payload);
      }
      toast.success(isNew ? 'Document created' : 'Document updated');
      onSaved(res.data);
      onClose();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Save failed');
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg bg-white rounded-2xl shadow-2xl border border-gray-100 overflow-hidden flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h2 className="font-semibold text-gray-900 text-sm">{isNew ? 'New Document' : 'Edit Document'}</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {/* PDF Upload */}
          <div>
            <label className="text-xs font-medium text-gray-600 mb-2 block">PDF File {isNew && <span className="text-red-500">*</span>}</label>
            <input ref={pdfRef} type="file" accept=".pdf,application/pdf" className="hidden" onChange={e => handlePdfSelect(e.target.files?.[0])} />
            {pdfUrl ? (
              <div className="flex items-center gap-3 p-3 rounded-xl bg-emerald-50 border border-emerald-200">
                <FileText size={18} className="text-emerald-600 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-emerald-800 truncate">{pdfFilename}</p>
                  <p className="text-[11px] text-emerald-600">{formatBytes(pdfSizeBytes)}</p>
                </div>
                <button onClick={() => pdfRef.current?.click()} className="text-xs text-emerald-700 underline font-medium hover:text-emerald-900">Replace</button>
              </div>
            ) : (
              <button
                onClick={() => pdfRef.current?.click()}
                disabled={uploadingPdf}
                className="w-full flex flex-col items-center justify-center gap-2 p-6 rounded-xl border-2 border-dashed border-gray-200 hover:border-violet-400 hover:bg-violet-50 transition-all text-gray-400 hover:text-violet-600"
              >
                {uploadingPdf ? <Loader2 size={20} className="animate-spin" /> : <Upload size={20} />}
                <span className="text-xs font-medium">{uploadingPdf ? 'Uploading…' : 'Click to upload PDF'}</span>
                <span className="text-[11px]">Max 50 MB</span>
              </button>
            )}
          </div>

          {/* Title */}
          <div>
            <label className="text-xs font-medium text-gray-600 mb-1.5 block">Title <span className="text-red-500">*</span></label>
            <Input value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Class 12 Physics Notes" />
          </div>

          {/* Description */}
          <div>
            <label className="text-xs font-medium text-gray-600 mb-1.5 block">Description <span className="text-gray-400 font-normal">(optional)</span></label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Brief description of the document…"
              rows={3}
              className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:bg-white focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 outline-none resize-none transition-all"
            />
          </div>

          {/* Category */}
          <div>
            <label className="text-xs font-medium text-gray-600 mb-1.5 block">Category <span className="text-gray-400 font-normal">(optional)</span></label>
            <select
              value={category}
              onChange={e => setCategory(e.target.value)}
              className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 focus:bg-white focus:border-violet-400 outline-none cursor-pointer"
            >
              <option value="">No category</option>
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
              <option value="__custom__">+ New category…</option>
            </select>
            {category === '__custom__' && (
              <Input
                className="mt-2"
                value={customCategory}
                onChange={e => setCustomCategory(e.target.value)}
                placeholder="Enter category name"
              />
            )}
          </div>

          {/* Status */}
          <div>
            <label className="text-xs font-medium text-gray-600 mb-1.5 block">Status</label>
            <div className="flex gap-2">
              {['draft', 'published'].map(s => (
                <button
                  key={s}
                  onClick={() => setStatus(s)}
                  className={`flex-1 py-2 rounded-xl text-xs font-semibold border transition-all ${
                    status === s
                      ? s === 'published'
                        ? 'bg-emerald-600 text-white border-emerald-600'
                        : 'bg-gray-700 text-white border-gray-700'
                      : 'border-gray-200 text-gray-500 hover:bg-gray-50'
                  }`}
                >
                  {s === 'published' ? '● Published' : '○ Draft'}
                </button>
              ))}
            </div>
          </div>

          {/* Cover image */}
          <div>
            <label className="text-xs font-medium text-gray-600 mb-2 block">Cover Image <span className="text-gray-400 font-normal">(optional)</span></label>
            <input ref={coverRef} type="file" accept="image/*" className="hidden" onChange={e => handleCoverSelect(e.target.files?.[0])} />
            {coverUrl ? (
              <div className="flex items-center gap-3 p-3 rounded-xl bg-blue-50 border border-blue-200">
                <img src={coverUrl} alt="Cover" className="w-12 h-12 rounded-lg object-cover flex-shrink-0 border border-blue-200" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-blue-800">Cover uploaded</p>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => coverRef.current?.click()} className="text-xs text-blue-700 underline font-medium hover:text-blue-900">Replace</button>
                  <button onClick={() => { setCoverUrl(''); setCoverFile(null); }} className="text-xs text-red-500 underline font-medium">Remove</button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => coverRef.current?.click()}
                disabled={uploadingCover}
                className="w-full flex items-center justify-center gap-2 py-3 rounded-xl border-2 border-dashed border-gray-200 hover:border-blue-400 hover:bg-blue-50 transition-all text-gray-400 hover:text-blue-600 text-xs font-medium"
              >
                {uploadingCover ? <Loader2 size={16} className="animate-spin" /> : <Image size={16} />}
                {uploadingCover ? 'Uploading…' : 'Upload cover image (max 5 MB)'}
              </button>
            )}
          </div>
        </div>

        <div className="flex gap-3 px-6 py-4 border-t border-gray-100 bg-gray-50">
          <Button variant="outline" className="flex-1" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button className="flex-1 bg-violet-600 hover:bg-violet-700 text-white" onClick={handleSave} disabled={saving || uploadingPdf || uploadingCover}>
            {saving ? <Loader2 size={14} className="animate-spin mr-1.5" /> : null}
            {isNew ? 'Create Document' : 'Save Changes'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Delete confirmation ───────────────────────────────────────────────────────
function DeleteModal({ doc, adminToken, onClose, onDeleted }) {
  const [deleting, setDeleting] = useState(false);
  const handleDelete = async () => {
    setDeleting(true);
    try {
      await adminDeleteDocument(adminToken, doc.id);
      toast.success('Document deleted');
      onDeleted(doc.id);
      onClose();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Delete failed');
    } finally { setDeleting(false); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="w-full max-w-sm bg-white rounded-2xl shadow-2xl border border-gray-100 p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-red-50 flex items-center justify-center flex-shrink-0">
            <AlertTriangle size={18} className="text-red-500" />
          </div>
          <div>
            <h3 className="font-semibold text-gray-900 text-sm">Delete Document</h3>
            <p className="text-gray-400 text-xs mt-0.5 line-clamp-1">{doc.title}</p>
          </div>
        </div>
        <p className="text-sm text-gray-600 mb-5">This will permanently delete the document and remove its files from storage. This cannot be undone.</p>
        <div className="flex gap-3">
          <Button variant="outline" className="flex-1" onClick={onClose} disabled={deleting}>Cancel</Button>
          <Button className="flex-1 bg-red-600 hover:bg-red-700 text-white" onClick={handleDelete} disabled={deleting}>
            {deleting ? <Loader2 size={14} className="animate-spin mr-1.5" /> : null}
            Delete
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function AdminDocuments({ adminToken }) {
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [inputQ, setInputQ] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [sort, setSort] = useState('newest');
  const [categories, setCategories] = useState([]);
  const [modal, setModal] = useState(null); // null | { type: 'new' | 'edit' | 'delete', doc?: object }
  const searchTimeout = useRef(null);
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const load = useCallback(async (pg, query, status, cat, srt) => {
    setLoading(true);
    try {
      const res = await adminGetDocuments(adminToken, {
        q: query || undefined,
        status: status !== 'all' ? status : undefined,
        category: cat || undefined,
        sort: srt,
        limit: PAGE_SIZE,
        offset: pg * PAGE_SIZE,
      });
      setDocs(res.data.items);
      setTotal(res.data.total);
    } catch (e) {
      toast.error('Failed to load documents');
    } finally { setLoading(false); }
  }, [adminToken]);

  const loadCategories = useCallback(async () => {
    try {
      const res = await adminGetDocumentCategories(adminToken);
      setCategories(res.data.categories || []);
    } catch { }
  }, [adminToken]);

  useEffect(() => { loadCategories(); }, [loadCategories]);
  useEffect(() => { load(page, q, statusFilter, categoryFilter, sort); }, [page, q, statusFilter, categoryFilter, sort, load]);

  const handleSearch = (val) => {
    setInputQ(val);
    clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => { setPage(0); setQ(val); }, 350);
  };

  const onSaved = (savedDoc) => {
    setDocs(prev => {
      const idx = prev.findIndex(d => d.id === savedDoc.id);
      if (idx === -1) return [savedDoc, ...prev];
      const next = [...prev];
      next[idx] = savedDoc;
      return next;
    });
    loadCategories();
    // Reload to get accurate total
    load(page, q, statusFilter, categoryFilter, sort);
  };

  const onDeleted = (id) => {
    setDocs(prev => prev.filter(d => d.id !== id));
    setTotal(t => t - 1);
  };

  const toggleStatus = async (doc) => {
    const newStatus = doc.status === 'published' ? 'draft' : 'published';
    try {
      const res = await adminUpdateDocument(adminToken, doc.id, { status: newStatus });
      setDocs(prev => prev.map(d => d.id === doc.id ? res.data : d));
      toast.success(`${newStatus === 'published' ? 'Published' : 'Unpublished'}: ${doc.title}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to update status');
    }
  };

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-bold text-gray-900">Documents & Library</h1>
          <p className="text-xs text-gray-400 mt-0.5">{total} document{total !== 1 ? 's' : ''} total</p>
        </div>
        <Button
          onClick={() => setModal({ type: 'new' })}
          className="bg-violet-600 hover:bg-violet-700 text-white flex items-center gap-1.5 text-sm"
        >
          <Plus size={15} /> New Document
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <div className="relative flex-1 min-w-48">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <Input
            className="pl-8 text-xs"
            placeholder="Search by title…"
            value={inputQ}
            onChange={e => handleSearch(e.target.value)}
          />
        </div>
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value); setPage(0); }}
          className="px-2.5 py-1.5 rounded-xl border border-gray-200 bg-gray-50 text-xs text-gray-700 focus:border-violet-400 outline-none cursor-pointer"
        >
          <option value="all">All status</option>
          <option value="published">Published</option>
          <option value="draft">Draft</option>
        </select>
        <select
          value={categoryFilter}
          onChange={e => { setCategoryFilter(e.target.value); setPage(0); }}
          className="px-2.5 py-1.5 rounded-xl border border-gray-200 bg-gray-50 text-xs text-gray-700 focus:border-violet-400 outline-none cursor-pointer"
        >
          <option value="">All categories</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select
          value={sort}
          onChange={e => { setSort(e.target.value); setPage(0); }}
          className="px-2.5 py-1.5 rounded-xl border border-gray-200 bg-gray-50 text-xs text-gray-700 focus:border-violet-400 outline-none cursor-pointer"
        >
          <option value="newest">Newest</option>
          <option value="oldest">Oldest</option>
          <option value="title_asc">Title A–Z</option>
          <option value="title_desc">Title Z–A</option>
        </select>
        <button
          onClick={() => load(page, q, statusFilter, categoryFilter, sort)}
          className="p-1.5 rounded-xl border border-gray-200 text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors"
          title="Refresh"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Table */}
      <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden shadow-sm">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <Loader2 size={20} className="animate-spin mr-2" /> Loading documents…
          </div>
        ) : docs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <FileText size={28} className="text-gray-300 mb-3" />
            <p className="text-gray-500 text-sm font-medium">No documents found</p>
            <p className="text-gray-400 text-xs mt-1">Upload your first document to get started</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="text-left px-4 py-3 font-semibold text-gray-500 w-12">Cover</th>
                  <th className="text-left px-4 py-3 font-semibold text-gray-500">Title</th>
                  <th className="text-left px-4 py-3 font-semibold text-gray-500 hidden sm:table-cell">Category</th>
                  <th className="text-left px-4 py-3 font-semibold text-gray-500 hidden md:table-cell">Size</th>
                  <th className="text-left px-4 py-3 font-semibold text-gray-500 hidden lg:table-cell">Uploaded</th>
                  <th className="text-left px-4 py-3 font-semibold text-gray-500">Status</th>
                  <th className="text-right px-4 py-3 font-semibold text-gray-500">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {docs.map(doc => (
                  <tr key={doc.id} className="hover:bg-gray-50/50 transition-colors">
                    <td className="px-4 py-3">
                      {doc.cover_url ? (
                        <img src={doc.cover_url} alt="" className="w-8 h-10 rounded-lg object-cover border border-gray-100" />
                      ) : (
                        <div className="w-8 h-10 rounded-lg bg-violet-50 flex items-center justify-center border border-gray-100">
                          <FileText size={13} className="text-violet-400" />
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-semibold text-gray-800 leading-tight max-w-xs truncate">{doc.title}</p>
                      {doc.description && <p className="text-gray-400 text-[11px] mt-0.5 truncate max-w-xs">{doc.description}</p>}
                    </td>
                    <td className="px-4 py-3 hidden sm:table-cell">
                      {doc.category ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-violet-50 text-violet-600 font-medium text-[11px]">
                          {doc.category}
                        </span>
                      ) : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-4 py-3 hidden md:table-cell text-gray-500">{formatBytes(doc.pdf_size_bytes)}</td>
                    <td className="px-4 py-3 hidden lg:table-cell text-gray-400">{formatDate(doc.created_at)}</td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => toggleStatus(doc)}
                        className={`inline-flex items-center gap-1 px-2 py-1 rounded-full font-semibold text-[11px] transition-all ${
                          doc.status === 'published'
                            ? 'bg-emerald-50 text-emerald-600 hover:bg-emerald-100'
                            : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                        }`}
                        title={doc.status === 'published' ? 'Click to unpublish' : 'Click to publish'}
                      >
                        {doc.status === 'published' ? <Eye size={10} /> : <EyeOff size={10} />}
                        {doc.status === 'published' ? 'Published' : 'Draft'}
                      </button>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1.5">
                        <a
                          href={doc.pdf_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="p-1.5 rounded-lg text-gray-400 hover:text-violet-600 hover:bg-violet-50 transition-colors"
                          title="View PDF"
                        >
                          <Download size={14} />
                        </a>
                        <button
                          onClick={() => setModal({ type: 'edit', doc })}
                          className="p-1.5 rounded-lg text-gray-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
                          title="Edit"
                        >
                          <Edit2 size={14} />
                        </button>
                        <button
                          onClick={() => setModal({ type: 'delete', doc })}
                          className="p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                          title="Delete"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            className="flex items-center gap-1 px-3 py-1.5 rounded-xl border border-gray-200 text-xs text-gray-600 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 transition-colors"
          >
            <ChevronLeft size={13} /> Prev
          </button>
          <span className="text-xs text-gray-500 px-2">Page {page + 1} of {totalPages}</span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="flex items-center gap-1 px-3 py-1.5 rounded-xl border border-gray-200 text-xs text-gray-600 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 transition-colors"
          >
            Next <ChevronRight size={13} />
          </button>
        </div>
      )}

      {/* Modals */}
      {modal?.type === 'new' && (
        <DocumentModal adminToken={adminToken} onClose={() => setModal(null)} onSaved={onSaved} categories={categories} />
      )}
      {modal?.type === 'edit' && (
        <DocumentModal doc={modal.doc} adminToken={adminToken} onClose={() => setModal(null)} onSaved={onSaved} categories={categories} />
      )}
      {modal?.type === 'delete' && (
        <DeleteModal doc={modal.doc} adminToken={adminToken} onClose={() => setModal(null)} onDeleted={onDeleted} />
      )}
    </div>
  );
}
