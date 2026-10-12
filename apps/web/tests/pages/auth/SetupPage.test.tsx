import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

beforeEach(() => authApi.reset({ password_set: false, authenticated: false }))

test('fresh instance redirects to the wizard, which asks only for the password', async () => {
  const { router } = renderRoutes(routes, '/')
  expect(await screen.findByRole('heading', { name: 'Welcome' })).toBeDefined()
  expect(router.state.location.pathname).toBe('/setup')
  expect(screen.getAllByLabelText(/password/i)).toHaveLength(2)
})

test('mismatched confirmation is rejected before calling the API', async () => {
  renderRoutes(routes, '/setup')
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Instance password'), 'correct horse')
  await user.type(screen.getByLabelText('Confirm password'), 'correct hose')
  await user.click(screen.getByRole('button', { name: 'Set password and continue' }))
  expect(await screen.findByText('Passwords do not match')).toBeDefined()
  expect(authApi.setupPassword).not.toHaveBeenCalled()
})

test('setting the password signs in and lands on the providers page', async () => {
  const { router } = renderRoutes(routes, '/setup')
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Instance password'), 'correct horse')
  await user.type(screen.getByLabelText('Confirm password'), 'correct horse')
  authApi.state = { password_set: true, authenticated: true }
  await user.click(screen.getByRole('button', { name: 'Set password and continue' }))

  await waitFor(() => expect(router.state.location.pathname).toBe('/providers'))
  expect(authApi.setupPassword).toHaveBeenCalledWith('correct horse')
  expect(await screen.findByRole('heading', { name: 'Providers' })).toBeDefined()
})

test('wizard is not shown once a password exists', async () => {
  authApi.reset({ password_set: true, authenticated: false })
  const { router } = renderRoutes(routes, '/setup')
  await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
})
