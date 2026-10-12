import { Chip, type ChipProps } from '@mui/material'
import type { JobStatus } from '../../api/jobs'

const COLORS: Record<JobStatus, ChipProps['color']> = {
  queued: 'default',
  running: 'info',
  ready: 'success',
  failed: 'error',
  cancelled: 'warning',
  skipped: 'default',
}

type Props = { status: JobStatus; cancelling?: boolean }

/** The status as text (color is never the only signal). */
export function JobStatusChip({ status, cancelling = false }: Props) {
  const label = cancelling && status === 'running' ? 'cancelling' : status
  return (
    <Chip
      size="small"
      label={label}
      color={COLORS[status]}
      variant={status === 'queued' ? 'outlined' : 'filled'}
    />
  )
}
