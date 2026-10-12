import { apiPost } from './client'
import type { GenerateResponse, GenerationJob, BudgetRead, VisualFormat } from './topics'

/** Mirrors `PlanGenerateResponse` in apps/api/app/models/assets.py. */
export type PlanGenerateResponse = {
  plan_version: number
  formats: VisualFormat[]
  dry_run: boolean
  job_count: number
  jobs: GenerationJob[]
  budget: BudgetRead
}

export const regenerateAsset = (id: string, feedback: string) =>
  apiPost<GenerateResponse>(`/api/assets/${encodeURIComponent(id)}/regenerate`, { feedback })

/** `dryRun` counts the jobs without creating them; the web confirms that count first. */
export const generateForPlan = (dryRun: boolean) =>
  apiPost<PlanGenerateResponse>(`/api/plans/current/generate${dryRun ? '?dry_run=true' : ''}`)
