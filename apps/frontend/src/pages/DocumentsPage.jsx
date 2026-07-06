import { useState, useEffect, useCallback, useRef } from 'react';
import { FileText, Download, ExternalLink, Search, ChevronLeft, ChevronRight, BookOpen, Filter, X, Loader2 } from 'lucide-react';
import { publicGetDocuments, publicGetDocumentCategories, publicGetDocumentDownloadUrl } from '@/utils/api';

const PAGE_SIZE = 12;

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

function DocumentCard({ doc }) {
  const [downloading, setDownloading] = useState(false);

  const handleOpen = async (mode) => {
    setDownloading(true);
    try {
      const res = await publicGetDocumentDownloadUrl(doc.id);
      const { download_url, filename } = res.data;
      if (mode === 'view') {
        window.open(download_url, '_blank', 'noopener,noreferrer');
      } else {
        // Trigger download
        const a = document.createElement('a');
        a.href = download_url;
        a.download = filename || doc.pdf_filename || 'document.pdf';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      }
    } catch {
      // Silently ignore — auth errors redirect via AuthGuard already
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="group bg-white rounded-2xl border border-gray-100 shadow-sm hover:shadow-md transition-all duration-200 overflow-hidden flex flex-col">
      {/* Cover image or placeholder */}
      <div className="relative aspect-[3/2] bg-gradient-to-br from-violet-50 to-indigo-100 overflow-hidden flex-shrink-0">
        {doc.cover_url ? (
          <img
            src={doc.cover_url}
            alt={doc.title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <FileText size={40} className="text-violet-300" />
          </div>
        )}
        {doc.category && (
          <span className="absolute top-2 left-2 text-[10px] font-semibold bg-white/90 text-violet-700 rounded-full px-2 py-0.5 backdrop-blur-sm">
            {doc.category}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex flex-col flex-1 p-4 gap-2">
        <h3 className="text-gray-900 font-semibold text-sm leading-tight line-clamp-2 group-hover:text-violet-700 transition-colors">
          {doc.title}
        </h3>
        {doc.description && (
          <p className="text-gray-400 text-xs leading-relaxed line-clamp-2">{doc.description}</p>
        )}
        <div className="flex items-center gap-2 text-[11px] text-gray-400 mt-auto pt-2 border-t border-gray-50">
          <span>{formatBytes(doc.pdf_size_bytes)}</span>
          <span>·</span>
          <span>{formatDate(doc.created_at)}</span>
        </div>

        {/* Actions */}
        <div className="flex gap-2 mt-1">
          <button
            onClick={() => handleOpen('view')}
            disabled={downloading}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-xl text-xs font-semibold bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-60 transition-colors"
          >
            {downloading ? <Loader2 size={13} className="animate-spin" /> : <ExternalLink size={13} />}
            View PDF
          </button>
          <button
            onClick={() => handleOpen('download')}
            disabled={downloading}
            className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold border border-gray-200 text-gray-600 hover:bg-gray-50 hover:border-gray-300 disabled:opacity-60 transition-colors"
          >
            <Download size={13} />
          </button>
        </div>
      </div>
    </div>
  );
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [q, setQ] = useState('');
  const [inputQ, setInputQ] = useState('');
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState('newest');
  const [categories, setCategories] = useState([]);
  const searchTimeout = useRef(null);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  const load = useCallback(async (pg, query, cat, srt) => {
    setLoading(true);
    setError(null);
    try {
      const res = await publicGetDocuments({ q: query, category: cat, sort: srt, limit: PAGE_SIZE, offset: pg * PAGE_SIZE });
      setDocs(res.data.items);
      setTotal(res.data.total);
    } catch (e) {
      setError('Failed to load documents. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    publicGetDocumentCategories()
      .then(r => setCategories(r.data.categories || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load(page, q, category, sort);
  }, [page, q, category, sort, load]);

  const handleSearch = (val) => {
    setInputQ(val);
    clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => {
      setPage(0);
      setQ(val);
    }, 350);
  };

  const handleCategory = (val) => { setCategory(val); setPage(0); };
  const handleSort = (val) => { setSort(val); setPage(0); };
  const clearFilters = () => { setQ(''); setInputQ(''); setCategory(''); setSort('newest'); setPage(0); };

  const hasFilters = q || category || sort !== 'newest';

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-100">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-10">
          <div className="flex items-start gap-4">
            <div className="w-12 h-12 rounded-2xl bg-violet-100 flex items-center justify-center flex-shrink-0">
              <BookOpen size={22} className="text-violet-600" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Documents & Library</h1>
              <p className="text-gray-500 text-sm mt-1">Browse and download books, notes, and study materials</p>
            </div>
          </div>

          {/* Search + Filters */}
          <div className="mt-6 flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search documents…"
                value={inputQ}
                onChange={e => handleSearch(e.target.value)}
                className="w-full pl-9 pr-4 py-2.5 rounded-xl border border-gray-200 bg-gray-50 text-sm text-gray-900 placeholder-gray-400 focus:bg-white focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all"
              />
            </div>

            <select
              value={category}
              onChange={e => handleCategory(e.target.value)}
              className="px-3 py-2.5 rounded-xl border border-gray-200 bg-gray-50 text-sm text-gray-700 focus:bg-white focus:border-violet-400 outline-none cursor-pointer"
            >
              <option value="">All categories</option>
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>

            <select
              value={sort}
              onChange={e => handleSort(e.target.value)}
              className="px-3 py-2.5 rounded-xl border border-gray-200 bg-gray-50 text-sm text-gray-700 focus:bg-white focus:border-violet-400 outline-none cursor-pointer"
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="title_asc">Title A–Z</option>
              <option value="title_desc">Title Z–A</option>
            </select>

            {hasFilters && (
              <button onClick={clearFilters} className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl border border-gray-200 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-50 transition-colors">
                <X size={14} /> Clear
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* Result count */}
        {!loading && !error && (
          <p className="text-xs text-gray-400 mb-5">
            {total === 0 ? 'No documents found' : `${total} document${total !== 1 ? 's' : ''}`}
          </p>
        )}

        {error && (
          <div className="rounded-xl bg-red-50 border border-red-100 text-red-600 text-sm px-4 py-3 mb-6">
            {error}
          </div>
        )}

        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="bg-white rounded-2xl border border-gray-100 overflow-hidden animate-pulse">
                <div className="aspect-[3/2] bg-gray-100" />
                <div className="p-4 space-y-2">
                  <div className="h-3.5 bg-gray-100 rounded-full w-3/4" />
                  <div className="h-3 bg-gray-100 rounded-full w-1/2" />
                  <div className="h-8 bg-gray-100 rounded-xl mt-4" />
                </div>
              </div>
            ))}
          </div>
        ) : docs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="w-16 h-16 rounded-2xl bg-gray-100 flex items-center justify-center mb-4">
              <FileText size={28} className="text-gray-300" />
            </div>
            <p className="text-gray-500 font-medium">No documents found</p>
            <p className="text-gray-400 text-sm mt-1">
              {hasFilters ? 'Try adjusting your search or filters' : 'No documents have been published yet'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {docs.map(doc => <DocumentCard key={doc.id} doc={doc} />)}
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 mt-10">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="flex items-center gap-1 px-3 py-2 rounded-xl border border-gray-200 text-sm text-gray-600 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 transition-colors"
            >
              <ChevronLeft size={15} /> Prev
            </button>
            <span className="text-sm text-gray-500 px-2">
              Page {page + 1} of {totalPages}
            </span>
            <button
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="flex items-center gap-1 px-3 py-2 rounded-xl border border-gray-200 text-sm text-gray-600 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 transition-colors"
            >
              Next <ChevronRight size={15} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
