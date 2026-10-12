import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Skeleton,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { generateForPlan, type PlanGenerateResponse } from '../../api/assets'
import { topicKeys } from '../../api/topics'
import { budgetSummary, formatLabels } from './format'

type Props = {
  open: boolean
  onClose: () => void
  onQueued: (result: PlanGenerateResponse) => void
}

/**
 * Whole-plan generation: a dry run counts the jobs first, and nothing is queued until the
 * engineer confirms that count. Reusable by the plan page.
 */
export function PlanGenerateDialog({ open, onClose, onQueued }: Props) {
  const queryClient = useQueryClient()
  const preview = useQuery({
    queryKey: ['plans', 'current', 'generate-preview'],
    queryFn: () => generateForPlan(true),
    enabled: open,
    gcTime: 0,
    staleTime: 0,
  })
  const confirm = useMutation({
    mutationFn: () => generateForPlan(false),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: topicKeys.all })
      onQueued(result)
      onClose()
    },
  })
  const data = preview.data
  const topics = data ? new Set(data.jobs.map((j) => j.topic_id)).size : 0
  const existing = data ? data.jobs.filter((j) => j.deduplicated).length : 0
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Generate visuals for the whole plan</DialogTitle>
      <DialogContent>
        {preview.isPending && <Skeleton variant="rounded" height={64} />}
        {preview.isError && <Alert severity="error">{preview.error.message}</Alert>}
        {data && (
          <>
            <DialogContentText>
              This queues <strong>{data.job_count}</strong> {data.job_count === 1 ? 'job' : 'jobs'}{' '}
              for {topics} {topics === 1 ? 'topic' : 'topics'} of plan version {data.plan_version} (
              {data.formats.map((f) => formatLabels[f]).join(' and ')}), in plan order.
              {existing > 0 && ` ${existing} already queued or running and are kept.`}
            </DialogContentText>
            <DialogContentText variant="caption" sx={{ display: 'block', mt: 1 }}>
              {budgetSummary(data.budget)}
            </DialogContentText>
            {data.job_count > data.budget.jobs_left && (
              <Alert severity="warning" sx={{ mt: 2 }}>
                The daily generation cap leaves room for {data.budget.jobs_left} jobs, so this
                request will be rejected.
              </Alert>
            )}
          </>
        )}
        {confirm.isError && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {confirm.error.message}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!data || data.job_count === 0 || confirm.isPending}
          onClick={() => confirm.mutate()}
        >
          {data ? `Queue ${data.job_count} ${data.job_count === 1 ? 'job' : 'jobs'}` : 'Queue'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
