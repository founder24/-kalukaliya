import { memo } from 'react';
import { ExternalLink } from 'lucide-react';

const QuestionPaperCard = memo(function QuestionPaperCard({ paper }) {
  return (
    <a
      href={paper.image_url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex flex-col rounded-2xl overflow-hidden border transition-all duration-200 hover:border-violet-500/30"
      style={{
        background: 'var(--card)',
        border: '1px solid rgba(139,92,246,0.10)',
        boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
      }}
    >
      <div className="p-4 flex flex-col flex-1 gap-3">
        <div className="flex flex-wrap gap-1.5">
          <span
            className="px-2 py-0.5 rounded-full text-[10px] font-medium"
            style={{ background: 'rgba(139,92,246,0.10)', color: '#a78bfa' }}
          >
            {paper.board}
          </span>
          {paper.year && (
            <span
              className="px-2 py-0.5 rounded-full text-[10px] font-medium"
              style={{ background: 'rgba(16,185,129,0.12)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.2)' }}
            >
              {paper.year}
            </span>
          )}
          <span
            className="px-2 py-0.5 rounded-full text-[10px] font-medium"
            style={{ background: 'rgba(59,130,246,0.10)', color: '#93c5fd' }}
          >
            Class {paper.class_level}
          </span>
        </div>
        <h3 className="text-sm font-semibold text-foreground leading-snug group-hover:text-violet-300 transition-colors line-clamp-2">
          {paper.title}
        </h3>
        <p className="text-xs text-muted-foreground">{paper.subject}</p>
        <div className="flex items-center gap-2 mt-auto pt-2 border-t border-white/[0.06]">
          <span className="ml-auto flex items-center gap-1 text-[10px] text-violet-400 font-medium group-hover:gap-2 transition-all">
            View Paper <ExternalLink size={10} />
          </span>
        </div>
      </div>
    </a>
  );
});

export default QuestionPaperCard;
