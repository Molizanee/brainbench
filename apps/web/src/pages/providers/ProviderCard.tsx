import {
  Button,
  Card,
  CardContent,
  CardHeader,
  Chip,
  FormControlLabel,
  Stack,
  Switch,
  Typography,
} from '@mui/material'
import type { LoginKind, ProviderRead } from '../../api/providers'
import { AccountRow } from './AccountRow'

const LOGIN_LABELS: Record<LoginKind, string> = {
  'oauth-code': 'Connect',
  'device-code': 'Connect with a device code',
}

type Props = {
  provider: ProviderRead
  vaultLocked: boolean
  busy: boolean
  onConnect: (kind: LoginKind) => void
  onToggle: (enabled: boolean) => void
  onDisconnect: (accountId: string) => void
  onPriority: (accountId: string, priority: number) => void
}

export function ProviderCard(props: Props) {
  const { provider, vaultLocked, busy, onConnect, onToggle } = props
  const canConnect = provider.enabled && !vaultLocked
  return (
    <Card variant="outlined" component="section" aria-label={provider.name}>
      <CardHeader
        title={provider.name}
        slotProps={{ title: { component: 'h2', variant: 'h2' } }}
        subheader={`Sign-in: ${provider.login_kinds.map((k) => (k === 'oauth-code' ? 'browser OAuth' : 'device code')).join(', ')}`}
        action={
          <FormControlLabel
            control={
              <Switch
                checked={provider.enabled}
                onChange={(e) => onToggle(e.target.checked)}
                disabled={busy}
              />
            }
            label={provider.enabled ? 'Enabled' : 'Disabled'}
          />
        }
      />
      <CardContent>
        <Stack spacing={2}>
          {provider.policy_notice && (
            <Typography variant="body2" color="text.secondary">
              {provider.policy_notice}
            </Typography>
          )}
          <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1 }} aria-label="Models">
            {provider.models.map((m) => (
              <Chip
                key={m.id}
                size="small"
                variant={m.default ? 'filled' : 'outlined'}
                label={m.default ? `${m.name} (default)` : m.name}
              />
            ))}
          </Stack>
          {provider.accounts.length === 0 ? (
            <Typography color="text.secondary">No account connected.</Typography>
          ) : (
            <Stack component="ul" spacing={1.5} sx={{ p: 0, m: 0 }} aria-label="Accounts">
              {provider.accounts.map((account) => (
                <AccountRow
                  key={account.id}
                  account={account}
                  busy={busy}
                  onDisconnect={() => props.onDisconnect(account.id)}
                  onReconnect={() => onConnect(provider.login_kinds[0] ?? 'oauth-code')}
                  onPriority={(priority) => props.onPriority(account.id, priority)}
                />
              ))}
            </Stack>
          )}
          <Stack direction="row" spacing={1}>
            {provider.login_kinds.map((kind, i) => (
              <Button
                key={kind}
                variant={i === 0 ? 'contained' : 'outlined'}
                disabled={!canConnect || busy}
                onClick={() => onConnect(kind)}
              >
                {LOGIN_LABELS[kind]}
              </Button>
            ))}
          </Stack>
          {!provider.enabled && (
            <Typography variant="body2" color="text.secondary">
              Disabled: its models are hidden from routing and its accounts are kept but not used.
            </Typography>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}
