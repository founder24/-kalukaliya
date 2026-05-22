import { memo } from 'react';
import { Link } from 'react-router-dom';
import { Clock, ArrowRight } from 'lucide-react';
import { cdnImage } from '@/utils/imageCdn';

const CmsDocCard = memo(function CmsDocCard({ doc }) {
  const tags = doc.seo_tags ? doc.seo_tags.split(',').map(t => t.trim()).filter(Boolean).slice(0, 3) : [];
  return (
    <Link
      to={`/learn/${doc.seo_slug || doc.id}`}
      className="group flex flex-col rounded-2xl overflow-hidden border transition-all duration-200 hover:border-violet-500/30"
      style={{
        background: 'var(--card)',
        border: '1px solid rgba(139,92,246,0.10)',
        boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
      }}
    >
      {doc.thumbnail_url && (
        <div className="w-full aspect-video overflow-hidden">
          <img src={cdnImage(doc.thumbnail_url, { width: 400, format: 'webp' })} alt={doc.alt_text || doc.title} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" loading="lazy" width="400" height="225" />
        </div>
      )}
      <div className="p-4 flex flex-col flex-1 gap-3">
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {tags.map(t => (
              <span
                key={t}
                className="px-2 py-0.5 rounded-full text-[10px] font-medium"
                style={t.toLowerCase() === 'syllabus'
                  ? { background: 'rgba(16,185,129,0.12)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.2)' }
                  : { background: 'rgba(139,92,246,0.10)', color: '#a78bfa' }
                }
              >{t}</span>
            ))}
          </div>
        )}
        <h3 className="text-sm font-semibold text-foreground leading-snug group-hover:text-violet-300 transition-colors line-clamp-2">{doc.title}</h3>
        {doc.meta_description && (
          <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">{doc.meta_description}</p>
        )}
        <div className="flex items-center gap-3 mt-auto pt-2 border-t border-white/[0.06]">
          {doc.word_count > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
              <Clock size={10} /> {Math.max(1, Math.ceil(doc.word_count / 200))} min
            </span>
          )}
          <span className="ml-auto flex items-center gap-1 text-[10px] text-violet-400 font-medium group-hover:gap-2 transition-all">
            Read <ArrowRight size={10} />
          </span>
        </div>
      </div>
    </Link>
  );
});

export default CmsDocCard;
