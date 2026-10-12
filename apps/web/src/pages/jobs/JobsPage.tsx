import { useState } from 'react'
import { Alert, Button, Skeleton, Snackbar, Stack, Typography } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelJob, jobKeys, listJobs, type JobRead } from '../../api/jobs'
import { EmptyState } from '../../components/EmptyState'
import { JobsTable } from './JobsTable'

const FILTERS = { limit: 100 }

/** Every job with live status: `jobs.updated` events refresh the list through the event hook. */
export default function JobsPage() {
  const queryClient = useQueryClient()
  const jobs = useQuery({ queryKey: jobKeys.list(FILTERS), queryFn: () => listJobs(FILTERS) })
  const [cancelError, setCancelError] = useState<string | null>(null)
  const cancel = useMutation({
    mutationFn: (job: JobRead) => cancelJob(job.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: jobKeys.all }),
    onError: (error) => setCancelError(error.message),
  })

  return (
    <>
      <Typography variant="h1" gutterBottom>
        Jobs
      </Typography>
      {jobs.isPending && (
        <Stack spacing={1} aria-label="Loading jobs">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} variant="rounded" height={40} />
          ))}
        </Stack>
      )}
      {jobs.isError && (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => void jobs.refetch()}>
              Retry
            </Button>
          }
        >
          Could not load jobs: {jobs.error.message}
        </Alert>
      )}
      {jobs.isSuccess && jobs.data.length === 0 && (
        <EmptyState
          title="No jobs yet"
          description="Ingestion, curation, and generation work shows up here as it runs."
        />
      )}
      {jobs.isSuccess && jobs.data.length > 0 && (
        <JobsTable
          jobs={jobs.data}
          onCancel={(job) => cancel.mutate(job)}
          cancellingId={cancel.isPending ? (cancel.variables?.id ?? null) : null}
        />
      )}
      <Snackbar open={cancelError !== null} onClose={() => setCancelError(null)}>
        <Alert severity="error" onClose={() => setCancelError(null)}>
          {cancelError}
        </Alert>
      </Snackbar>
    </>
  )
}
