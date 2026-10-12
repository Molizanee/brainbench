import { apiGet, apiPost } from './client'
import type { JobStatus } from './jobs'

/** Request and response types mirror apps/api/app/models/curation.py. */
export type ModelUsed = { provider: string; model: string }

export type CurationRunCreate = { threshold?: number | null }

export type CurationRunRead = {
  id: string
  job_id: string | null
  status: JobStatus | null
  progress: string | null
  error: string | null
  profile_version: number
  source_ids: string[]
  threshold: number
  models: Record<string, ModelUsed>
  step_log: Array<Record<string, unknown>>
  plan_version: number | null
  created_at: string
  finished_at: string | null
}

export const curationKeys = {
  all: ['curation'] as const,
  runs: () => [...curationKeys.all, 'runs'] as const,
}

export const isRunActive = (run: CurationRunRead | undefined) =>
  run?.status === 'queued' || run?.status === 'running'

export const startCurationRun = (body: CurationRunCreate = {}) =>
  apiPost<CurationRunRead>('/api/curation/runs', body)

export const listCurationRuns = () => apiGet<CurationRunRead[]>('/api/curation/runs')
