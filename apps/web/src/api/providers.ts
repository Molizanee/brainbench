import { apiDelete, apiGet, apiPatch, apiPost } from './client'

/** Mirrors the models in apps/api/app/models/providers.py. No type here carries a token. */
export type LoginKind = 'oauth-code' | 'device-code'
export type LoginStatus = 'pending' | 'connected' | 'failed' | 'expired'
export type AccountStatus = 'connected' | 'needs-reauthentication' | 'locked' | 'blocked'
export type TaskClass =
  | 'curation'
  | 'storyboarding'
  | 'script-generation'
  | 'script-repair'
  | 'figure-building'
  | 'tutoring'
  | 'topic_extraction'
  | 'topic_merge'
  | 'scoring'
  | 'prerequisites'

export type VaultRead = { state: 'ok' | 'empty' | 'locked'; detail: string; key_path: string }

export type ModelRead = {
  id: string
  name: string
  context_window: number
  max_output: number
  reasoning: boolean
  default: boolean
}

export type BlockRead = { scope: string; until: string; reason: string | null }

export type WindowRead = {
  window: string
  used_pct: number | null
  resets_at: string | null
  fetched_at: string
}

export type UsageTodayRead = { calls: number; input_tokens: number; output_tokens: number }

export type AccountRead = {
  id: string
  provider: string
  email: string | null
  account_id: string | null
  org_id: string | null
  org_name: string | null
  plan: string | null
  status: AccountStatus
  disabled_cause: string | null
  expires_at: string | null
  priority: number
  blocked_until: string | null
  blocks: BlockRead[]
  windows: WindowRead[]
  usage_today: UsageTodayRead
  created_at: string | null
}

export type ProviderRead = {
  id: string
  name: string
  api: string
  enabled: boolean
  login_kinds: LoginKind[]
  callback_port: number | null
  policy_notice: string | null
  models: ModelRead[]
  accounts: AccountRead[]
}

export type ProvidersRead = {
  vault: VaultRead
  callbacks_enabled: boolean
  usage_since: string
  providers: ProviderRead[]
}

export type LoginRead = {
  login_id: string
  provider: string
  kind: LoginKind
  status: LoginStatus
  expires_at: string
  authorize_url: string | null
  paste_code: boolean
  callback_listening: boolean
  callback_port: number | null
  verification_url: string | null
  user_code: string | null
  interval_s: number | null
  credential_id: string | null
  error: string | null
  instructions: string | null
}

export type RouteEntry = { provider: string; model: string }

export type EffectiveRoute = RouteEntry & { source: 'explicit' | 'default'; connected: boolean }

export type RoutingProviderOption = {
  provider: string
  name: string
  connected: boolean
  models: Array<{ id: string; name: string; default: boolean }>
}

export type ProviderSettingsRead = {
  task_classes: TaskClass[]
  enabled_providers: string[] | null
  routing: Partial<Record<TaskClass, RouteEntry>>
  effective: Record<TaskClass, EffectiveRoute | null>
  options: RoutingProviderOption[]
}

export type ProviderSettingsPatch = {
  enabled_providers?: string[] | null
  routing?: Partial<Record<TaskClass, RouteEntry>>
}

export const VAULT_RESET_CONFIRMATION = 'reset'

export const providerKeys = {
  all: ['providers'] as const,
  list: () => [...providerKeys.all, 'list'] as const,
  login: (loginId: string) => [...providerKeys.all, 'login', loginId] as const,
  settings: () => [...providerKeys.all, 'settings'] as const,
}

export const getProviders = () => apiGet<ProvidersRead>('/api/providers')

export const startLogin = (providerId: string, kind: LoginKind) =>
  apiPost<LoginRead>(`/api/providers/${encodeURIComponent(providerId)}/logins`, { kind })

export const getLogin = (loginId: string) =>
  apiGet<LoginRead>(`/api/providers/logins/${encodeURIComponent(loginId)}`)

export const completeLogin = (loginId: string, input: string) =>
  apiPost<LoginRead>(`/api/providers/logins/${encodeURIComponent(loginId)}/complete`, { input })

export const deleteAccount = (accountId: string) =>
  apiDelete(`/api/providers/accounts/${encodeURIComponent(accountId)}`)

export const updateAccount = (accountId: string, priority: number) =>
  apiPatch<void>(`/api/providers/accounts/${encodeURIComponent(accountId)}`, { priority })

export const resetVault = (confirm: string) =>
  apiPost<VaultRead>('/api/providers/vault/reset', { confirm })

export const getProviderSettings = () => apiGet<ProviderSettingsRead>('/api/settings/providers')

// The API also accepts PUT (full replace); the client has no PUT helper, so the page sends
// partial PATCH updates.
export const patchProviderSettings = (patch: ProviderSettingsPatch) =>
  apiPatch<ProviderSettingsRead>('/api/settings/providers', patch)
