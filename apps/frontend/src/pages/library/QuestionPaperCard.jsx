import { memo, useState } from 'react';
import { Eye } from 'lucide-react';

const QuestionPaperCard = memo(function QuestionPaperCard({ paper, onOpen }) {
  const [imgFailed, setImgFailed] = useState(false);

  const handleClick = (e) => {
    e.preventDefault();
    if (onOpen) onOpen(paper);
  };

  return (
    <button
      onClick={handleClick}
      className="group flex flex-col rounded-2xl overflow-hidden border transition-all duration-200 hover:border-violet-500/30 text-left w-full"
      style={{
        background: 'var(--card)',
        border: '1px solid rgba(139,92,246,0.10)',
        boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
      }}
    >
      {paper.image_url && !imgFailed && !paper.is_pdf && (
        <div
          className="overflow-hidden flex-shrink-0"
          style={{ height: '160px', background: 'rgba(0,0,0,0.18)' }}
        >
          <img
            src={paper.image_url}
            alt={paper.title}
            className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
            loading="lazy"
            onError={() => setImgFailed(true)}
          />
        </div>
      )}
      {paper.is_pdf && (
        <div
          className="flex items-center justify-center flex-shrink-0"
          style={{ height: '100px', background: 'rgba(139,92,246,0.08)' }}
        >
          <span className="text-3xl select-none" aria-hidden="true">📄</span>
        </div>
      )}
      <div className="p-4 flex flex-col flex-1 gap-3">
        <div className="flex flex-wrap gap-1.5">
          {paper.board && (
            <span
              className="px-2 py-0.5 rounded-full text-[10px] font-medium"
              style={{ background: 'rgba(139,92,246,0.10)', color: '#a78bfa' }}
            >
              {paper.board}
            </span>
          )}
          {paper.year && (
            <span
              className="px-2 py-0.5 rounded-full text-[10px] font-medium"
              style={{ background: 'rgba(16,185,129,0.12)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.2)' }}
            >
              {paper.year}
            </span>
          )}
          {paper.class_level && (
            <span
              className="px-2 py-0.5 rounded-full text-[10px] font-medium"
              style={{ background: 'rgba(59,130,246,0.10)', color: '#93c5fd' }}
            >
              Class {paper.class_level}
            </span>
          )}
          {paper.is_pdf && (
            <span
              className="px-2 py-0.5 rounded-full text-[10px] font-medium"
              style={{ background: 'rgba(239,68,68,0.10)', color: '#fca5a5' }}
            >
              PDF
            </span>
          )}
        </div>
        <h3 className="text-sm font-semibold text-foreground leading-snug group-hover:text-violet-300 transition-colors line-clamp-2">
          {paper.title}
        </h3>
        {paper.subject && (
          <p className="text-xs text-muted-foreground">{paper.subject}</p>
        )}
        <div className="flex items-center gap-2 mt-auto pt-2 border-t border-white/[0.06]">
          <span className="ml-auto flex items-center gap-1 text-[10px] text-violet-400 font-medium group-hover:gap-2 transition-all">
            View Paper <Eye size={10} />
          </span>
        </div>
      </div>
    </button>
  );
});

export default QuestionPaperCard;
