import { mock } from 'bun:test'
import type { JobFilters, JobRead } from '../../src/api/jobs'

export function makeJob(overrides: Partial<JobRead> = {}): JobRead {
  return {
    id: 'j1',
    type: 'noop',
    status: 'queued',
    priority: 0,
    payload: {},
    dedupe_key: null,
    attempt: 0,
    max_attempts: 3,
    run_after: '2026-10-11T12:00:00Z',
    progress: 'queued',
    error: null,
    error_reason: null,
    cancel_requested: false,
    created_at: '2026-10-11T12:00:00Z',
    started_at: null,
    finished_at: null,
    updated_at: '2026-10-11T12:00:00Z',
    ...overrides,
  }
}

/**
 * Module-boundary mock of src/api/jobs. Bun shares the module registry across test files, so
 * every file that renders the jobs page imports this helper and sets the jobs it needs.
 */
export const jobsApi = {
  jobs: [] as JobRead[],
  listJobs: mock(async (_filters?: JobFilters): Promise<JobRead[]> => jobsApi.jobs),
  cancelJob: mock(async (id: string): Promise<JobRead> => {
    const job = jobsApi.jobs.find((j) => j.id === id)
    if (!job) throw new Error('not found')
    return { ...job, cancel_requested: true }
  }),
  reset(jobs: JobRead[]) {
    jobsApi.jobs = jobs
    jobsApi.listJobs.mockClear()
    jobsApi.cancelJob.mockClear()
  },
}

const real = await import('../../src/api/jobs')

mock.module('../../src/api/jobs', () => ({
  ...real,
  listJobs: (filters?: JobFilters) => jobsApi.listJobs(filters),
  cancelJob: (id: string) => jobsApi.cancelJob(id),
}))
