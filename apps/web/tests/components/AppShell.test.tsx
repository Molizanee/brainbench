import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../support/authMock'
import { routes } from '../../src/routes'
import { renderRoutes } from '../harness'

beforeEach(() => authApi.reset({ password_set: true, authenticated: true }))

test('shell renders navigation from the route table and the section page', async () => {
  renderRoutes(routes, '/')
  expect(await screen.findByRole('heading', { name: 'Providers' })).toBeDefined()
  const nav = screen.getByRole('navigation', { name: 'Main navigation' })
  expect(nav.textContent).toContain('Providers')
})

test('sign out calls the API and goes to the login page', async () => {
  const { router } = renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  await screen.findByRole('heading', { name: 'Providers' })
  authApi.state = { password_set: true, authenticated: false }
  await user.click(screen.getByRole('button', { name: 'Sign out' }))
  await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  expect(authApi.logout).toHaveBeenCalled()
})
