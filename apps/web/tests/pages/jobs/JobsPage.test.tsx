import { afterEach, beforeEach, expect, test } from 'bun:test'
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { jobsApi, makeJob } from '../../support/jobsMock'
import { ApiError } from '../../../src/api/client'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this)
  }
  close() {}
  emit(type: string, payload: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ type, payload }) }))
  }
}

const originalEventSource = globalThis.EventSource

beforeEach(() => {
  authApi.reset({ password_set: true, authenticated: true })
  jobsApi.reset([])
  FakeEventSource.instances = []
  Object.assign(globalThis, { EventSource: FakeEventSource })
})

afterEach(() => {
  Object.assign(globalThis, { EventSource: originalEventSource })
})

const row = (id: string) => screen.findByTestId(`job-${id}`)

test('lists jobs with status, progress, attempt, and a pending run-after', async () => {
  const later = new Date(Date.now() + 10 * 60_000).toISOString()
  jobsApi.reset([
    makeJob({
      id: 'deferred',
      type: 'video',
      attempt: 1,
      run_after: later,
      progress: 'waiting for provider limit reset',
    }),
    makeJob({
      id: 'broken',
      type: 'ingest',
      status: 'failed',
      attempt: 3,
      progress: 'failed after 3 attempts',
      error: 'TimeoutError: fetch timed out',
      error_reason: 'retries-exhausted',
    }),
  ])
  renderRoutes(routes, '/jobs')

  const deferred = within(await row('deferred'))
  expect(deferred.getByText('video')).toBeDefined()
  expect(deferred.getByText('queued')).toBeDefined()
  expect(deferred.getByText('waiting for provider limit reset')).toBeDefined()
  expect(deferred.getByText('1 / 3')).toBeDefined()
  expect(deferred.queryAllByText('-').length).toBeLessThan(2)

  const broken = within(await row('broken'))
  expect(broken.getByText('failed')).toBeDefined()
  expect(broken.getByText(/retries-exhausted: TimeoutError: fetch timed out/)).toBeDefined()
  expect(broken.queryByRole('button', { name: /Cancel/ })).toBeNull()
})

test('cancel button cancels an active job and refreshes the list', async () => {
  jobsApi.reset([makeJob({ id: 'r1', type: 'figure', status: 'running', attempt: 1 })])
  renderRoutes(routes, '/jobs')
  const user = userEvent.setup()
  await user.click(within(await row('r1')).getByRole('button', { name: 'Cancel figure job' }))
  expect(jobsApi.cancelJob).toHaveBeenCalledWith('r1')
  await waitFor(() => expect(jobsApi.listJobs.mock.calls.length).toBeGreaterThan(1))
})

test('a jobs.updated event refreshes the table without a reload', async () => {
  jobsApi.reset([makeJob({ id: 'j1', status: 'running', attempt: 1, progress: 'rendering' })])
  renderRoutes(routes, '/jobs')
  expect(within(await row('j1')).getByText('running')).toBeDefined()

  const ready = makeJob({ id: 'j1', status: 'ready', attempt: 1, progress: 'ready' })
  jobsApi.jobs = [ready]
  const source = FakeEventSource.instances[0]!
  await act(async () => source.emit('jobs.updated', ready))
  await waitFor(() =>
    expect(within(screen.getByTestId('job-j1')).getAllByText('ready')).toHaveLength(2),
  )
})

test('a failed cancel is reported', async () => {
  jobsApi.reset([makeJob({ id: 'q1' })])
  jobsApi.cancelJob.mockImplementationOnce(async () => {
    throw new ApiError(409, 'job-finished', 'Job q1 already finished with status ready.')
  })
  renderRoutes(routes, '/jobs')
  const user = userEvent.setup()
  await user.click(within(await row('q1')).getByRole('button', { name: 'Cancel noop job' }))
  expect(await screen.findByText('Job q1 already finished with status ready.')).toBeDefined()
})

test('empty state when there are no jobs', async () => {
  renderRoutes(routes, '/jobs')
  expect(await screen.findByText('No jobs yet')).toBeDefined()
})
