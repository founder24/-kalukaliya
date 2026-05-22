import { useState, useCallback } from 'react';
import { BookText, Loader2 } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/utils/api';
import CmsPostCard from './CmsPostCard';

const POSTS_PER_PAGE = 12;

const fetchCmsPosts = ({ limit, skip, board, classSlug }) => {
  const params = new URLSearchParams({ limit: String(limit), skip: String(skip) });
  if (board) params.append('board', board);
  if (classSlug) params.append('class_slug', classSlug);
  return apiClient().get(`/cms/posts?${params}`).then((r) => r.data);
};

export default function CmsPostsGrid({ board, classSlug }) {
  const [pages, setPages] = useState([0]);
  const queryClient = useQueryClient();

  const { data: firstPageData, isLoading } = useQuery({
    queryKey: ['cms-posts', board || '', classSlug || '', 0],
    queryFn: () => fetchCmsPosts({ limit: POSTS_PER_PAGE, skip: 0, board, classSlug }),
    staleTime: 30 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
  });

  const total = firstPageData?.total || 0;

  const allItems = pages.flatMap((skip) => {
    const cached = queryClient.getQueryData(['cms-posts', board || '', classSlug || '', skip]);
    return cached?.items || [];
  });

  const [loadingMore, setLoadingMore] = useState(false);
  const hasMore = allItems.length < total;

  const handleLoadMore = useCallback(async () => {
    const nextSkip = allItems.length;
    setLoadingMore(true);
    try {
      const data = await queryClient.fetchQuery({
        queryKey: ['cms-posts', board || '', classSlug || '', nextSkip],
        queryFn: () => fetchCmsPosts({ limit: POSTS_PER_PAGE, skip: nextSkip, board, classSlug }),
        staleTime: 30 * 60 * 1000,
      });
      if (data) {
        setPages(prev => [...prev, nextSkip]);
      }
    } finally {
      setLoadingMore(false);
    }
  }, [allItems.length, board, classSlug, queryClient]);

  if (!isLoading && allItems.length === 0) return null;

  return (
    <div className="w-full max-w-6xl mx-auto px-4 md:px-6 pb-10">
      <div className="flex items-center gap-2 mb-4 mt-2">
        <BookText size={16} className="text-violet-400" />
        <h2 className="text-base font-semibold text-foreground">Subject Blog Posts</h2>
        {total > 0 && (
          <span className="ml-1 px-2 py-0.5 rounded-full text-[10px] font-medium" style={{ background: 'rgba(149,117,224,0.12)', color: '#a78bfa' }}>{total}</span>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {allItems.map((post, index) => (
          <CmsPostCard key={post.id || post._id || `${post.subject_id}-${index}`} post={post} />
        ))}
      </div>
      {(isLoading || loadingMore) && (
        <div className="flex justify-center py-6">
          <Loader2 size={20} className="animate-spin text-violet-400" />
        </div>
      )}
      {hasMore && !isLoading && !loadingMore && (
        <div className="flex justify-center pt-6">
          <button
            onClick={handleLoadMore}
            className="px-5 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 active:scale-95"
            style={{
              color: '#a78bfa',
              background: 'rgba(139,92,246,0.08)',
              border: '1px solid rgba(139,92,246,0.20)',
            }}
          >
            Load more posts
          </button>
        </div>
      )}
      {!hasMore && allItems.length > 0 && !isLoading && (
        <p className="text-center text-xs py-4" style={{ color: 'rgba(232,232,232,0.25)' }}>All {total} posts loaded</p>
      )}
    </div>
  );
}
