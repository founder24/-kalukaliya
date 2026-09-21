/**
 * useUser.js — React Query v5 hooks for user data and mutations.
 * Mirrors the spec: useToggleSavedSubject (optimistic mutation)
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/utils/api';

/**
 * useToggleSavedSubject — optimistic bookmark toggle.
 * Spec:
 *   - On mutate: immediately toggles the subjectId in/out of ['saved-subjects'] cache.
 *   - On error: reverts to snapshot + shows error toast.
 *   - On settled: invalidates ['saved-subjects'] to refetch authoritative state.
 */
export const useToggleSavedSubject = (user) => {
  const queryClient = useQueryClient();
  // Preserve the legacy key for the anonymous/unit-test surface, while
  // authenticated pages receive an identity-scoped cache.
  const savedSubjectsKey = user?.id
    ? ['saved-subjects', user.id]
    : ['saved-subjects'];

  return useMutation({
    mutationFn: (subjectId) =>
      apiClient()
        .post(
          `/user/saved-subjects/${subjectId}`,
          {},
        )
        .then((r) => r.data),

    // ── Optimistic update ──────────────────────────────────────────────────
    onMutate: async (subjectId) => {
      await queryClient.cancelQueries({ queryKey: savedSubjectsKey });
      const previous = queryClient.getQueryData(savedSubjectsKey);
      queryClient.setQueryData(savedSubjectsKey, (old = []) => {
        if (old.includes(subjectId)) {
          return old.filter((id) => id !== subjectId);
        }
        return [...old, subjectId];
      });
      return { previous };
    },

    // ── Rollback on error ──────────────────────────────────────────────────
    onError: (err, _subjectId, context) => {
      // An anonymous query has no cache entry, so `previous` is undefined.
      // Still write an empty authoritative value: otherwise the optimistic
      // [subjectId] created in onMutate remains visibly Saved after a 401.
      queryClient.setQueryData(savedSubjectsKey, context?.previous ?? []);
      const unauthorized = err?.response?.status === 401;
      import('sonner').then(({ toast }) => {
        if (unauthorized) {
          toast.error('Sign in to save subjects', {
            action: {
              label: 'Sign in',
              onClick: () => {
                const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
                window.location.assign(`/login?next=${encodeURIComponent(currentPath)}`);
              },
            },
          });
        } else {
          toast.error('Failed to save subject — please try again');
        }
      });
    },

    // ── Invalidate on settled (success or error) ───────────────────────────
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: savedSubjectsKey });
      queryClient.invalidateQueries({ queryKey: ['library-bundle'] });
    },
  });
};
