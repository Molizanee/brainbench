import {
  Button,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { isActive, type JobRead } from '../../api/jobs'
import { JobStatusChip } from './JobStatusChip'
import { formatTime, pendingRunAfter } from './time'

type Props = {
  jobs: JobRead[]
  onCancel: (job: JobRead) => void
  cancellingId: string | null
}

export function JobsTable({ jobs, onCancel, cancellingId }: Props) {
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small" aria-label="Jobs">
        <TableHead>
          <TableRow>
            <TableCell>Type</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Progress</TableCell>
            <TableCell align="right">Attempt</TableCell>
            <TableCell>Run after</TableCell>
            <TableCell>Created</TableCell>
            <TableCell align="right">Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {jobs.map((job) => (
            <JobRow
              key={job.id}
              job={job}
              onCancel={onCancel}
              cancelling={cancellingId === job.id}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

type RowProps = { job: JobRead; onCancel: (job: JobRead) => void; cancelling: boolean }

function JobRow({ job, onCancel, cancelling }: RowProps) {
  const waitingUntil = job.status === 'queued' ? pendingRunAfter(job.run_after) : null
  return (
    <TableRow data-testid={`job-${job.id}`}>
      <TableCell>{job.type}</TableCell>
      <TableCell>
        <JobStatusChip status={job.status} cancelling={job.cancel_requested} />
      </TableCell>
      <TableCell>
        <Typography variant="body2">{job.progress ?? '-'}</Typography>
        {job.status === 'failed' && job.error && (
          <Typography variant="caption" color="error" component="p">
            {job.error_reason ? `${job.error_reason}: ` : ''}
            {job.error}
          </Typography>
        )}
      </TableCell>
      <TableCell align="right">
        {job.attempt} / {job.max_attempts}
      </TableCell>
      <TableCell>{waitingUntil ?? '-'}</TableCell>
      <TableCell>{formatTime(job.created_at)}</TableCell>
      <TableCell align="right">
        {isActive(job) && (
          <Button
            size="small"
            color="warning"
            disabled={job.cancel_requested || cancelling}
            onClick={() => onCancel(job)}
            aria-label={`Cancel ${job.type} job`}
          >
            Cancel
          </Button>
        )}
      </TableCell>
    </TableRow>
  )
}
