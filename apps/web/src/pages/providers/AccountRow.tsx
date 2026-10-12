import { useState } from 'react'
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  LinearProgress,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import type { AccountRead } from '../../api/providers'
import { StatusChip } from './StatusChip'
import { accountLabel, formatCount, formatDateTime } from './format'

type Props = {
  account: AccountRead
  onDisconnect: () => void
  onReconnect: () => void
  onPriority: (priority: number) => void
  busy: boolean
}

export function AccountRow({ account, onDisconnect, onReconnect, onPriority, busy }: Props) {
  const [confirming, setConfirming] = useState(false)
  const [priority, setPriority] = useState(String(account.priority))
  const org = [account.org_name, account.org_id].filter(Boolean).join(' · ')
  const usage = account.usage_today

  const savePriority = () => {
    const value = Number.parseInt(priority, 10)
    if (Number.isFinite(value) && value !== account.priority) onPriority(value)
    else setPriority(String(account.priority))
  }

  return (
    <Box
      component="li"
      sx={{ listStyle: 'none', border: 1, borderColor: 'divider', borderRadius: 1, p: 2 }}
    >
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: 'baseline' }}>
        <Typography sx={{ fontWeight: 600 }}>{accountLabel(account)}</Typography>
        {org && <Typography color="text.secondary">{org}</Typography>}
        <StatusChip account={account} />
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        Access token expires {formatDateTime(account.expires_at)}. Today: {formatCount(usage.calls)}{' '}
        calls, {formatCount(usage.input_tokens)} input and {formatCount(usage.output_tokens)} output
        tokens.
      </Typography>
      {account.status === 'needs-reauthentication' && account.disabled_cause && (
        <Typography variant="body2" color="warning.main">
          Disabled: {account.disabled_cause}
        </Typography>
      )}
      {account.blocks.map((block) => (
        <Typography key={`${block.scope}-${block.until}`} variant="body2" color="info.main">
          Rate limited ({block.scope}) until {formatDateTime(block.until)}
        </Typography>
      ))}
      {account.windows.length > 0 && (
        <Stack spacing={1} sx={{ mt: 1.5 }} aria-label="Usage windows">
          {account.windows.map((w) => (
            <Box key={w.window}>
              <Typography variant="body2">
                {w.window} window:{' '}
                {w.used_pct === null ? 'not reported' : `${Math.round(w.used_pct)}% used`}
                {w.resets_at && `, resets ${formatDateTime(w.resets_at)}`}
              </Typography>
              {w.used_pct !== null && (
                <LinearProgress
                  variant="determinate"
                  value={w.used_pct}
                  aria-label={`${w.window} window usage`}
                />
              )}
            </Box>
          ))}
        </Stack>
      )}
      <Stack direction="row" spacing={1} sx={{ mt: 2, alignItems: 'center' }}>
        <TextField
          label="Priority"
          type="number"
          size="small"
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          onBlur={savePriority}
          helperText="Lower is tried first"
          sx={{ maxWidth: 140 }}
        />
        {account.status === 'needs-reauthentication' && (
          <Button variant="contained" onClick={onReconnect}>
            Connect again
          </Button>
        )}
        <Button color="error" onClick={() => setConfirming(true)} disabled={busy}>
          Disconnect
        </Button>
      </Stack>
      <Dialog open={confirming} onClose={() => setConfirming(false)}>
        <DialogTitle>Disconnect {accountLabel(account)}?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Its tokens are deleted from the vault. Requests already running finish or fail; no new
            request uses this account.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirming(false)}>Cancel</Button>
          <Button
            color="error"
            onClick={() => {
              setConfirming(false)
              onDisconnect()
            }}
          >
            Disconnect
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
