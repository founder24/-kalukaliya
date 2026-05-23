import { Link } from 'react-router-dom';
import { BookText, Clock, ArrowRight } from 'lucide-react';

export default function CmsPostCard({ post }) {
  const to = `/subject/${post.subject_id}`;
  const mins = post.word_count ? Math.max(1, Math.ceil(post.word_count / 200)) : null;
  return (
    <Link
      to={to}
      className="block rounded-2xl overflow-hidden border transition-all duration-200 hover:-translate-y-0.5"
      style={{ background: '#1a1a1a', border: '1px solid rgba(149,117,224,0.10)', boxShadow: '0 4px 20px rgba(0,0,0,0.30)' }}
    >
      <div className="p-4 flex flex-col gap-2">
        <div className="flex items-start gap-2">
          <BookText size={14} className="text-violet-400 shrink-0 mt-0.5" />
          <h3 className="text-sm font-semibold leading-snug line-clamp-2" style={{ color: '#E8E8E8' }}>{post.title || 'Untitled Post'}</h3>
        </div>
        {post.word_count > 0 && (
          <div className="flex items-center gap-3 text-[10px]" style={{ color: 'rgba(232,232,232,0.35)' }}>
            {mins && <span className="flex items-center gap-1"><Clock size={9} />{mins} min</span>}
            <span>{post.word_count.toLocaleString()} words</span>
          </div>
        )}
        <div className="flex items-center justify-between mt-1">
          {post.board_slug && (
            <span className="px-2 py-0.5 rounded-full text-[9px] font-medium uppercase tracking-wide" style={{ background: 'rgba(149,117,224,0.12)', color: '#a78bfa' }}>
              {post.board_slug}
            </span>
          )}
          <span className="ml-auto flex items-center gap-1 text-[10px] font-medium" style={{ color: '#9575e0' }}>
            Read <ArrowRight size={10} />
          </span>
        </div>
      </div>
    </Link>
  );
}
