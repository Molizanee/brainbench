import { useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Link,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { completeLogin, getLogin, providerKeys, type LoginRead } from '../../api/providers'

type Props = { login: LoginRead; providerName: string; onClose: () => void }

/** Drives one sign-in: authorize link, paste field, callback indicator, or device code. */
export function ConnectDialog({ login: started, providerName, onClose }: Props) {
  const queryClient = useQueryClient()
  const [pasted, setPasted] = useState('')
  const { data: login = started } = useQuery({
    queryKey: providerKeys.login(started.login_id),
    queryFn: () => getLogin(started.login_id),
    initialData: started,
    refetchInterval: (query) =>
      query.state.data?.status === 'pending' ? (query.state.data.interval_s ?? 2) * 1000 : false,
  })
  const complete = useMutation({
    mutationFn: () => completeLogin(started.login_id, pasted),
    onSuccess: (result) => queryClient.setQueryData(providerKeys.login(started.login_id), result),
  })
  const connected = login.status === 'connected'

  useEffect(() => {
    if (connected) void queryClient.invalidateQueries({ queryKey: providerKeys.all })
  }, [connected, queryClient])

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Connect {providerName}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {connected && <Alert severity="success">Connected. The account is ready to use.</Alert>}
          {(login.status === 'failed' || login.status === 'expired') && (
            <Alert severity="error">{login.error ?? `Sign-in ${login.status}.`}</Alert>
          )}
          {login.status === 'pending' && login.kind === 'oauth-code' && (
            <>
              <Typography>{login.instructions}</Typography>
              <Box>
                <Button
                  variant="contained"
                  component="a"
                  href={login.authorize_url ?? undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                  endIcon={<OpenInNewIcon />}
                >
                  Open sign-in page
                </Button>
              </Box>
              {login.callback_listening && (
                <Alert severity="info" icon={<CircularProgress size={18} />}>
                  Listening for the callback on localhost:{login.callback_port}. This only works
                  when this browser runs on the Docker host; otherwise paste the redirect below.
                </Alert>
              )}
              <Stack spacing={1}>
                <TextField
                  label="Redirect URL or code"
                  value={pasted}
                  onChange={(e) => setPasted(e.target.value)}
                  helperText="After consent, paste the URL of the page that failed to load (or the code shown)."
                  error={complete.isError}
                />
                {complete.isError && <Alert severity="error">{complete.error.message}</Alert>}
                <Box>
                  <Button
                    variant="outlined"
                    disabled={!pasted.trim()}
                    loading={complete.isPending}
                    onClick={() => complete.mutate()}
                  >
                    Complete sign-in
                  </Button>
                </Box>
              </Stack>
            </>
          )}
          {login.status === 'pending' && login.kind === 'device-code' && (
            <>
              <Typography>
                Open{' '}
                <Link href={login.verification_url ?? undefined} target="_blank" rel="noopener">
                  {login.verification_url}
                </Link>
                , sign in, and enter this code:
              </Typography>
              <Typography
                variant="h2"
                component="p"
                aria-label="User code"
                sx={{ fontFamily: 'monospace', letterSpacing: 2 }}
              >
                {login.user_code}
              </Typography>
              <Typography color="text.secondary">Waiting for approval…</Typography>
              <LinearProgress aria-label="Waiting for approval" />
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{connected ? 'Done' : 'Close'}</Button>
      </DialogActions>
    </Dialog>
  )
}
