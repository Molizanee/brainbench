import { useState } from 'react'
import { Alert, Button, Skeleton, Snackbar, Stack, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import {
  getProgress,
  localUtcOffsetMinutes,
  progressKeys,
  type SessionLogged,
  type TargetTopic,
} from '../../api/progress'
import { EmptyState } from '../../components/EmptyState'
import { LogSessionDialog } from './components/LogSessionDialog'
import { TargetCard } from './components/TargetCard'
import { WeeklyPaceCard } from './components/WeeklyPaceCard'
import { formatMinutes } from './format'

/** Per target skill: topic counts by status, minutes studied and remaining; weekly pace. */
export default function ProgressPage() {
  const [offset] = useState(localUtcOffsetMinutes)
  const progress = useQuery({
    queryKey: progressKeys.summary(offset),
    queryFn: () => getProgress(offset),
  })
  const [logging, setLogging] = useState<TargetTopic | null>(null)
  const [achieved, setAchieved] = useState<string | null>(null)

  const onLogged = (result: SessionLogged) => {
    const names = result.promoted_targets.map((t) => `${t.name} (${t.level})`)
    if (names.length) setAchieved(`Target achieved: ${names.join(', ')}`)
  }

  return (
    <>
      <Typography variant="h1" gutterBottom>
        Progress
      </Typography>
      {progress.isPending && (
        <Stack spacing={2} aria-label="Loading progress">
          <Skeleton variant="rounded" height={140} />
          <Skeleton variant="rounded" height={220} />
        </Stack>
      )}
      {progress.isError && (
        <Alert
          severity="error"
          action={<Button onClick={() => void progress.refetch()}>Retry</Button>}
        >
          {progress.error.message}
        </Alert>
      )}
      {progress.data && (
        <Stack spacing={3}>
          <WeeklyPaceCard week={progress.data.week} />
          <Typography color="text.secondary">
            {formatMinutes(progress.data.minutes_studied)} studied in total
          </Typography>
          {progress.data.targets.length > 0 && progress.data.plan_version === null && (
            <Alert severity="info">
              No study plan yet. Run curation to see the topics that serve each target.
            </Alert>
          )}
          {progress.data.targets.length === 0 ? (
            <EmptyState
              title="No target skills yet"
              description="Add target skills to your profile to track progress toward them."
            />
          ) : (
            progress.data.targets.map((target) => (
              <TargetCard key={target.id} target={target} onLog={setLogging} />
            ))
          )}
        </Stack>
      )}
      {logging && (
        <LogSessionDialog
          open
          topicId={logging.topic_id}
          topicTitle={logging.title}
          onClose={() => setLogging(null)}
          onLogged={onLogged}
        />
      )}
      <Snackbar
        open={achieved !== null}
        autoHideDuration={8000}
        onClose={() => setAchieved(null)}
        message={achieved}
      />
    </>
  )
}
