import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { regenerateAsset } from '../../api/assets'
import { ApiError } from '../../api/client'
import { topicKeys, type AssetRead, type GenerateResponse } from '../../api/topics'
import { formatLabels } from './format'

const schema = z.object({
  feedback: z
    .string()
    .trim()
    .min(1, 'Describe what should change')
    .max(4000, 'Keep the feedback under 4000 characters'),
})
type Values = z.infer<typeof schema>

type Props = {
  asset: AssetRead
  open: boolean
  onClose: () => void
  onQueued: (result: GenerateResponse) => void
}

/** New version of an asset from free-text feedback; the old one stays in the history. */
export function RegenerateDialog({ asset, open, onClose, onQueued }: Props) {
  const queryClient = useQueryClient()
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { feedback: '' } })
  const mutation = useMutation({
    mutationFn: (values: Values) => regenerateAsset(asset.id, values.feedback),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: topicKeys.detail(asset.topic_id) })
      form.reset()
      onQueued(result)
      onClose()
    },
    onError: (error) => {
      if (error instanceof ApiError && error.fields?.['body.feedback']) {
        form.setError('feedback', { message: error.fields['body.feedback'] })
      }
    },
  })
  const label = formatLabels[asset.format].toLowerCase()
  const fieldError = form.formState.errors.feedback?.message
  const otherError =
    mutation.error && !(mutation.error instanceof ApiError && mutation.error.fields)
      ? mutation.error.message
      : null
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <form onSubmit={form.handleSubmit((values) => mutation.mutate(values))} noValidate>
        <DialogTitle>Regenerate the {label}</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            The new version starts from this {label}&apos;s storyboard and{' '}
            {asset.format === 'video' ? 'script' : 'build source'}. This one stays in the history.
          </DialogContentText>
          {otherError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {otherError}
            </Alert>
          )}
          <TextField
            {...form.register('feedback')}
            label="What should change?"
            placeholder="Too fast, show the proof step by step"
            multiline
            minRows={3}
            autoFocus
            error={!!fieldError}
            helperText={fieldError}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={mutation.isPending}>
            Regenerate
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
