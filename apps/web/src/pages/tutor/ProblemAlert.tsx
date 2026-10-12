import { Link as RouterLink } from 'react-router'
import { Alert, AlertTitle, Button, Link } from '@mui/material'
import type { TutorProblem } from './useTutorStream'

export function formatReset(resetAt: string): string {
  return new Date(resetAt).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

type Props = { problem: TutorProblem; onResend: () => void; onDismiss: () => void }

/** No provider, provider limit, or failure; the unsent question stays in the composer. */
export function ProblemAlert({ problem, onResend, onDismiss }: Props) {
  if (problem.kind === 'no-provider') {
    return (
      <Alert severity="warning" onClose={onDismiss}>
        <AlertTitle>No provider connected</AlertTitle>
        Your message was not sent. Connect your ChatGPT or Claude account on the{' '}
        <Link component={RouterLink} to="/providers">
          providers page
        </Link>
        , then send it again.
      </Alert>
    )
  }
  if (problem.kind === 'limit') {
    return (
      <Alert
        severity="warning"
        onClose={onDismiss}
        action={
          <Button color="inherit" size="small" onClick={onResend}>
            Resend
          </Button>
        }
      >
        <AlertTitle>Provider limit reached</AlertTitle>
        {problem.resetAt
          ? `The limit resets at ${formatReset(problem.resetAt)}. Resend your message after that.`
          : 'The provider did not say when the limit resets. Resend your message later.'}
      </Alert>
    )
  }
  return (
    <Alert
      severity="error"
      onClose={onDismiss}
      action={
        <Button color="inherit" size="small" onClick={onResend}>
          Retry
        </Button>
      }
    >
      {problem.message}
    </Alert>
  )
}
