import { afterEach, beforeEach, expect, test } from 'bun:test'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../support/authMock'
import { tutorApi } from '../support/tutorMock'
import { routes } from '../../src/routes'
import { renderRoutes } from '../harness'

beforeEach(() => authApi.reset({ password_set: true, authenticated: true }))
afterEach(() => {
  window.localStorage.removeItem('brainbench.tutor.open')
  tutorApi.useReal()
})

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

test('the tutor opens beside every page, bound to the page context, and stays open', async () => {
  tutorApi.useFakes()
  const { router } = renderRoutes(routes, '/providers')
  const user = userEvent.setup()
  await screen.findByRole('heading', { name: 'Providers' })
  expect(tutorApi.listThreads).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Open tutor' }))
  const tutor = await screen.findByRole('heading', { name: 'Tutor' })
  expect(await screen.findByLabelText('Tutor context: General')).toBeDefined()
  expect(tutorApi.listThreads).toHaveBeenCalledWith('general')
  expect(window.localStorage.getItem('brainbench.tutor.open')).toBe('1')
  await router.navigate('/sources')
  expect(tutor.isConnected).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Close tutor', pressed: true }))
  expect(window.localStorage.getItem('brainbench.tutor.open')).toBe('0')
  expect(screen.getByRole('button', { name: 'Open tutor', pressed: false })).toBeDefined()
})
