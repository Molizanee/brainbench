import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { Alert, Button, Skeleton, Snackbar, Stack } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import type { PlanGenerateResponse } from '../../api/assets'
import {
  getTopic,
  topicKeys,
  VISUAL_FORMATS,
  type GenerateResponse,
  type TopicRead,
} from '../../api/topics'
import { EmptyState } from '../../components/EmptyState'
import { LogSessionDialog } from '../progress/components/LogSessionDialog'
import { AssetSection } from './AssetSection'
import { formatLabels } from './format'
import { GeneratePanel } from './GeneratePanel'
import { PlanGenerateDialog } from './PlanGenerateDialog'
import { TopicHeader } from './TopicHeader'

function queuedMessage(result: GenerateResponse): string {
  return result.jobs
    .map((job) =>
      job.deduplicated
        ? `A ${formatLabels[job.format].toLowerCase()} job is already ${job.status ?? 'queued'}`
        : `${formatLabels[job.format]} queued`,
    )
    .join(' · ')
}

/** One topic: its visuals (video, figure) with provenance and history, and generation. */
export default function TopicPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const topic = useQuery({ queryKey: topicKeys.detail(id), queryFn: () => getTopic(id) })
  const [logging, setLogging] = useState(false)
  const [planOpen, setPlanOpen] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  // A merged topic answers with its survivor (the API redirects); show the survivor's URL.
  const survivor = topic.data && topic.data.id !== id ? topic.data.id : null
  useEffect(() => {
    if (survivor) void navigate(`/topics/${survivor}`, { replace: true })
  }, [survivor, navigate])

  if (topic.isPending) {
    return (
      <Stack spacing={2} aria-label="Loading topic">
        <Skeleton variant="text" height={48} />
        <Skeleton variant="rounded" height={120} />
        <Skeleton variant="rounded" height={320} />
      </Stack>
    )
  }
  if (topic.isError) {
    return (
      <Alert severity="error" action={<Button onClick={() => void topic.refetch()}>Retry</Button>}>
        {topic.error.message}
      </Alert>
    )
  }
  const data: TopicRead = topic.data
  const byFormat = VISUAL_FORMATS.map(
    (format) => [format, data.assets.filter((a) => a.format === format)] as const,
  )
  const onPlanQueued = (result: PlanGenerateResponse) =>
    setMessage(`${result.job_count} ${result.job_count === 1 ? 'job' : 'jobs'} queued for the plan`)

  return (
    <Stack spacing={3}>
      <TopicHeader
        topic={data}
        onLogSession={() => setLogging(true)}
        onGeneratePlan={() => setPlanOpen(true)}
      />
      <GeneratePanel topic={data} onQueued={(result) => setMessage(queuedMessage(result))} />
      {data.assets.length === 0 ? (
        <EmptyState
          title="No visuals yet"
          description="Generate a narrated video or an interactive figure for this topic."
        />
      ) : (
        byFormat.map(([format, assets]) =>
          assets.length ? (
            <AssetSection
              key={format}
              format={format}
              assets={assets}
              title={data.title}
              onQueued={(result) => setMessage(queuedMessage(result))}
            />
          ) : null,
        )
      )}
      {logging && (
        <LogSessionDialog
          open
          topicId={data.id}
          topicTitle={data.title}
          onClose={() => setLogging(false)}
        />
      )}
      {planOpen && (
        <PlanGenerateDialog open onClose={() => setPlanOpen(false)} onQueued={onPlanQueued} />
      )}
      <Snackbar
        open={message !== null}
        autoHideDuration={6000}
        onClose={() => setMessage(null)}
        message={message}
      />
    </Stack>
  )
}
