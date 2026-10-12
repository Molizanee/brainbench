import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Navigate, useNavigate } from 'react-router'
import { Alert, Button, LinearProgress, Stack, TextField } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authKeys, getAuthState, PASSWORD_MIN_LENGTH, setupPassword } from '../../api/auth'
import { ApiError } from '../../api/client'
import { AuthCard } from './AuthCard'

const schema = z
  .object({
    password: z.string().min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters`),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, { path: ['confirm'], message: 'Passwords do not match' })

type Values = z.infer<typeof schema>

/** First-run wizard: shown while no instance password exists; asks only for the password. */
export default function SetupPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const auth = useQuery({ queryKey: authKeys.me(), queryFn: getAuthState })
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { password: '', confirm: '' },
  })
  const setup = useMutation({
    mutationFn: (values: Values) => setupPassword(values.password),
    onSuccess: (state) => {
      queryClient.setQueryData(authKeys.me(), state)
      void navigate('/providers', { replace: true })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.fields?.password) {
        form.setError('password', { message: error.fields.password })
      }
    },
  })

  if (auth.isPending) return <LinearProgress aria-label="Checking setup" />
  if (auth.data?.password_set && !setup.isSuccess) return <Navigate to="/login" replace />

  const formError =
    setup.error && !(setup.error instanceof ApiError && setup.error.fields) ? setup.error : null

  return (
    <AuthCard
      title="Welcome"
      subtitle="Choose the password that protects this BrainBench instance. You will use it to sign in from any browser."
    >
      <form onSubmit={form.handleSubmit((values) => setup.mutate(values))} noValidate>
        <Stack spacing={2}>
          {formError && <Alert severity="error">{formError.message}</Alert>}
          <Controller
            name="password"
            control={form.control}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                type="password"
                label="Instance password"
                autoComplete="new-password"
                autoFocus
                error={!!fieldState.error}
                helperText={
                  fieldState.error?.message ?? `At least ${PASSWORD_MIN_LENGTH} characters`
                }
              />
            )}
          />
          <Controller
            name="confirm"
            control={form.control}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                type="password"
                label="Confirm password"
                autoComplete="new-password"
                error={!!fieldState.error}
                helperText={fieldState.error?.message}
              />
            )}
          />
          <Button type="submit" variant="contained" size="large" loading={setup.isPending}>
            Set password and continue
          </Button>
        </Stack>
      </form>
    </AuthCard>
  )
}
