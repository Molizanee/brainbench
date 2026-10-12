import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
} from '@mui/material'
import type { PlanItemRead } from '../../../api/plans'

const schema = z.object({
  title: z.string().trim().min(1, 'Enter a title').max(120, 'Keep the title under 120 characters'),
  summary: z
    .string()
    .trim()
    .min(1, 'Enter a summary')
    .max(4000, 'Keep the summary under 4000 characters'),
})

type Values = z.infer<typeof schema>

type Props = {
  item: PlanItemRead
  error?: string | null
  busy?: boolean
  onClose: () => void
  onSave: (changes: Partial<Values>) => void
}

/** Edits a topic's title or summary; saving creates a topic revision and a plan version. */
export function EditTopicDialog({ item, error, busy = false, onClose, onSave }: Props) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { title: item.title, summary: item.summary },
  })
  const submit = form.handleSubmit((values) => {
    const changes: Partial<Values> = {}
    if (values.title !== item.title) changes.title = values.title
    if (values.summary !== item.summary) changes.summary = values.summary
    if (Object.keys(changes).length === 0) {
      form.setError('title', { message: 'Change the title or the summary' })
      return
    }
    onSave(changes)
  })
  const { errors } = form.formState
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="edit-topic-title">
      <form onSubmit={submit} noValidate>
        <DialogTitle id="edit-topic-title">Edit topic</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              label="Title"
              {...form.register('title')}
              error={!!errors.title}
              helperText={errors.title?.message}
              fullWidth
            />
            <TextField
              label="Summary"
              {...form.register('summary')}
              error={!!errors.summary}
              helperText={errors.summary?.message}
              multiline
              minRows={4}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={busy}>
            Save
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
