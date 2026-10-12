import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { account, overview, pendingLogin, providersApi } from '../../support/providersMock'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

beforeEach(() => {
  authApi.reset({ password_set: true, authenticated: true })
  providersApi.reset()
})

const card = async (name: string) => screen.findByRole('region', { name })

test('lists every registry provider with login kinds, models and the policy notice', async () => {
  renderRoutes(routes, '/providers')
  const anthropic = await card('Anthropic (Claude Pro/Max)')
  expect(within(anthropic).getByText('Sign-in: browser OAuth')).toBeDefined()
  expect(within(anthropic).getByText('claude-opus-5-5 (default)')).toBeDefined()
  expect(within(anthropic).getByText('No account connected.')).toBeDefined()
  const codex = await card('ChatGPT Plus/Pro (Codex)')
  expect(within(codex).getByText('Sign-in: browser OAuth, device code')).toBeDefined()
  expect(within(codex).getByRole('button', { name: 'Connect with a device code' })).toBeDefined()
  expect(screen.getByText(/outside Anthropic's permitted use/)).toBeDefined()
})

test('connect shows the sign-in link, the listening indicator and the paste field', async () => {
  renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  await user.click(
    within(await card('Anthropic (Claude Pro/Max)')).getByRole('button', { name: 'Connect' }),
  )
  expect(providersApi.startLogin).toHaveBeenCalledWith('anthropic', 'oauth-code')
  const dialog = await screen.findByRole('dialog', { name: 'Connect Anthropic (Claude Pro/Max)' })
  const link = within(dialog).getByRole('link', { name: /Open sign-in page/ })
  expect(link.getAttribute('href')).toBe(providersApi.login.authorize_url)
  expect(link.getAttribute('target')).toBe('_blank')
  expect(within(dialog).getByText(/Listening for the callback on localhost:54545/)).toBeDefined()
  expect(
    within(dialog).getByText(/only works when this browser runs on the Docker host/),
  ).toBeDefined()
  const redirect = 'http://localhost:54545/callback?code=abc&state=s1'
  await user.type(within(dialog).getByLabelText('Redirect URL or code'), redirect)
  await user.click(within(dialog).getByRole('button', { name: 'Complete sign-in' }))
  expect(await within(dialog).findByText(/Connected/)).toBeDefined()
  expect(providersApi.completeLogin).toHaveBeenCalledWith('login-1', redirect)
})

test('paste field is shown without the indicator when callbacks are off', async () => {
  providersApi.login = pendingLogin('oauth-code', { callback_listening: false })
  renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  await user.click(
    within(await card('Anthropic (Claude Pro/Max)')).getByRole('button', { name: 'Connect' }),
  )
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByLabelText('Redirect URL or code')).toBeDefined()
  expect(within(dialog).queryByText(/Listening for the callback/)).toBeNull()
})

test('device code is displayed and polled until approval', async () => {
  providersApi.login = pendingLogin('device-code')
  providersApi.polled = [
    pendingLogin('device-code'),
    { ...pendingLogin('device-code'), status: 'connected' },
  ]
  renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  await user.click(
    within(await card('ChatGPT Plus/Pro (Codex)')).getByRole('button', {
      name: 'Connect with a device code',
    }),
  )
  expect(providersApi.startLogin).toHaveBeenCalledWith('openai-codex', 'device-code')
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByLabelText('User code').textContent).toBe('ABCD-1234')
  expect(
    within(dialog).getByRole('link', { name: 'https://auth.openai.com/codex/device' }),
  ).toBeDefined()
  expect(await within(dialog).findByText(/Connected/, {}, { timeout: 2000 })).toBeDefined()
  expect(providersApi.getLogin).toHaveBeenCalled()
})

test('expired device code is reported', async () => {
  providersApi.login = pendingLogin('device-code')
  providersApi.polled = [
    {
      ...pendingLogin('device-code'),
      status: 'expired',
      error: 'The sign-in expired; start again.',
    },
  ]
  renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  await user.click(
    within(await card('ChatGPT Plus/Pro (Codex)')).getByRole('button', {
      name: 'Connect with a device code',
    }),
  )
  expect(
    await screen.findByText('The sign-in expired; start again.', {}, { timeout: 2000 }),
  ).toBeDefined()
})

test('account statuses, usage windows and disconnect', async () => {
  const data = overview()
  data.providers[0]!.accounts = [
    account({
      windows: [
        {
          window: '5h',
          used_pct: 42,
          resets_at: '2026-10-11T15:00:00Z',
          fetched_at: '2026-10-11T12:00:00Z',
        },
      ],
    }),
    account({
      id: 'acc-2',
      email: 'team@example.com',
      status: 'needs-reauthentication',
      disabled_cause: 'needs-reauthentication',
    }),
    account({
      id: 'acc-3',
      email: 'max@example.com',
      status: 'blocked',
      blocked_until: '2026-10-11T15:00:00Z',
      blocks: [{ scope: '5h', until: '2026-10-11T15:00:00Z', reason: null }],
    }),
  ]
  providersApi.overview = data
  renderRoutes(routes, '/providers')
  const anthropic = await card('Anthropic (Claude Pro/Max)')
  expect(within(anthropic).getByText('connected')).toBeDefined()
  expect(within(anthropic).getByText('needs re-authentication')).toBeDefined()
  expect(within(anthropic).getByText(/^blocked until/)).toBeDefined()
  expect(within(anthropic).getByText(/5h window: 42% used/)).toBeDefined()
  expect(within(anthropic).getByRole('progressbar', { name: '5h window usage' })).toBeDefined()
  expect(within(anthropic).getAllByText(/3 calls/)).toHaveLength(3)
  expect(within(anthropic).getByRole('button', { name: 'Connect again' })).toBeDefined()
  const user = userEvent.setup()
  await user.click(within(anthropic).getAllByRole('button', { name: 'Disconnect' })[0]!)
  const confirm = await screen.findByRole('dialog', { name: /Disconnect dev@example.com/ })
  await user.click(within(confirm).getByRole('button', { name: 'Disconnect' }))
  await waitFor(() => expect(providersApi.deleteAccount).toHaveBeenCalledWith('acc-1'))
})

test('locked vault shows recovery options and reset needs the typed confirmation', async () => {
  const data = overview({
    vault: { state: 'locked', detail: 'locked-missing', key_path: '/data/vault.key' },
  })
  data.providers[0]!.accounts = [account({ status: 'locked' })]
  providersApi.overview = data
  renderRoutes(routes, '/providers')
  expect(await screen.findByText('Credential vault locked')).toBeDefined()
  expect(screen.getByText(/\/data\/vault\.key/)).toBeDefined()
  expect(screen.getByText('locked')).toBeDefined()
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Reset vault' }))
  const dialog = await screen.findByRole('dialog', { name: 'Reset the credential vault?' })
  const submit = within(dialog).getByRole('button', { name: 'Delete accounts and reset' })
  expect(submit.hasAttribute('disabled')).toBe(true)
  await user.type(within(dialog).getByLabelText('Confirmation'), 'reset')
  await user.click(submit)
  await waitFor(() => expect(providersApi.resetVault).toHaveBeenCalledWith('reset'))
})

test('disabling a provider sends the remaining enabled providers', async () => {
  renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  const anthropic = await card('Anthropic (Claude Pro/Max)')
  await user.click(within(anthropic).getByRole('switch', { name: 'Enabled' }))
  await waitFor(() =>
    expect(providersApi.patchProviderSettings).toHaveBeenCalledWith({
      enabled_providers: ['openai-codex'],
    }),
  )
})

test('routing form saves an explicit model for a task class', async () => {
  renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  const routing = await card('Routing')
  await user.click(within(routing).getByRole('combobox', { name: 'Tutoring' }))
  await user.click(await screen.findByRole('option', { name: 'Claude Haiku 5.5' }))
  await user.click(within(routing).getByRole('button', { name: 'Save routing' }))
  await waitFor(() =>
    expect(providersApi.patchProviderSettings).toHaveBeenCalledWith({
      routing: { tutoring: { provider: 'anthropic', model: 'claude-haiku-5-5' } },
    }),
  )
  expect(
    within(routing).getAllByText(/Default \(Anthropic \(Claude Pro\/Max\), Claude Opus 5\.5\)/)
      .length,
  ).toBeGreaterThan(0)
})
