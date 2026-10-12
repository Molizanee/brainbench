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
import { addSkill, SKILL_LEVELS, updateSkill, type CurrentSkillRead } from '../../api/profile'
import { applyFieldErrors } from './fieldErrors'
import { LEVEL_LABELS } from './labels'
import { useProfileMutation } from './useProfile'

const schema = z.object({
  name: z.string().trim().min(1, 'Enter the skill name'),
  level: z.enum(SKILL_LEVELS),
})

type Values = z.infer<typeof schema>

const FIELDS: Path<Values>[] = ['name', 'level']

type Props = { skill?: CurrentSkillRead; onClose: () => void }

/** Add or edit a current skill. Mounted while open, so each opening starts fresh. */
export function SkillDialog({ skill, onClose }: Props) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: skill?.name ?? '', level: skill?.level ?? 'working' },
  })
  const [formError, setFormError] = useState<Error | null>(null)
  const save = useProfileMutation((values: Values) =>
    skill ? updateSkill(skill.id, values) : addSkill(values),
  )

  const onSubmit = form.handleSubmit((values) => {
    setFormError(null)
    save.mutate(values, {
      onSuccess: onClose,
      onError: (error) => setFormError(applyFieldErrors(error, FIELDS, form.setError)),
    })
  })

  const title = skill ? 'Edit current skill' : 'Add current skill'
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="xs" aria-labelledby="skill-dialog-title">
      <form onSubmit={onSubmit} noValidate>
        <DialogTitle id="skill-dialog-title">{title}</DialogTitle>
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
            <Controller
              name="level"
              control={form.control}
              render={({ field, fieldState }) => (
                <TextField
                  {...field}
                  select
                  label="Level"
                  error={!!fieldState.error}
                  helperText={fieldState.error?.message}
                >
                  {SKILL_LEVELS.map((level) => (
                    <MenuItem key={level} value={level}>
                      {LEVEL_LABELS[level]}
                    </MenuItem>
                  ))}
                </TextField>
              )}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" loading={save.isPending}>
            {skill ? 'Save' : 'Add'}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
