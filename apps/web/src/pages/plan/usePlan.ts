import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  curationKeys,
  isRunActive,
  listCurationRuns,
  startCurationRun,
  type CurationRunCreate,
} from '../../api/curation'
import {
  editPlan,
  getCurrentPlan,
  getPlan,
  listPlans,
  planKeys,
  type PlanEdit,
  type PlanItemRead,
  type PlanRead,
} from '../../api/plans'
import { progressKeys } from '../../api/progress'

/** The plan at `version`, or the current plan when `version` is null. */
export function usePlanQuery(version: number | null) {
  return useQuery({
    queryKey: version === null ? planKeys.current() : planKeys.version(version),
    queryFn: () => (version === null ? getCurrentPlan() : getPlan(version)),
  })
}

export function usePlanVersions() {
  return useQuery({ queryKey: planKeys.list(), queryFn: listPlans })
}

/** Curation runs, newest first; polled while the latest one is queued or running. */
export function useRunsQuery() {
  return useQuery({
    queryKey: curationKeys.runs(),
    queryFn: listCurationRuns,
    refetchInterval: (query) => (isRunActive(query.state.data?.[0]) ? 5000 : false),
  })
}

export function useStartRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CurationRunCreate = {}) => startCurationRun(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: curationKeys.all }),
  })
}

/** Moves the items to `order`, swapping week placement so blocks stay contiguous until the
 * server answers with the repacked version. */
export function reorderLocally(plan: PlanRead, order: string[]): PlanRead {
  const byId = new Map(plan.items.map((item) => [item.topic_id, item]))
  const items: PlanItemRead[] = order.flatMap((id, index) => {
    const item = byId.get(id)
    const slot = plan.items[index]
    if (!item || !slot) return []
    return [
      {
        ...item,
        position: slot.position,
        week_start: slot.week_start,
        week_span: slot.week_span,
        exceeds_weekly_budget: slot.exceeds_weekly_budget,
      },
    ]
  })
  return { ...plan, items }
}

/** Edits the current plan; a reorder shows at once and rolls back if the API rejects it. */
export function usePlanEdit(plan: PlanRead) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (edit: PlanEdit) => editPlan(plan.version, edit),
    onMutate: async (edit) => {
      if (edit.op !== 'reorder') return { previous: undefined }
      await queryClient.cancelQueries({ queryKey: planKeys.current() })
      const previous = queryClient.getQueryData<PlanRead | null>(planKeys.current())
      if (previous)
        queryClient.setQueryData(planKeys.current(), reorderLocally(previous, edit.order))
      return { previous }
    },
    onError: (_error, _edit, context) => {
      if (context?.previous) queryClient.setQueryData(planKeys.current(), context.previous)
    },
    onSuccess: async (next) => {
      queryClient.setQueryData(planKeys.current(), next)
      queryClient.setQueryData(planKeys.version(next.version), next)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: planKeys.list() }),
        queryClient.invalidateQueries({ queryKey: progressKeys.all }),
      ])
    },
  })
}
