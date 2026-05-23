import { useMemo } from 'react';
import { FileText } from 'lucide-react';
import CmsDocCard from './CmsDocCard';
import { useCmsLibrary } from '@/hooks/useContent';

export default function CmsDocsSection({ board, classSlug }) {
  const { data: docs = [], isLoading } = useCmsLibrary();

  const filtered = useMemo(() => {
    let result = docs;
    if (board) result = result.filter(d => d.board_slug === board);
    if (classSlug) result = result.filter(d => d.class_slug === classSlug);
    return result;
  }, [docs, board, classSlug]);

  if (isLoading || filtered.length === 0) return null;
  return (
    <div className="w-full max-w-6xl mx-auto px-4 md:px-6 pb-8">
      <div className="flex items-center gap-2 mb-4 mt-2">
        <FileText size={16} className="text-violet-400" />
        <h2 className="text-base font-semibold text-foreground">Study Resources</h2>
        <span className="ml-1 px-2 py-0.5 rounded-full text-[10px] font-medium" style={{ background: 'rgba(139,92,246,0.12)', color: '#a78bfa' }}>{filtered.length}</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {filtered.slice(0, 9).map(doc => <CmsDocCard key={doc.id} doc={doc} />)}
      </div>
    </div>
  );
}
