import { useState } from 'react'
import { Controller, useForm, type Path } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormHelperText,
  FormLabel,
  MenuItem,
  Stack,
  TextField,
} from '@mui/material'
import {
  CONTENT_FORMATS,
  createProfile,
  DEPTHS,
  updateProfile,
  type ProfileRead,
} from '../../api/profile'
import { applyFieldErrors } from './fieldErrors'
import { DEPTH_LABELS, FORMAT_LABELS } from './labels'
import { useProfileMutation } from './useProfile'

// Shape only: the API owns the business rules (budget > 0) and reports them per field.
const schema = z.object({
  display_name: z.string().trim().min(1, 'Enter a display name'),
  background: z.string(),
  weekly_budget_hours: z
    .string()
    .trim()
    .min(1, 'Enter your weekly study time')
    .refine((value) => Number.isFinite(Number(value)), 'Enter a number of hours'),
  preferences: z.object({
    formats: z.array(z.enum(CONTENT_FORMATS)).min(1, 'Pick at least one format'),
    depth: z.enum(DEPTHS),
    language: z.string().trim().min(1, 'Enter a language code'),
  }),
})

type Values = z.infer<typeof schema>

const FIELDS: Path<Values>[] = [
  'display_name',
  'background',
  'weekly_budget_hours',
  'preferences.formats',
  'preferences.depth',
  'preferences.language',
]

function toValues(profile: ProfileRead | null): Values {
  return {
    display_name: profile?.display_name ?? '',
    background: profile?.background ?? '',
    weekly_budget_hours: profile ? String(profile.weekly_budget_hours) : '',
    preferences: profile?.preferences ?? {
      formats: [...CONTENT_FORMATS],
      depth: 'intuition-first',
      language: 'en',
    },
  }
}

type Props = { profile: ProfileRead | null }

/** Display name, background, weekly time budget, and preferences; creates or updates. */
export function ProfileForm({ profile }: Props) {
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: toValues(profile) })
  const [formError, setFormError] = useState<Error | null>(null)
  const save = useProfileMutation((values: Values) => {
    const body = { ...values, weekly_budget_hours: Number(values.weekly_budget_hours) }
    return profile ? updateProfile(body) : createProfile(body)
  })

  const onSubmit = form.handleSubmit((values) => {
    setFormError(null)
    save.mutate(values, {
      onSuccess: (saved) => form.reset(toValues(saved)),
      onError: (error) => setFormError(applyFieldErrors(error, FIELDS, form.setError)),
    })
  })

  return (
    <form onSubmit={onSubmit} noValidate aria-label="Profile details">
      <Stack spacing={2.5}>
        {formError && <Alert severity="error">{formError.message}</Alert>}
        <Controller
          name="display_name"
          control={form.control}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Display name"
              error={!!fieldState.error}
              helperText={fieldState.error?.message}
            />
          )}
        />
        <Controller
          name="background"
          control={form.control}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Background"
              multiline
              minRows={3}
              error={!!fieldState.error}
              helperText={
                fieldState.error?.message ?? 'What you work on and what you already know.'
              }
            />
          )}
        />
        <Controller
          name="weekly_budget_hours"
          control={form.control}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Weekly study time (hours)"
              type="number"
              slotProps={{ htmlInput: { step: 0.5 } }}
              sx={{ maxWidth: 280 }}
              error={!!fieldState.error}
              helperText={fieldState.error?.message}
            />
          )}
        />
        <Controller
          name="preferences.formats"
          control={form.control}
          render={({ field, fieldState }) => (
            <FormControl component="fieldset" error={!!fieldState.error}>
              <FormLabel component="legend">Preferred formats</FormLabel>
              <FormGroup row>
                {CONTENT_FORMATS.map((format) => (
                  <FormControlLabel
                    key={format}
                    label={FORMAT_LABELS[format]}
                    control={
                      <Checkbox
                        checked={field.value.includes(format)}
                        onChange={(_, checked) =>
                          field.onChange(
                            checked
                              ? CONTENT_FORMATS.filter(
                                  (f) => f === format || field.value.includes(f),
                                )
                              : field.value.filter((f) => f !== format),
                          )
                        }
                      />
                    }
                  />
                ))}
              </FormGroup>
              {fieldState.error && <FormHelperText>{fieldState.error.message}</FormHelperText>}
            </FormControl>
          )}
        />
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
          <Controller
            name="preferences.depth"
            control={form.control}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                select
                label="Explanation depth"
                error={!!fieldState.error}
                helperText={fieldState.error?.message}
              >
                {DEPTHS.map((depth) => (
                  <MenuItem key={depth} value={depth}>
                    {DEPTH_LABELS[depth]}
                  </MenuItem>
                ))}
              </TextField>
            )}
          />
          <Controller
            name="preferences.language"
            control={form.control}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                label="Narration language"
                error={!!fieldState.error}
                helperText={fieldState.error?.message ?? 'Language code, for example en or pt-BR'}
              />
            )}
          />
        </Stack>
        <Box>
          <Button type="submit" variant="contained" loading={save.isPending}>
            {profile ? 'Save profile' : 'Create profile'}
          </Button>
        </Box>
      </Stack>
    </form>
  )
}
