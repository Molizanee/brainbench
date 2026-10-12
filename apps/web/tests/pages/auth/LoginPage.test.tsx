import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { ApiError } from '../../../src/api/client'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

beforeEach(() => authApi.reset({ password_set: true, authenticated: false }))

test('signed-out visit goes to login and returns to the page after sign-in', async () => {
  const { router } = renderRoutes(routes, '/providers')
  await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  expect(router.state.location.search).toBe('?next=%2Fproviders')

  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Password'), 'correct horse')
  authApi.state = { password_set: true, authenticated: true }
  await user.click(screen.getByRole('button', { name: 'Sign in' }))

  await waitFor(() => expect(router.state.location.pathname).toBe('/providers'))
  expect(authApi.login).toHaveBeenCalledWith('correct horse')
})

test('wrong password is shown on the field', async () => {
  authApi.login.mockImplementationOnce(async () => {
    throw new ApiError(401, 'invalid-password', 'Wrong password.')
  })
  renderRoutes(routes, '/login')
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Password'), 'nope')
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByText('Wrong password')).toBeDefined()
})

test('external next targets are ignored', async () => {
  const { router } = renderRoutes(routes, '/login?next=//evil.example')
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Password'), 'correct horse')
  authApi.state = { password_set: true, authenticated: true }
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
  await waitFor(() => expect(router.state.location.pathname).toBe('/providers'))
})
