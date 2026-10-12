import { useState } from 'react'
import {
  Alert,
  Button,
  Card,
  CardContent,
  CardHeader,
  FormControl,
  InputLabel,
  ListSubheader,
  MenuItem,
  Select,
  Stack,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  patchProviderSettings,
  providerKeys,
  type ProviderSettingsRead,
  type RouteEntry,
  type TaskClass,
} from '../../api/providers'

const TASK_LABELS: Record<TaskClass, string> = {
  curation: 'Curation',
  storyboarding: 'Storyboarding',
  'script-generation': 'Script generation',
  'script-repair': 'Script repair',
  'figure-building': 'Figure building',
  tutoring: 'Tutoring',
  topic_extraction: 'Curation: topic extraction',
  topic_merge: 'Curation: topic merge',
  scoring: 'Curation: scoring',
  prerequisites: 'Curation: prerequisites',
}

const DEFAULT = ''
const encode = (r: RouteEntry) => `${r.provider}::${r.model}`

function decode(value: string): RouteEntry | null {
  const [provider, model] = value.split('::')
  return provider && model ? { provider, model } : null
}

/** Task class → provider and model; an unset class uses the first connected provider. */
export function RoutingCard({ settings }: { settings: ProviderSettingsRead }) {
  const queryClient = useQueryClient()
  const initial = Object.fromEntries(
    settings.task_classes.map((tc) => {
      const route = settings.routing[tc]
      return [tc, route ? encode(route) : DEFAULT]
    }),
  ) as Record<TaskClass, string>
  const [values, setValues] = useState(initial)
  const save = useMutation({
    mutationFn: () => {
      const routing: Partial<Record<TaskClass, RouteEntry>> = {}
      for (const tc of settings.task_classes) {
        const route = decode(values[tc])
        if (route) routing[tc] = route
      }
      return patchProviderSettings({ routing })
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(providerKeys.settings(), saved)
    },
  })

  const defaultLabel = (tc: TaskClass) => {
    const effective = settings.effective[tc]
    if (!effective || effective.source !== 'default') return 'Default (no connected provider)'
    const option = settings.options.find((o) => o.provider === effective.provider)
    const model = option?.models.find((m) => m.id === effective.model)
    return `Default (${option?.name ?? effective.provider}, ${model?.name ?? effective.model})`
  }

  return (
    <Card variant="outlined" component="section" aria-label="Routing">
      <CardHeader
        title="Routing"
        subheader="Choose the provider and model each kind of work runs on."
        slotProps={{ title: { component: 'h2', variant: 'h2' } }}
      />
      <CardContent>
        <Stack spacing={2}>
          {settings.task_classes.map((tc) => (
            <FormControl key={tc} fullWidth size="small">
              <InputLabel id={`route-${tc}`} shrink>
                {TASK_LABELS[tc]}
              </InputLabel>
              <Select
                displayEmpty
                notched
                labelId={`route-${tc}`}
                label={TASK_LABELS[tc]}
                value={values[tc]}
                onChange={(e) => setValues({ ...values, [tc]: e.target.value })}
              >
                <MenuItem value={DEFAULT}>{defaultLabel(tc)}</MenuItem>
                {settings.options.flatMap((option) => [
                  <ListSubheader key={`h-${option.provider}`}>
                    {option.name}
                    {option.connected ? '' : ' (not connected)'}
                  </ListSubheader>,
                  ...option.models.map((m) => (
                    <MenuItem
                      key={`${option.provider}-${m.id}`}
                      value={encode({ provider: option.provider, model: m.id })}
                    >
                      {m.name}
                    </MenuItem>
                  )),
                ])}
              </Select>
            </FormControl>
          ))}
          {save.isError && <Alert severity="error">{save.error.message}</Alert>}
          {save.isSuccess && <Alert severity="success">Routing saved.</Alert>}
          <Stack direction="row">
            <Button variant="contained" loading={save.isPending} onClick={() => save.mutate()}>
              Save routing
            </Button>
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  )
}
