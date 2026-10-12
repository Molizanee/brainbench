import { mock } from 'bun:test'
import type {
  AccountRead,
  LoginKind,
  LoginRead,
  ProviderSettingsPatch,
  ProviderSettingsRead,
  ProvidersRead,
  VaultRead,
} from '../../src/api/providers'

const models = (ids: string[]) =>
  ids.map((id, i) => ({
    id,
    name: id,
    context_window: 1_000_000,
    max_output: 128_000,
    reasoning: true,
    default: i === 0,
  }))

export function overview(overrides: Partial<ProvidersRead> = {}): ProvidersRead {
  return {
    vault: {
      state: 'empty',
      detail: 'created with the first connected account',
      key_path: '/data/vault.key',
    },
    callbacks_enabled: true,
    usage_since: '2026-10-11T00:00:00Z',
    providers: [
      {
        id: 'anthropic',
        name: 'Anthropic (Claude Pro/Max)',
        api: 'anthropic-messages',
        enabled: true,
        login_kinds: ['oauth-code'],
        callback_port: 54545,
        policy_notice: 'Anthropic policy notice.',
        models: models(['claude-opus-5-5', 'claude-sonnet-5-5']),
        accounts: [],
      },
      {
        id: 'openai-codex',
        name: 'ChatGPT Plus/Pro (Codex)',
        api: 'openai-responses',
        enabled: true,
        login_kinds: ['oauth-code', 'device-code'],
        callback_port: 1455,
        policy_notice: null,
        models: models(['gpt-6.1-sol', 'gpt-6-sol']),
        accounts: [],
      },
    ],
    ...overrides,
  }
}

export function account(overrides: Partial<AccountRead> = {}): AccountRead {
  return {
    id: 'acc-1',
    provider: 'anthropic',
    email: 'dev@example.com',
    account_id: 'acct-1',
    org_id: 'org-1',
    org_name: 'Personal',
    plan: null,
    status: 'connected',
    disabled_cause: null,
    expires_at: '2026-10-11T20:00:00Z',
    priority: 0,
    blocked_until: null,
    blocks: [],
    windows: [],
    usage_today: { calls: 3, input_tokens: 1200, output_tokens: 300 },
    created_at: '2026-10-11T10:00:00Z',
    ...overrides,
  }
}

export function pendingLogin(kind: LoginKind, overrides: Partial<LoginRead> = {}): LoginRead {
  return {
    login_id: 'login-1',
    provider: kind === 'device-code' ? 'openai-codex' : 'anthropic',
    kind,
    status: 'pending',
    expires_at: '2026-10-11T12:10:00Z',
    authorize_url:
      kind === 'oauth-code'
        ? 'https://claude.ai/oauth/authorize?client_id=x&code_challenge=abc&state=s1'
        : null,
    paste_code: kind === 'oauth-code',
    callback_listening: kind === 'oauth-code',
    callback_port: kind === 'oauth-code' ? 54545 : null,
    verification_url: kind === 'device-code' ? 'https://auth.openai.com/codex/device' : null,
    user_code: kind === 'device-code' ? 'ABCD-1234' : null,
    interval_s: kind === 'device-code' ? 0.02 : null,
    credential_id: null,
    error: null,
    instructions: 'Complete login in your browser.',
    ...overrides,
  }
}

export function routingSettings(): ProviderSettingsRead {
  const effective = {
    provider: 'anthropic',
    model: 'claude-opus-5-5',
    source: 'default' as const,
    connected: true,
  }
  return {
    task_classes: [
      'curation',
      'storyboarding',
      'script-generation',
      'script-repair',
      'figure-building',
      'tutoring',
    ],
    enabled_providers: null,
    routing: {},
    effective: {
      curation: effective,
      storyboarding: effective,
      'script-generation': effective,
      'script-repair': effective,
      'figure-building': effective,
      tutoring: effective,
    },
    options: [
      {
        provider: 'anthropic',
        name: 'Anthropic (Claude Pro/Max)',
        connected: true,
        models: [
          { id: 'claude-opus-5-5', name: 'Claude Opus 5.5', default: true },
          { id: 'claude-haiku-5-5', name: 'Claude Haiku 5.5', default: false },
        ],
      },
      {
        provider: 'openai-codex',
        name: 'ChatGPT Plus/Pro (Codex)',
        connected: false,
        models: [{ id: 'gpt-6.1-sol', name: 'GPT-6.1 Sol', default: true }],
      },
    ],
  }
}

/** Module-boundary mock of src/api/providers (same pattern as authMock). */
export const providersApi = {
  overview: overview(),
  settings: routingSettings(),
  login: pendingLogin('oauth-code'),
  polled: [] as LoginRead[],
  getProviders: mock(async (): Promise<ProvidersRead> => providersApi.overview),
  getProviderSettings: mock(async (): Promise<ProviderSettingsRead> => providersApi.settings),
  startLogin: mock(
    async (_provider: string, _kind: LoginKind): Promise<LoginRead> => providersApi.login,
  ),
  getLogin: mock(
    async (_id: string): Promise<LoginRead> => providersApi.polled.shift() ?? providersApi.login,
  ),
  // Completing a login connects it server side, so later polls see the connected state too.
  completeLogin: mock(async (_id: string, _input: string): Promise<LoginRead> => {
    providersApi.login = {
      ...providersApi.login,
      status: 'connected',
      callback_listening: false,
      credential_id: 'acc-1',
    }
    return providersApi.login
  }),
  deleteAccount: mock(async (_id: string): Promise<void> => undefined),
  updateAccount: mock(async (_id: string, _priority: number): Promise<void> => undefined),
  resetVault: mock(async (_confirm: string): Promise<VaultRead> => ({
    state: 'ok',
    detail: 'ok',
    key_path: '/data/vault.key',
  })),
  patchProviderSettings: mock(
    async (_patch: ProviderSettingsPatch): Promise<ProviderSettingsRead> => providersApi.settings,
  ),
  reset() {
    providersApi.overview = overview()
    providersApi.settings = routingSettings()
    providersApi.login = pendingLogin('oauth-code')
    providersApi.polled = []
    for (const fn of [
      providersApi.getProviders,
      providersApi.getProviderSettings,
      providersApi.startLogin,
      providersApi.getLogin,
      providersApi.completeLogin,
      providersApi.deleteAccount,
      providersApi.updateAccount,
      providersApi.resetVault,
      providersApi.patchProviderSettings,
    ]) {
      fn.mockClear()
    }
  },
}

const real = await import('../../src/api/providers')

mock.module('../../src/api/providers', () => ({
  ...real,
  getProviders: () => providersApi.getProviders(),
  getProviderSettings: () => providersApi.getProviderSettings(),
  startLogin: (provider: string, kind: LoginKind) => providersApi.startLogin(provider, kind),
  getLogin: (id: string) => providersApi.getLogin(id),
  completeLogin: (id: string, input: string) => providersApi.completeLogin(id, input),
  deleteAccount: (id: string) => providersApi.deleteAccount(id),
  updateAccount: (id: string, priority: number) => providersApi.updateAccount(id, priority),
  resetVault: (confirm: string) => providersApi.resetVault(confirm),
  patchProviderSettings: (patch: ProviderSettingsPatch) =>
    providersApi.patchProviderSettings(patch),
}))
