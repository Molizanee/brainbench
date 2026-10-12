import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormHelperText,
  FormLabel,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../../api/client'
import {
  logSession,
  MASTERY_RATINGS,
  MAX_SESSION_MINUTES,
  sessionLoggedKeys,
  type SessionLogged,
} from '../../../api/progress'
import { ratingLabels } from '../format'

const schema = z.object({
  minutes: z.coerce
    .number<string>({ error: 'Enter the minutes you studied' })
    .int('Use whole minutes')
    .min(1, 'Log at least 1 minute')
    .max(MAX_SESSION_MINUTES, `At most ${MAX_SESSION_MINUTES} minutes per session`),
  rating: z.enum(MASTERY_RATINGS, { error: 'Choose how well you mastered it' }),
  note: z.string().max(4000, 'Keep the note under 4000 characters'),
})

type Input = z.input<typeof schema>
type Values = z.output<typeof schema>
const FIELDS = ['minutes', 'rating', 'note'] as const

type Props = {
  topicId: string
  topicTitle?: string
  open: boolean
  onClose: () => void
  onLogged?: (result: SessionLogged) => void
}

/** Logs a study session on a topic; used by the progress page and the topic page. */
export function LogSessionDialog({ topicId, topicTitle, open, onClose, onLogged }: Props) {
  const queryClient = useQueryClient()
  const form = useForm<Input, unknown, Values>({
    resolver: zodResolver(schema),
    defaultValues: { minutes: '', rating: undefined, note: '' },
  })
  const mutation = useMutation({
    mutationFn: (values: Values) =>
      logSession(topicId, {
        minutes: values.minutes,
        rating: values.rating,
        note: values.note.trim() || null,
      }),
    onSuccess: async (result) => {
      await Promise.all(
        sessionLoggedKeys(topicId).map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      )
      form.reset()
      onLogged?.(result)
      onClose()
    },
    onError: (error) => {
      for (const field of FIELDS) {
        const message = error instanceof ApiError ? error.fields?.[field] : undefined
        if (message) form.setError(field, { message })
      }
    },
  })

  const close = () => {
    if (mutation.isPending) return
    form.reset()
    mutation.reset()
    onClose()
  }
  const formError =
    mutation.error && !(mutation.error instanceof ApiError && mutation.error.fields)
      ? mutation.error
      : null

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="sm">
      <form onSubmit={form.handleSubmit((values) => mutation.mutate(values))} noValidate>
        <DialogTitle>{topicTitle ? `Log session: ${topicTitle}` : 'Log session'}</DialogTitle>
        <DialogContent>
          <Stack spacing={3} sx={{ pt: 1 }}>
            {formError && <Alert severity="error">{formError.message}</Alert>}
            <Controller
              name="minutes"
              control={form.control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  value={field.value ?? ''}
                  type="number"
                  label="Minutes studied"
                  autoFocus
                  slotProps={{ htmlInput: { min: 1, max: MAX_SESSION_MINUTES, step: 1 } }}
                  error={!!fieldState.error}
                  helperText={fieldState.error?.message}
                />
              )}
            />
            <Controller
              name="rating"
              control={form.control}
              render={({ field, fieldState }) => (
                <FormControl error={!!fieldState.error}>
                  <FormLabel id="mastery-rating-label" sx={{ mb: 1 }}>
                    How well do you know it now?
                  </FormLabel>
                  <ToggleButtonGroup
                    exclusive
                    color="primary"
                    aria-labelledby="mastery-rating-label"
                    value={field.value ?? null}
                    onChange={(_, value: Values['rating'] | null) => {
                      if (value) field.onChange(value)
                    }}
                    sx={{ flexWrap: 'wrap' }}
                  >
                    {MASTERY_RATINGS.map((rating) => (
                      <ToggleButton key={rating} value={rating} sx={{ textTransform: 'none' }}>
                        {ratingLabels[rating]}
                      </ToggleButton>
                    ))}
                  </ToggleButtonGroup>
                  {fieldState.error && <FormHelperText>{fieldState.error.message}</FormHelperText>}
                </FormControl>
              )}
            />
            <Controller
              name="note"
              control={form.control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="Note (optional)"
                  multiline
                  minRows={2}
                  error={!!fieldState.error}
                  helperText={fieldState.error?.message}
                />
              )}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={close} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="contained" loading={mutation.isPending}>
            Log session
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
