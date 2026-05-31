/**
 * useContent.js — React Query v5 hooks for all content fetching.
 * Mirrors the spec: useSubjects, useBoards, useClasses, useStreams, useSavedSubjects
 */
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/utils/api';

// ── Static-first helper ──────────────────────────────────────────────────────

/**
 * Static-first fetcher: tries /static/<file>.json from CDN first,
 * falls back to the live API if the static file is unavailable.
 */
const staticFirst = (staticPath, apiPath) => async () => {
  try {
    const res = await fetch(staticPath);
    if (res.ok) return res.json();
  } catch {
    // static file unavailable, fall through to API
  }
  return apiClient().get(apiPath).then((r) => r.data);
};

// ── Raw fetchers ────────────────────────────────────────────────────────────
const fetchBoards = staticFirst('/static/boards.json', '/content/boards');

const fetchClasses = staticFirst('/static/classes.json', '/content/classes');

const fetchStreams = staticFirst('/static/streams.json', '/content/streams');

const fetchSubjects = staticFirst('/static/subjects.json', '/content/subjects');

const fetchLibraryBundle = staticFirst('/static/library-bundle.json', '/content/library-bundle');

const fetchLibraryBundleSlim = staticFirst('/static/library-bundle-slim.json', '/content/library-bundle?slim=1');

const fetchLibraryBundleBoot = (boardId) =>
  apiClient()
    .get(`/content/library-bundle?boot=${encodeURIComponent(boardId)}`)
    .then((r) => r.data);

const fetchSubject = (id) =>
  apiClient().get(`/content/subjects/${id}`).then((r) => r.data);

const fetchChapters = (subjectId) =>
  apiClient().get(`/content/chapters/${subjectId}`).then((r) => r.data);

const fetchChunks = (chapterId) =>
  apiClient().get(`/content/chunks/${chapterId}`).then((r) => r.data);

const fetchSavedSubjects = () =>
  apiClient().get('/user/profile').then((r) => r.data.saved_subjects || []);

// ── Hooks ────────────────────────────────────────────────────────────────────

/** All boards (30min stale) */
const useBoards = () =>
  useQuery({
    queryKey: ['boards'],
    queryFn: fetchBoards,
    staleTime: 30 * 60 * 1000,
  });

/** All classes (30min stale) */
const useClasses = () =>
  useQuery({
    queryKey: ['classes', undefined],
    queryFn: fetchClasses,
    staleTime: 30 * 60 * 1000,
  });

/** All streams (30min stale) */
const useStreams = () =>
  useQuery({
    queryKey: ['streams', undefined],
    queryFn: fetchStreams,
    staleTime: 30 * 60 * 1000,
  });

/** All published subjects (10min stale) */
const useSubjects = () =>
  useQuery({
    queryKey: ['subjects', undefined],
    queryFn: fetchSubjects,
    staleTime: 10 * 60 * 1000,
  });

/** Single subject detail (10min stale) */
export const useSubject = (id) =>
  useQuery({
    queryKey: ['subject', id],
    queryFn: () => fetchSubject(id),
    staleTime: 10 * 60 * 1000,
    enabled: !!id,
  });

/** Chapters for a subject (10min stale) */
export const useChapters = (subjectId) =>
  useQuery({
    queryKey: ['chapters', subjectId],
    queryFn: () => fetchChapters(subjectId),
    staleTime: 10 * 60 * 1000,
    enabled: !!subjectId,
  });

/** Chunks for a chapter (10min stale) */
const useChunks = (chapterId) =>
  useQuery({
    queryKey: ['chunks', chapterId],
    queryFn: () => fetchChunks(chapterId),
    staleTime: 10 * 60 * 1000,
    enabled: !!chapterId,
  });

/** Saved subject IDs for authenticated user (1min stale) */
export const useSavedSubjects = (user) =>
  useQuery({
    queryKey: ['saved-subjects'],
    queryFn: fetchSavedSubjects,
    staleTime: 1 * 60 * 1000,
    enabled: !!user,
  });

export const useLibraryBundle = (enabled = true) =>
  useQuery({
    queryKey: ['library-bundle'],
    queryFn: fetchLibraryBundle,
    staleTime: 30 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
    enabled,
  });

export const useLibraryBundleSlim = () =>
  useQuery({
    queryKey: ['library-bundle-slim'],
    queryFn: fetchLibraryBundleSlim,
    staleTime: 30 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
    retry: 4,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 15000),
  });

/**
 * Boot-tier library bundle: slim metadata + chapters scoped to ONE board.
 * Used as a payload-light first paint when the active board is known
 * (~150-300KB vs ~1MB for the full bundle). The full bundle is still
 * required for cross-board chapter search and is fetched lazily afterwards.
 */
export const useLibraryBundleBoot = (boardId, enabled = true) =>
  useQuery({
    queryKey: ['library-bundle-boot', boardId],
    queryFn: () => fetchLibraryBundleBoot(boardId),
    staleTime: 30 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
    enabled: !!boardId && enabled,
  });

const fetchResolveSubject = (board, classSlug, subjectSlug) =>
  apiClient().get(`/content/resolve-subject/${board}/${classSlug}/${subjectSlug}`).then((r) => r.data);

const fetchSeoTopics = (board, classSlug, subjectSlug) =>
  apiClient().get(`/seo/topics/${board}/${classSlug}/${subjectSlug}`).then((r) => r.data);

export const useResolveSubject = (board, classSlug, subjectSlug) =>
  useQuery({
    queryKey: ['resolve-subject', board, classSlug, subjectSlug],
    queryFn: () => fetchResolveSubject(board, classSlug, subjectSlug),
    staleTime: 10 * 60 * 1000,
    enabled: !!board && !!classSlug && !!subjectSlug,
  });

const useSeoTopics = (board, classSlug, subjectSlug) =>
  useQuery({
    queryKey: ['seo-topics', board, classSlug, subjectSlug],
    queryFn: () => fetchSeoTopics(board, classSlug, subjectSlug),
    staleTime: 10 * 60 * 1000,
    enabled: !!board && !!classSlug && !!subjectSlug,
  });

export function prefetchSubjectData(queryClient, board, classSlug, subjectSlug) {
  const staleTime = 10 * 60 * 1000;
  queryClient.prefetchQuery({
    queryKey: ['seo-topics', board, classSlug, subjectSlug],
    queryFn: () => fetchSeoTopics(board, classSlug, subjectSlug),
    staleTime,
  });
  queryClient.fetchQuery({
    queryKey: ['resolve-subject', board, classSlug, subjectSlug],
    queryFn: () => fetchResolveSubject(board, classSlug, subjectSlug),
    staleTime,
  }).then((subjectData) => {
    const subjectId = subjectData?.id || subjectData?._id;
    if (subjectId) {
      queryClient.prefetchQuery({
        queryKey: ['chapters', subjectId],
        queryFn: () => fetchChapters(subjectId),
        staleTime,
      });
    }
  }).catch(() => {});
}

const fetchCmsLibrary = () =>
  apiClient().get('/content/cms-library').then((r) => {
    const d = r.data;
    return Array.isArray(d) ? d : [];
  });

export const useCmsLibrary = () =>
  useQuery({
    queryKey: ['cms-library'],
    queryFn: fetchCmsLibrary,
    staleTime: 30 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
  });

const fetchQuestionPapers = () =>
  apiClient().get('/content/question-papers').then((r) => {
    const d = r.data;
    return Array.isArray(d) ? d : [];
  });

export const useQuestionPapers = () =>
  useQuery({
    queryKey: ['question-papers'],
    queryFn: fetchQuestionPapers,
    staleTime: 30 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
  });

