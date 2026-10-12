import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import {
  consensusTarget,
  emptyProgress,
  loggedResult,
  progressApi,
  sampleProgress,
} from '../../support/progressMock'
import { ApiError } from '../../../src/api/client'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

beforeEach(() => {
  authApi.reset({ password_set: true, authenticated: true })
  progressApi.reset(sampleProgress)
})

test('progress section is in the navigation', async () => {
  renderRoutes(routes, '/progress')
  expect(await screen.findByRole('heading', { name: 'Progress' })).toBeDefined()
  const nav = screen.getByRole('navigation', { name: 'Main navigation' })
  expect(within(nav).getByText('Progress')).toBeDefined()
})

test('shows per-target counts, minutes, and the weekly budget used', async () => {
  renderRoutes(routes, '/progress')
  const card = await screen.findByRole('region', { name: 'Distributed consensus' })
  const inCard = within(card)
  expect(inCard.getByLabelText('Mastered: 1')).toBeDefined()
  expect(inCard.getByLabelText('In progress: 1')).toBeDefined()
  expect(inCard.getByLabelText('Not started: 1')).toBeDefined()
  expect(inCard.getByLabelText('Studied: 3 h')).toBeDefined()
  expect(inCard.getByLabelText('Remaining: 4 h')).toBeDefined()
  expect(inCard.getByText('Raft')).toBeDefined()

  expect(screen.getByText('60%')).toBeDefined()
  expect(screen.getByText('3 h of 5 h')).toBeDefined()
  const bar = screen.getByRole('progressbar', { name: 'Weekly budget used' })
  expect(bar.getAttribute('aria-valuenow')).toBe('60')
  expect(progressApi.getProgress).toHaveBeenCalledWith(-new Date().getTimezoneOffset())
})

test('empty state without target skills, budget hint without a profile', async () => {
  progressApi.reset(emptyProgress)
  renderRoutes(routes, '/progress')
  expect(await screen.findByText('No target skills yet')).toBeDefined()
  expect(screen.getByText(/Set a weekly time budget/)).toBeDefined()
})

test('targets without a plan show the curation hint', async () => {
  progressApi.reset({
    ...sampleProgress,
    plan_version: null,
    targets: [{ ...consensusTarget, topics: [] }],
  })
  renderRoutes(routes, '/progress')
  expect(await screen.findByText(/No study plan yet/)).toBeDefined()
  expect(screen.getByText(/No topic in the current plan serves this target/)).toBeDefined()
})

test('a load error offers a retry', async () => {
  progressApi.getProgress.mockImplementation(async () => {
    throw new ApiError(500, 'http-500', 'Server error')
  })
  renderRoutes(routes, '/progress')
  expect(await screen.findByText('Server error')).toBeDefined()
  progressApi.getProgress.mockImplementation(async () => sampleProgress)
  await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('region', { name: 'Distributed consensus' })).toBeDefined()
})

test('logging a session from a topic row refreshes the summary', async () => {
  renderRoutes(routes, '/progress')
  const user = userEvent.setup()
  const paxos = (await screen.findByText('Paxos')).closest('li') as HTMLElement
  await user.click(within(paxos).getByRole('button', { name: 'Log session' }))

  const dialog = await screen.findByRole('dialog', { name: 'Log session: Paxos' })
  await user.type(within(dialog).getByLabelText('Minutes studied'), '45')
  await user.click(within(dialog).getByRole('button', { name: 'Understood' }))
  await user.click(within(dialog).getByRole('button', { name: 'Log session' }))

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(progressApi.logSession).toHaveBeenCalledWith('paxos', {
    minutes: 45,
    rating: 'understood',
    note: null,
  })
  expect(progressApi.getProgress).toHaveBeenCalledTimes(2)
})

test('an achieved target is announced', async () => {
  progressApi.logSession.mockImplementation(async (topicId, body) => ({
    ...loggedResult(topicId, body),
    promoted_targets: [{ id: 'target-1', name: 'Distributed consensus', level: 'proficient' }],
  }))
  renderRoutes(routes, '/progress')
  const user = userEvent.setup()
  const pbft = (await screen.findByText('PBFT')).closest('li') as HTMLElement
  await user.click(within(pbft).getByRole('button', { name: 'Log session' }))
  const dialog = await screen.findByRole('dialog')
  await user.type(within(dialog).getByLabelText('Minutes studied'), '30')
  await user.click(within(dialog).getByRole('button', { name: 'Could teach it' }))
  await user.click(within(dialog).getByRole('button', { name: 'Log session' }))

  expect(
    await screen.findByText('Target achieved: Distributed consensus (proficient)'),
  ).toBeDefined()
})
