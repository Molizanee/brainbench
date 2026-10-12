import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { makeRun, plansApi, samplePlan } from '../../support/plansMock'
import { ApiError } from '../../../src/api/client'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

beforeEach(() => {
  authApi.reset({ password_set: true, authenticated: true })
  plansApi.useFakes()
})

const card = (title: string) => screen.findByRole('article', { name: title })

test('plan section is in the navigation', async () => {
  renderRoutes(routes, '/plan')
  expect(await screen.findByRole('heading', { name: 'Study plan' })).toBeDefined()
  const nav = screen.getByRole('navigation', { name: 'Main navigation' })
  expect(within(nav).getByText('Plan')).toBeDefined()
})

test('weekly blocks render, including a multi-week block over the budget', async () => {
  renderRoutes(routes, '/plan')
  const week1 = await screen.findByRole('region', { name: 'Week 1' })
  expect(within(week1).getByText('2 h of 5 h')).toBeDefined()
  expect(within(week1).getByRole('article', { name: 'Terms' })).toBeDefined()
  const block = screen.getByRole('region', { name: 'Weeks 2–3' })
  expect(within(block).getByText('Exceeds weekly budget')).toBeDefined()
  expect(within(block).getByText('7 h 30 min of 10 h')).toBeDefined()
  expect(within(block).getByRole('article', { name: 'Logs' })).toBeDefined()
})

test('topic cards show rationale, prerequisites, citations, and link to the topic page', async () => {
  renderRoutes(routes, '/plan')
  const terms = within(await card('Terms'))
  expect(terms.getByText('Why: Terms serves Distributed consensus.')).toBeDefined()
  expect(terms.getByText('Distributed consensus')).toBeDefined()
  expect(terms.getByRole('link', { name: 'Terms' }).getAttribute('href')).toBe(
    '/topics/topic-terms',
  )
  await userEvent.click(terms.getByRole('button', { name: 'Sources for Terms' }))
  expect(terms.getByText('Raft paper, page 3')).toBeDefined()
  expect(terms.getByText('Raft divides time into terms.')).toBeDefined()
  const link = terms.getByRole('link', { name: 'Raft (algorithm)' })
  expect(link.getAttribute('href')).toBe('https://en.wikipedia.org/wiki/Raft#:~:text=Leader')
  expect(within(await card('Logs')).getByText('After: Terms')).toBeDefined()
})

test('a reorder before a prerequisite is rejected inline and rolled back', async () => {
  plansApi.editPlan.mockImplementation(async () => {
    throw new ApiError(
      400,
      'prerequisite-violation',
      "'Logs' must come after its prerequisite 'Terms'.",
    )
  })
  renderRoutes(routes, '/plan')
  await userEvent.click(await screen.findByRole('button', { name: 'Move Logs up' }))

  const logs = within(await card('Logs'))
  expect((await logs.findByRole('alert')).textContent).toContain(
    "'Logs' must come after its prerequisite 'Terms'.",
  )
  expect(plansApi.editPlan).toHaveBeenCalledWith(2, {
    op: 'reorder',
    order: ['topic-logs', 'topic-terms'],
  })
  const titles = screen.getAllByRole('article').map((a) => a.getAttribute('aria-labelledby'))
  expect(titles).toEqual(['topic-topic-terms', 'topic-topic-logs'])
})

test('a valid reorder writes a new version', async () => {
  renderRoutes(routes, '/plan')
  await userEvent.click(await screen.findByRole('button', { name: 'Move Terms down' }))
  await waitFor(() => expect(plansApi.plan?.version).toBe(3))
  expect(plansApi.editPlan).toHaveBeenCalledWith(2, {
    op: 'reorder',
    order: ['topic-logs', 'topic-terms'],
  })
})

test('remove, include, edit, and merge each send their edit', async () => {
  renderRoutes(routes, '/plan')
  await userEvent.click(await screen.findByRole('button', { name: 'Include CSS' }))
  await waitFor(() =>
    expect(plansApi.editPlan).toHaveBeenLastCalledWith(2, { op: 'include', topic_id: 'topic-css' }),
  )
  expect(within(await card('CSS')).getByText('Manually included')).toBeDefined()

  await userEvent.click(screen.getByRole('button', { name: 'Edit Terms' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit topic' })
  const title = within(dialog).getByLabelText('Title')
  await userEvent.clear(title)
  await userEvent.type(title, 'Raft terms')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  await waitFor(() =>
    expect(plansApi.editPlan).toHaveBeenLastCalledWith(3, {
      op: 'edit',
      topic_id: 'topic-terms',
      title: 'Raft terms',
    }),
  )
  expect(await card('Raft terms')).toBeDefined()

  await userEvent.click(screen.getByRole('button', { name: 'Merge CSS' }))
  const merge = await screen.findByRole('dialog', { name: /Merge/ })
  await userEvent.click(within(merge).getByRole('combobox', { name: 'Merge into' }))
  await userEvent.click(await screen.findByRole('option', { name: 'Raft terms' }))
  await userEvent.click(within(merge).getByRole('button', { name: 'Merge' }))
  await waitFor(() =>
    expect(plansApi.editPlan).toHaveBeenLastCalledWith(4, {
      op: 'merge',
      topic_id: 'topic-css',
      into_topic_id: 'topic-terms',
    }),
  )

  await userEvent.click(await screen.findByRole('button', { name: 'Remove Logs' }))
  await waitFor(() =>
    expect(plansApi.editPlan).toHaveBeenLastCalledWith(5, { op: 'remove', topic_id: 'topic-logs' }),
  )
})

test('an edit with nothing changed stays in the dialog', async () => {
  renderRoutes(routes, '/plan')
  await userEvent.click(await screen.findByRole('button', { name: 'Edit Terms' }))
  const dialog = await screen.findByRole('dialog', { name: 'Edit topic' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  expect(await within(dialog).findByText('Change the title or the summary')).toBeDefined()
  expect(plansApi.editPlan).not.toHaveBeenCalled()
})

test('a stale plan offers a re-run', async () => {
  plansApi.useFakes({
    ...samplePlan,
    stale: true,
    stale_reasons: ['Your profile changed (version 4; this plan used version 3).'],
  })
  renderRoutes(routes, '/plan')
  const alert = await screen.findByLabelText('Stale plan')
  expect(within(alert).getByText(/Your profile changed/)).toBeDefined()
  await userEvent.click(screen.getByRole('button', { name: 'Re-run curation' }))
  await waitFor(() => expect(plansApi.startCurationRun).toHaveBeenCalled())
  expect(await screen.findByRole('status')).toBeDefined()
})

test('without a plan, curation can be started and its errors are shown', async () => {
  plansApi.useFakes(null)
  plansApi.startCurationRun.mockImplementation(async () => {
    throw new ApiError(400, 'targets-required', 'Curation needs at least one target skill.')
  })
  renderRoutes(routes, '/plan')
  expect(await screen.findByText('No study plan yet')).toBeDefined()
  await userEvent.click(screen.getByRole('button', { name: 'Run curation' }))
  expect(await screen.findByText('Curation needs at least one target skill.')).toBeDefined()
})

test('a running curation shows its progress and disables a second run', async () => {
  plansApi.useFakes(null, [makeRun({ status: 'running', progress: 'scoring topics 1-20 of 45' })])
  renderRoutes(routes, '/plan')
  expect(await screen.findByText(/scoring topics 1-20 of 45/)).toBeDefined()
  expect(screen.getByRole('button', { name: 'Run curation' }).hasAttribute('disabled')).toBe(true)
})

test('an older version is read-only', async () => {
  renderRoutes(routes, '/plan?version=1')
  expect(await screen.findByText(/You are viewing version 1/)).toBeDefined()
  expect(await card('Terms')).toBeDefined()
  expect(screen.queryByRole('button', { name: 'Edit Terms' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Include CSS' })).toBeNull()
  expect(plansApi.getPlan).toHaveBeenCalledWith(1)
})
