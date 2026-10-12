import { useState } from 'react'
import { Controller, useForm, type Path } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
} from '@mui/material'
import {
  addTarget,
  PRIORITIES,
  TARGET_LEVELS,
  updateTarget,
  type TargetSkillRead,
} from '../../api/profile'
import { applyFieldErrors } from './fieldErrors'
import { LEVEL_LABELS, PRIORITY_LABELS } from './labels'
import { useProfileMutation } from './useProfile'

const schema = z.object({
  name: z.string().trim().min(1, 'Enter the skill name'),
  target_level: z.enum(TARGET_LEVELS),
  priority: z.enum(PRIORITIES),
  reason: z.string(),
})

type Values = z.infer<typeof schema>

const FIELDS: Path<Values>[] = ['name', 'target_level', 'priority', 'reason']

type Props = { target?: TargetSkillRead; onClose: () => void }

/** Add or edit a target skill. Mounted while open, so each opening starts fresh. */
export function TargetDialog({ target, onClose }: Props) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: target?.name ?? '',
      target_level: target?.target_level ?? 'proficient',
      priority: target?.priority ?? 'medium',
      reason: target?.reason ?? '',
    },
  })
  const [formError, setFormError] = useState<Error | null>(null)
  const save = useProfileMutation((values: Values) => {
    const body = { ...values, reason: values.reason.trim() || null }
    return target ? updateTarget(target.id, body) : addTarget(body)
  })

  const onSubmit = form.handleSubmit((values) => {
    setFormError(null)
    save.mutate(values, {
      onSuccess: onClose,
      onError: (error) => setFormError(applyFieldErrors(error, FIELDS, form.setError)),
    })
  })

  const title = target ? 'Edit target skill' : 'Add target skill'
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="target-dialog-title">
      <form onSubmit={onSubmit} noValidate>
        <DialogTitle id="target-dialog-title">{title}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {formError && <Alert severity="error">{formError.message}</Alert>}
            <Controller
              name="name"
              control={form.control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="Skill"
                  autoFocus
                  error={!!fieldState.error}
                  helperText={fieldState.error?.message}
                />
              )}
            />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
              <Controller
                name="target_level"
                control={form.control}
                render={({ field, fieldState }) => (
                  <TextField
                    {...field}
                    select
                    label="Target level"
                    error={!!fieldState.error}
                    helperText={fieldState.error?.message}
                  >
                    {TARGET_LEVELS.map((level) => (
                      <MenuItem key={level} value={level}>
                        {LEVEL_LABELS[level]}
                      </MenuItem>
                    ))}
                  </TextField>
                )}
              />
              <Controller
                name="priority"
                control={form.control}
                render={({ field, fieldState }) => (
                  <TextField
                    {...field}
                    select
                    label="Priority"
                    error={!!fieldState.error}
                    helperText={fieldState.error?.message}
                  >
                    {PRIORITIES.map((priority) => (
                      <MenuItem key={priority} value={priority}>
                        {PRIORITY_LABELS[priority]}
                      </MenuItem>
                    ))}
                  </TextField>
                )}
              />
            </Stack>
            <Controller
              name="reason"
              control={form.control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  label="Reason (optional)"
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
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" loading={save.isPending}>
            {target ? 'Save' : 'Add'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
