import { useSearchParams } from 'react-router'
import { Alert, Button, Skeleton, Stack, Typography } from '@mui/material'
import { EmptyState } from '../../components/EmptyState'
import { RunPanel } from './components/RunPanel'
import { PlanView } from './PlanView'
import { usePlanQuery, usePlanVersions } from './usePlan'

/** The study plan: the current version by default, any version through `?version=N`. */
export default function PlanPage() {
  const [params, setParams] = useSearchParams()
  const requested = Number(params.get('version')) || null
  const plan = usePlanQuery(requested)
  const versions = usePlanVersions()

  const selectVersion = (version: number) => {
    const current = plan.data?.current_version
    setParams(version === current ? {} : { version: String(version) })
  }

  return (
    <>
      <Typography variant="h1" gutterBottom>
        Study plan
      </Typography>
      {plan.isPending && (
        <Stack spacing={2} aria-label="Loading plan">
          <Skeleton variant="rounded" height={60} />
          <Skeleton variant="rounded" height={240} />
        </Stack>
      )}
      {plan.isError && (
        <Alert severity="error" action={<Button onClick={() => void plan.refetch()}>Retry</Button>}>
          {plan.error.message}
        </Alert>
      )}
      {plan.data === null && (
        <EmptyState
          title="No study plan yet"
          description="Curation turns your ready sources into a plan ordered by prerequisites and your target skills."
          action={<RunPanel />}
        />
      )}
      {plan.data && (
        <PlanView plan={plan.data} versions={versions.data ?? []} onSelectVersion={selectVersion} />
      )}
    </>
  )
}
