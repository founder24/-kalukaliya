import { FileText } from 'lucide-react';
import QuestionPaperCard from './QuestionPaperCard';
import { useQuestionPapers } from '@/hooks/useContent';

export default function QuestionPapersSection() {
  const { data: papers = [], isLoading } = useQuestionPapers();

  if (isLoading || papers.length === 0) return null;

  const displayed = papers.slice(0, 6);

  return (
    <div className="w-full max-w-6xl mx-auto px-4 md:px-6 pb-8">
      <div className="flex items-center gap-2 mb-4 mt-2">
        <FileText size={16} className="text-violet-400" />
        <h2 className="text-base font-semibold text-foreground">Question Papers</h2>
        <span
          className="ml-1 px-2 py-0.5 rounded-full text-[10px] font-medium"
          style={{ background: 'rgba(139,92,246,0.12)', color: '#a78bfa' }}
        >
          {papers.length}
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {displayed.map((paper) => (
          <QuestionPaperCard key={paper.id} paper={paper} />
        ))}
      </div>
      {papers.length > 6 && (
        <div className="mt-4 text-center">
          <span className="text-xs text-muted-foreground">
            Showing 6 of {papers.length} papers
          </span>
        </div>
      )}
    </div>
  );
}
