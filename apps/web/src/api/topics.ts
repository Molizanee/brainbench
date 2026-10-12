import { apiGet, apiPost } from './client'
import type { JobRead, JobStatus } from './jobs'
import type { TopicStatus } from './progress'

/** Mirrors `VisualFormat` in apps/api/app/models/assets.py. */
export const VISUAL_FORMATS = ['video', 'figure'] as const
export type VisualFormat = (typeof VISUAL_FORMATS)[number]

/** Request and response types mirror apps/api/app/models/{topics,assets}.py. */
export type ChunkCitation = {
  chunk_id: string
  source_id: string | null
  source_title: string | null
  location: Record<string, unknown>
  excerpt: string | null
}

export type SceneRead = {
  id: string
  objective: string
  narration: string
  visual: string
  chunk_ids: string[]
  citations: ChunkCitation[]
}

export type StoryboardRead = {
  id: string
  title: string
  scenes: SceneRead[]
  figure_brief: Record<string, unknown> | null
  created_at: string
}

export type AssetUrls = {
  video: string | null
  captions: string | null
  html: string | null
  preview: string | null
}

export type BuildSourceFile = { path: string; size: number; content: string | null }

export type AssetRead = {
  id: string
  topic_id: string
  format: VisualFormat
  is_current: boolean
  created_at: string
  job_id: string | null
  previous_asset_id: string | null
  feedback: string | null
  duration_s: number | null
  urls: AssetUrls
  storyboard: StoryboardRead | null
  script: string | null
  build_source: BuildSourceFile[]
  transcript_tail: string | null
  details: Record<string, unknown>
}

export type BudgetRead = {
  render_minutes_used: number
  render_minutes_limit: number
  render_minutes_left: number
  render_resets_at: string
  jobs_today: number
  jobs_limit: number
  jobs_left: number
  jobs_reset_at: string
}

export type TopicStatsRead = { minutes_studied: number; session_count: number }

export type TopicRevisionRead = {
  revision: number
  title: string
  created_by: string
  created_at: string
}

export type PlanItemRead = {
  plan_version: number
  position: number
  week_start: number | null
  week_span: number | null
  estimate_minutes: number | null
  relevance: number | null
  rationale: string | null
  excluded: boolean
}

export type TopicRead = {
  id: string
  title: string
  summary: string
  status: TopicStatus
  stats: TopicStatsRead
  revision: number
  history: TopicRevisionRead[]
  assets: AssetRead[]
  plan_item: PlanItemRead | null
  jobs: JobRead[]
  budget: BudgetRead
}

export type GenerationJob = {
  topic_id: string
  title: string | null
  format: VisualFormat
  job_id: string | null
  status: JobStatus | null
  deduplicated: boolean
}

export type GenerateResponse = { jobs: GenerationJob[]; budget: BudgetRead }

/** `['topics', id]`: the progress dialog invalidates this literal when a session is logged. */
export const topicKeys = {
  all: ['topics'] as const,
  detail: (id: string) => ['topics', id] as const,
}

/** Follows the API's 307 when the topic was merged; the returned `id` is the survivor's. */
export const getTopic = (id: string) => apiGet<TopicRead>(`/api/topics/${encodeURIComponent(id)}`)

export const generateForTopic = (id: string, formats: VisualFormat[]) =>
  apiPost<GenerateResponse>(`/api/topics/${encodeURIComponent(id)}/generate`, { formats })
