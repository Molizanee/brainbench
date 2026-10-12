import { Alert, Button, LinearProgress, Stack, Typography } from '@mui/material'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { ApiError } from '../../../api/client'
import { isRunActive } from '../../../api/curation'
import { useRunsQuery, useStartRun } from '../usePlan'

/** Starts a curation run and shows the latest run's progress or failure. */
export function RunPanel({ label = 'Run curation' }: { label?: string }) {
  const runs = useRunsQuery()
  const start = useStartRun()
  const latest = runs.data?.[0]
  const active = isRunActive(latest)
  const startError = start.error instanceof ApiError ? start.error.message : start.error?.message
  return (
    <Stack spacing={1} aria-label="Curation run">
      <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <Button
          variant="contained"
          startIcon={<PlayArrowIcon />}
          onClick={() => start.mutate({})}
          disabled={active || start.isPending}
        >
          {label}
        </Button>
        {active && (
          <Typography color="text.secondary" role="status">
            Curation {latest?.status}: {latest?.progress ?? 'waiting for the worker'}
          </Typography>
        )}
      </Stack>
      {active && <LinearProgress aria-label="Curation in progress" />}
      {startError && <Alert severity="error">{startError}</Alert>}
      {!active && latest?.status === 'failed' && (
        <Alert severity="error">
          The last curation run failed: {latest.error ?? 'unknown error'}
        </Alert>
      )}
    </Stack>
  )
}
