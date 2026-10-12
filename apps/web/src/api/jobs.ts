import { apiGet, apiPost } from './client'

/** Mirrors `JobStatus` in apps/api/app/models/jobs.py. */
export type JobStatus = 'queued' | 'running' | 'ready' | 'failed' | 'cancelled' | 'skipped'

export const JOB_STATUSES: readonly JobStatus[] = [
  'queued',
  'running',
  'ready',
  'failed',
  'cancelled',
  'skipped',
]

/** Mirrors `JobRead`; also the payload of the `jobs.updated` event. */
export type JobRead = {
  id: string
  type: string
  status: JobStatus
  priority: number
  payload: Record<string, unknown>
  dedupe_key: string | null
  attempt: number
  max_attempts: number
  run_after: string
  progress: string | null
  error: string | null
  error_reason: string | null
  cancel_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

/** Mirrors `JobEventRead`; also the payload of the `jobs.event` event. */
export type JobEventRead = {
  id: number
  job_id: string
  kind: 'status' | 'progress'
  status: JobStatus | null
  message: string | null
  data: Record<string, unknown>
  created_at: string
}

/** Mirrors `JobDetail`. */
export type JobDetail = JobRead & { events: JobEventRead[] }

export type JobFilters = { status?: JobStatus[]; type?: string; limit?: number }

export const jobKeys = {
  all: ['jobs'] as const,
  lists: () => [...jobKeys.all, 'list'] as const,
  list: (filters: JobFilters = {}) => [...jobKeys.lists(), filters] as const,
  detail: (id: string) => [...jobKeys.all, 'detail', id] as const,
}

export const isActive = (job: Pick<JobRead, 'status'>) =>
  job.status === 'queued' || job.status === 'running'

function query(filters: JobFilters): string {
  const params = new URLSearchParams()
  for (const status of filters.status ?? []) params.append('status', status)
  if (filters.type) params.set('type', filters.type)
  if (filters.limit) params.set('limit', String(filters.limit))
  const text = params.toString()
  return text ? `?${text}` : ''
}

export const listJobs = (filters: JobFilters = {}) =>
  apiGet<JobRead[]>(`/api/jobs${query(filters)}`)

export const getJob = (id: string) => apiGet<JobDetail>(`/api/jobs/${encodeURIComponent(id)}`)

export const cancelJob = (id: string) =>
  apiPost<JobRead>(`/api/jobs/${encodeURIComponent(id)}/cancel`)
