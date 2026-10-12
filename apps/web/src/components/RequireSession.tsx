import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { Alert, Button, LinearProgress } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { authKeys, getAuthState } from '../api/auth'

/** Route guard: wizard when no password exists, login page when signed out. */
export function RequireSession({ children }: { children: ReactNode }) {
  const location = useLocation()
  const auth = useQuery({ queryKey: authKeys.me(), queryFn: getAuthState })

  if (auth.isPending) return <LinearProgress aria-label="Checking session" />
  if (auth.isError) {
    return (
      <Alert
        severity="error"
        action={<Button onClick={() => void auth.refetch()}>Retry</Button>}
        sx={{ m: 2 }}
      >
        Cannot reach the BrainBench API.
      </Alert>
    )
  }
  if (!auth.data.password_set) return <Navigate to="/setup" replace />
  if (!auth.data.authenticated) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }
  return children
}
