import { Controller, useForm } from 'react-hook-form'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { Alert, Button, LinearProgress, Stack, TextField } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authKeys, getAuthState, login } from '../../api/auth'
import { ApiError } from '../../api/client'
import { AuthCard } from './AuthCard'

type Values = { password: string }

/** Only same-app paths are accepted as the post-login destination. */
function safeNext(next: string | null): string {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : '/providers'
}

export default function LoginPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const queryClient = useQueryClient()
  const auth = useQuery({ queryKey: authKeys.me(), queryFn: getAuthState })
  const form = useForm<Values>({ defaultValues: { password: '' } })
  const signIn = useMutation({
    mutationFn: (values: Values) => login(values.password),
    onSuccess: (state) => {
      queryClient.setQueryData(authKeys.me(), state)
      void navigate(safeNext(params.get('next')), { replace: true })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        form.setError('password', { message: 'Wrong password' })
      }
    },
  })

  if (auth.isPending) return <LinearProgress aria-label="Checking session" />
  if (auth.data && !auth.data.password_set) return <Navigate to="/setup" replace />

  const formError =
    signIn.error && !(signIn.error instanceof ApiError && signIn.error.status === 401)
      ? signIn.error
      : null

  return (
    <AuthCard title="Sign in" subtitle="Enter the instance password.">
      <form onSubmit={form.handleSubmit((values) => signIn.mutate(values))} noValidate>
        <Stack spacing={2}>
          {formError && <Alert severity="error">{formError.message}</Alert>}
          <Controller
            name="password"
            control={form.control}
            rules={{ required: 'Enter the password' }}
            render={({ field, fieldState }) => (
              <TextField
                {...field}
                type="password"
                label="Password"
                autoComplete="current-password"
                autoFocus
                error={!!fieldState.error}
                helperText={fieldState.error?.message}
              />
            )}
          />
          <Button type="submit" variant="contained" size="large" loading={signIn.isPending}>
            Sign in
          </Button>
        </Stack>
      </form>
    </AuthCard>
  )
}
