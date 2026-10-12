import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createSource,
  deleteSource,
  IN_PROGRESS_STATUSES,
  listSources,
  sourceKeys,
  uploadPdf,
  type SourceCreate,
} from '../../api/sources'

/** Fallback poll while a source is in flight, for when no `/events` stream is connected. */
const IN_PROGRESS_POLL_MS = 5000

export function useSources() {
  return useQuery({
    queryKey: sourceKeys.list(),
    queryFn: listSources,
    refetchInterval: (query) =>
      query.state.data?.some((s) => IN_PROGRESS_STATUSES.includes(s.status))
        ? IN_PROGRESS_POLL_MS
        : false,
  })
}

function useInvalidateSources() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: sourceKeys.all })
}

export function useCreateSource() {
  const invalidate = useInvalidateSources()
  return useMutation({
    mutationFn: (body: SourceCreate) => createSource(body),
    onSuccess: invalidate,
  })
}

export function useUploadPdf() {
  const invalidate = useInvalidateSources()
  return useMutation({ mutationFn: (file: File) => uploadPdf(file), onSuccess: invalidate })
}

export function useDeleteSource() {
  const invalidate = useInvalidateSources()
  return useMutation({ mutationFn: (id: string) => deleteSource(id), onSettled: invalidate })
}
