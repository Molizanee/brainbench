import { Chip } from '@mui/material'
import type { AccountRead } from '../../api/providers'
import { formatDateTime } from './format'

/** Account status as text plus color (color is never the only signal). */
export function StatusChip({ account }: { account: AccountRead }) {
  switch (account.status) {
    case 'connected':
      return <Chip size="small" color="success" label="connected" />
    case 'needs-reauthentication':
      return <Chip size="small" color="warning" label="needs re-authentication" />
    case 'locked':
      return <Chip size="small" color="error" label="locked" />
    case 'blocked':
      return (
        <Chip
          size="small"
          color="info"
          label={`blocked until ${formatDateTime(account.blocked_until)}`}
        />
      )
  }
}
