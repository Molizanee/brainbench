import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { profileApi } from '../../support/profileMock'
import { ApiError } from '../../../src/api/client'
import type { ProfileRead } from '../../../src/api/profile'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

const PROFILE: ProfileRead = {
  id: 'p1',
  display_name: 'Ada',
  background: 'Backend engineer',
  weekly_budget_hours: 5,
  preferences: { formats: ['video', 'figure', 'text'], depth: 'intuition-first', language: 'en' },
  current_skills: [{ id: 's1', name: 'Linear algebra', level: 'working' }],
  target_skills: [],
  version: 2,
  created_at: '2026-10-11T00:00:00Z',
  updated_at: '2026-10-11T00:00:00Z',
}

beforeEach(() => {
  authApi.reset({ password_set: true, authenticated: true })
  profileApi.reset()
  profileApi.getProfile.mockImplementation(async () => PROFILE)
})

test('profile is a navigation section', async () => {
  renderRoutes(routes, '/profile')
  expect(await screen.findByRole('heading', { name: 'Profile' })).toBeDefined()
  const nav = screen.getByRole('navigation', { name: 'Main navigation' })
  expect(nav.textContent).toContain('Profile')
  expect(screen.getByText('Version 2')).toBeDefined()
})

test('without a profile the create form is shown and creates one', async () => {
  profileApi.getProfile.mockImplementation(async () => null)
  profileApi.createProfile.mockImplementation(async () => ({ ...PROFILE, version: 1 }))
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()

  await user.type(await screen.findByLabelText('Display name'), 'Ada')
  await user.type(screen.getByLabelText('Weekly study time (hours)'), '6')
  await user.click(screen.getByLabelText('Text'))
  await user.click(screen.getByRole('button', { name: 'Create profile' }))

  await waitFor(() => expect(profileApi.createProfile).toHaveBeenCalled())
  expect(profileApi.createProfile.mock.calls[0]?.[0]).toEqual({
    display_name: 'Ada',
    background: '',
    weekly_budget_hours: 6,
    preferences: { formats: ['video', 'figure'], depth: 'intuition-first', language: 'en' },
  })
  expect(await screen.findByRole('heading', { name: 'Current skills' })).toBeDefined()
  expect(screen.getByText('Version 1')).toBeDefined()
})

test('time budget error from the API is shown on its field', async () => {
  profileApi.updateProfile.mockImplementation(async () => {
    throw new ApiError(422, 'validation-error', 'Some fields are invalid', {
      weekly_budget_hours: 'Input should be greater than 0',
    })
  })
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()

  const budget = await screen.findByLabelText('Weekly study time (hours)')
  await user.clear(budget)
  await user.type(budget, '0')
  await user.click(screen.getByRole('button', { name: 'Save profile' }))

  expect(await screen.findByText('Input should be greater than 0')).toBeDefined()
  expect(budget.getAttribute('aria-invalid')).toBe('true')
  expect(profileApi.updateProfile.mock.calls[0]?.[0]).toMatchObject({ weekly_budget_hours: 0 })
})

test('nested API error paths land on the closest form field', async () => {
  profileApi.updateProfile.mockImplementation(async () => {
    throw new ApiError(422, 'validation-error', 'Some fields are invalid', {
      'preferences.formats.0': "Input should be 'video', 'figure' or 'text'",
    })
  })
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Save profile' }))

  const formats = screen.getByRole('group', { name: 'Preferred formats' })
  expect(
    await within(formats).findByText("Input should be 'video', 'figure' or 'text'"),
  ).toBeDefined()
})

test('duplicate skill name is shown on the name field and the dialog stays open', async () => {
  const message = "'linear algebra' is already one of your current skills."
  profileApi.addSkill.mockImplementation(async () => {
    throw new ApiError(422, 'duplicate-skill', message, { name: message })
  })
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()

  await user.click(await screen.findByRole('button', { name: 'Add skill' }))
  const dialog = await screen.findByRole('dialog', { name: 'Add current skill' })
  const name = within(dialog).getByLabelText('Skill')
  await user.type(name, 'linear algebra')
  await user.click(within(dialog).getByRole('button', { name: 'Add' }))

  expect(await within(dialog).findByText(message)).toBeDefined()
  expect(name.getAttribute('aria-invalid')).toBe('true')
  expect(profileApi.addSkill).toHaveBeenCalledWith({ name: 'linear algebra', level: 'working' })
  expect(screen.getByRole('dialog', { name: 'Add current skill' })).toBeDefined()
})

test('target at or below the current level is shown on the target level field', async () => {
  const message =
    "The target level must be higher than your current level: 'Linear algebra' is already at working."
  profileApi.addTarget.mockImplementation(async () => {
    throw new ApiError(422, 'target-not-above-current', message, { target_level: message })
  })
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()

  await user.click(await screen.findByRole('button', { name: 'Add target' }))
  const dialog = await screen.findByRole('dialog', { name: 'Add target skill' })
  await user.type(within(dialog).getByLabelText('Skill'), 'Linear algebra')
  await user.click(within(dialog).getByRole('combobox', { name: 'Target level' }))
  await user.click(await screen.findByRole('option', { name: 'Working' }))
  await user.click(within(dialog).getByRole('button', { name: 'Add' }))

  expect(await within(dialog).findByText(message)).toBeDefined()
  expect(within(dialog).getByLabelText('Skill').getAttribute('aria-invalid')).toBe('false')
  expect(profileApi.addTarget).toHaveBeenCalledWith({
    name: 'Linear algebra',
    target_level: 'working',
    priority: 'medium',
    reason: null,
  })
})

test('a saved skill closes the dialog and updates the list', async () => {
  profileApi.addSkill.mockImplementation(async (body) => ({
    ...PROFILE,
    version: 3,
    current_skills: [...PROFILE.current_skills, { id: 's2', name: body.name, level: body.level }],
  }))
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()

  await user.click(await screen.findByRole('button', { name: 'Add skill' }))
  const dialog = await screen.findByRole('dialog', { name: 'Add current skill' })
  await user.type(within(dialog).getByLabelText('Skill'), 'Rust')
  await user.click(within(dialog).getByRole('button', { name: 'Add' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const list = screen.getByRole('list', { name: 'Current skills' })
  expect(within(list).getByText('Rust')).toBeDefined()
  expect(screen.getByText('Version 3')).toBeDefined()
})

test('an error without fields is shown as an alert in the dialog', async () => {
  profileApi.addSkill.mockImplementation(async () => {
    throw new ApiError(404, 'profile-missing', 'No learner profile exists yet; create one first.')
  })
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()

  await user.click(await screen.findByRole('button', { name: 'Add skill' }))
  const dialog = await screen.findByRole('dialog', { name: 'Add current skill' })
  await user.type(within(dialog).getByLabelText('Skill'), 'Go')
  await user.click(within(dialog).getByRole('button', { name: 'Add' }))

  expect(
    await within(dialog).findByText('No learner profile exists yet; create one first.'),
  ).toBeDefined()
})

test('deleting a skill calls the API', async () => {
  profileApi.deleteSkill.mockImplementation(async () => ({
    ...PROFILE,
    version: 3,
    current_skills: [],
  }))
  renderRoutes(routes, '/profile')
  const user = userEvent.setup()

  await user.click(await screen.findByRole('button', { name: 'Delete Linear algebra' }))
  await waitFor(() => expect(profileApi.deleteSkill).toHaveBeenCalledWith('s1'))
  expect(await screen.findByText(/No current skills yet/)).toBeDefined()
})
