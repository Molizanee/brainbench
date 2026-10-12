import { ApiError, apiGet, apiPost } from './client'
import type { ModelUsed } from './curation'
import type { TopicStatus } from './progress'

/** Request and response types mirror apps/api/app/models/plans.py. */
export type Citation = {
  chunk_id: string
  source_id: string
  source_title: string
  source_type: string
  location: Record<string, unknown>
  url: string | null
  excerpt: string
}

export type TargetRef = { id: string; name: string }
export type TopicRef = { topic_id: string; title: string }

export type PlanItemRead = {
  topic_id: string
  topic_revision_id: string
  revision: number
  title: string
  summary: string
  position: number
  week_start: number | null
  week_span: number | null
  exceeds_weekly_budget: boolean
  prerequisites: TopicRef[]
  manually_included: boolean
  excluded: boolean
  relevance: number | null
  rationale: string | null
  serves_targets: TargetRef[]
  estimate_minutes: number | null
  status: TopicStatus
  merged_into: string | null
  citations: Citation[]
}

export type PlanWeek = {
  week_start: number
  week_span: number
  exceeds_weekly_budget: boolean
  total_minutes: number
  topic_ids: string[]
}

export type PlanRunInfo = {
  id: string
  threshold: number
  models: Record<string, ModelUsed>
  finished_at: string | null
}

export type PlanRead = {
  version: number
  created_at: string
  created_by: 'run' | 'edit'
  curation_run_id: string | null
  profile_version: number
  source_ids: string[]
  current_version: number
  is_current: boolean
  stale: boolean
  stale_reasons: string[]
  weekly_budget_minutes: number | null
  run: PlanRunInfo | null
  weeks: PlanWeek[]
  items: PlanItemRead[]
  excluded: PlanItemRead[]
}

export type PlanSummary = { version: number; created_at: string; created_by: 'run' | 'edit' }

export type PlanEdit =
  | { op: 'reorder'; order: string[] }
  | { op: 'remove'; topic_id: string }
  | { op: 'merge'; topic_id: string; into_topic_id: string }
  | { op: 'edit'; topic_id: string; title?: string; summary?: string }
  | { op: 'include'; topic_id: string }

export const planKeys = {
  all: ['plans'] as const,
  list: () => [...planKeys.all, 'list'] as const,
  current: () => [...planKeys.all, 'current'] as const,
  version: (version: number) => [...planKeys.all, 'version', version] as const,
}

/** The current plan, or `null` before the first curation run. */
export async function getCurrentPlan(): Promise<PlanRead | null> {
  try {
    return await apiGet<PlanRead>('/api/plans/current')
  } catch (error) {
    if (error instanceof ApiError && error.code === 'plan-missing') return null
    throw error
  }
}

export const getPlan = (version: number) => apiGet<PlanRead>(`/api/plans/${version}`)

export const listPlans = () => apiGet<PlanSummary[]>('/api/plans')

/** Applies one edit to the current version (`version`); answers with the new version. */
export const editPlan = (version: number, edit: PlanEdit) =>
  apiPost<PlanRead>(`/api/plans/${version}/edits`, edit)
