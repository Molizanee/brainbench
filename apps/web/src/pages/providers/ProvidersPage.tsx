import { useState } from 'react'
import { Alert, Button, Skeleton, Stack, Typography } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteAccount,
  getProviderSettings,
  getProviders,
  patchProviderSettings,
  providerKeys,
  startLogin,
  updateAccount,
  type LoginKind,
  type LoginRead,
  type ProvidersRead,
} from '../../api/providers'
import { ConnectDialog } from './ConnectDialog'
import { ProviderCard } from './ProviderCard'
import { RoutingCard } from './RoutingCard'
import { VaultAlert } from './VaultAlert'

function enabledAfterToggle(data: ProvidersRead, id: string, enabled: boolean): string[] | null {
  const ids = data.providers.filter((p) => (p.id === id ? enabled : p.enabled)).map((p) => p.id)
  return ids.length === data.providers.length ? null : ids
}

export default function ProvidersPage() {
  const queryClient = useQueryClient()
  const providers = useQuery({ queryKey: providerKeys.list(), queryFn: getProviders })
  const settings = useQuery({ queryKey: providerKeys.settings(), queryFn: getProviderSettings })
  const [login, setLogin] = useState<LoginRead | null>(null)
  const invalidate = () => queryClient.invalidateQueries({ queryKey: providerKeys.all })

  const start = useMutation({
    mutationFn: ({ provider, kind }: { provider: string; kind: LoginKind }) =>
      startLogin(provider, kind),
    onSuccess: setLogin,
  })
  const toggle = useMutation({
    mutationFn: (enabled: string[] | null) => patchProviderSettings({ enabled_providers: enabled }),
    onSuccess: invalidate,
  })
  const disconnect = useMutation({ mutationFn: deleteAccount, onSuccess: invalidate })
  const priority = useMutation({
    mutationFn: ({ id, value }: { id: string; value: number }) => updateAccount(id, value),
    onSuccess: invalidate,
  })
  const busy = start.isPending || toggle.isPending || disconnect.isPending

  return (
    <>
      <Typography variant="h1" gutterBottom>
        Providers
      </Typography>
      <Alert severity="warning" sx={{ mb: 3 }}>
        BrainBench signs in with your own ChatGPT or Claude subscription. Using subscription sign-in
        from a third-party application is outside Anthropic&apos;s permitted use, and OpenAI
        documents ChatGPT sign-in for Codex only; the account you connect bears that risk. Each
        provider can be disabled below.
      </Alert>
      {providers.isPending && <Skeleton variant="rounded" height={240} aria-label="Loading" />}
      {providers.isError && (
        <Alert
          severity="error"
          action={<Button onClick={() => void providers.refetch()}>Retry</Button>}
        >
          Could not load providers: {providers.error.message}
        </Alert>
      )}
      {providers.data && (
        <Stack spacing={3}>
          <VaultAlert vault={providers.data.vault} />
          {start.isError && <Alert severity="error">{start.error.message}</Alert>}
          {disconnect.isError && <Alert severity="error">{disconnect.error.message}</Alert>}
          {providers.data.providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              vaultLocked={providers.data.vault.state === 'locked'}
              busy={busy}
              onConnect={(kind) => start.mutate({ provider: provider.id, kind })}
              onToggle={(enabled) =>
                toggle.mutate(enabledAfterToggle(providers.data, provider.id, enabled))
              }
              onDisconnect={(id) => disconnect.mutate(id)}
              onPriority={(id, value) => priority.mutate({ id, value })}
            />
          ))}
          {settings.data && (
            <RoutingCard key={JSON.stringify(settings.data.routing)} settings={settings.data} />
          )}
        </Stack>
      )}
      {login && (
        <ConnectDialog
          login={login}
          providerName={
            providers.data?.providers.find((p) => p.id === login.provider)?.name ?? login.provider
          }
          onClose={() => {
            setLogin(null)
            void invalidate()
          }}
        />
      )}
    </>
  )
}
