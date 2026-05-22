/**
 * useUser.js — React Query v5 hooks for user data and mutations.
 * Mirrors the spec: useToggleSavedSubject (optimistic mutation)
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { API_BASE } from '@/utils/api';

/**
 * useToggleSavedSubject — optimistic bookmark toggle.
 * Spec:
 *   - On mutate: immediately toggles the subjectId in/out of ['saved-subjects'] cache.
 *   - On error: reverts to snapshot + shows error toast.
 *   - On settled: invalidates ['saved-subjects'] to refetch authoritative state.
 */
export const useToggleSavedSubject = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (subjectId) =>
      axios
        .post(
          `${API_BASE}/user/saved-subjects/${subjectId}`,
          {},
          { withCredentials: true }
        )
        .then((r) => r.data),

    // ── Optimistic update ──────────────────────────────────────────────────
    onMutate: async (subjectId) => {
      await queryClient.cancelQueries({ queryKey: ['saved-subjects'] });
      const previous = queryClient.getQueryData(['saved-subjects']);
      queryClient.setQueryData(['saved-subjects'], (old = []) => {
        if (old.includes(subjectId)) {
          return old.filter((id) => id !== subjectId);
        }
        return [...old, subjectId];
      });
      return { previous };
    },

    // ── Rollback on error ──────────────────────────────────────────────────
    onError: (_err, _subjectId, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(['saved-subjects'], context.previous);
      }
      import('sonner').then(({ toast }) => {
        toast.error('Failed to save subject — please try again');
      });
    },

    // ── Invalidate on settled (success or error) ───────────────────────────
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['saved-subjects'] });
      queryClient.invalidateQueries({ queryKey: ['library-bundle'] });
    },
  });
};
