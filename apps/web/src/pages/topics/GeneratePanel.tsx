import { useState } from 'react'
import {
  Alert,
  Button,
  Card,
  CardContent,
  Checkbox,
  FormControlLabel,
  FormGroup,
  Stack,
  Typography,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { isActive } from '../../api/jobs'
import {
  generateForTopic,
  topicKeys,
  VISUAL_FORMATS,
  type GenerateResponse,
  type TopicRead,
  type VisualFormat,
} from '../../api/topics'
import { JobStatusChip } from '../jobs/JobStatusChip'
import { budgetSummary, formatLabels } from './format'

type Props = { topic: TopicRead; onQueued: (result: GenerateResponse) => void }

/** Request visuals for this topic; shows the latest job per format and the budget left. */
export function GeneratePanel({ topic, onQueued }: Props) {
  const queryClient = useQueryClient()
  const [formats, setFormats] = useState<VisualFormat[]>(['video', 'figure'])
  const mutation = useMutation({
    mutationFn: () => generateForTopic(topic.id, formats),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: topicKeys.detail(topic.id) })
      onQueued(result)
    },
  })
  const toggle = (format: VisualFormat) =>
    setFormats((list) =>
      list.includes(format) ? list.filter((f) => f !== format) : [...list, format],
    )
  return (
    <Card component="section" aria-label="Generate visuals" variant="outlined">
      <CardContent>
        <Stack spacing={2}>
          <Typography variant="h2" component="h2">
            Visuals
          </Typography>
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <FormGroup row aria-label="Formats to generate">
              {VISUAL_FORMATS.map((format) => (
                <FormControlLabel
                  key={format}
                  label={formatLabels[format]}
                  control={
                    <Checkbox checked={formats.includes(format)} onChange={() => toggle(format)} />
                  }
                />
              ))}
            </FormGroup>
            <Button
              variant="contained"
              startIcon={<AutoAwesomeIcon />}
              disabled={formats.length === 0 || mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              Generate
            </Button>
          </Stack>
          {mutation.isError && <Alert severity="error">{mutation.error.message}</Alert>}
          {topic.jobs.map((job) => (
            <Stack
              key={job.id}
              direction="row"
              spacing={1}
              sx={{ alignItems: 'center', flexWrap: 'wrap' }}
              aria-label={`${formatLabels[job.type as VisualFormat] ?? job.type} job`}
            >
              <Typography variant="body2" sx={{ minWidth: 56 }}>
                {formatLabels[job.type as VisualFormat] ?? job.type}
              </Typography>
              <JobStatusChip status={job.status} cancelling={job.cancel_requested} />
              <Typography variant="body2" color="text.secondary">
                {job.status === 'failed'
                  ? `${job.error_reason ?? 'failed'}: ${(job.error ?? '').split('\n')[0]}`
                  : isActive(job) || job.status === 'skipped'
                    ? job.progress
                    : null}
              </Typography>
            </Stack>
          ))}
          <Typography variant="caption" color="text.secondary">
            {budgetSummary(topic.budget)}
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  )
}
