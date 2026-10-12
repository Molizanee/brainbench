import type { QueryKey } from '@tanstack/react-query'
import { apiGet, apiPost } from './client'

/** Mirrors `MASTERY_RATINGS` in apps/api/app/db/schema.py. */
export const MASTERY_RATINGS = ['struggled', 'understood', 'confident', 'could-teach'] as const
export type MasteryRating = (typeof MASTERY_RATINGS)[number]

export type TopicStatus = 'not-started' | 'in-progress' | 'mastered'

/** Mirrors `MAX_SESSION_MINUTES` in apps/api/app/models/progress.py. */
export const MAX_SESSION_MINUTES = 24 * 60

/** Request and response types mirror apps/api/app/models/progress.py. */
export type StudySessionCreate = { minutes: number; rating: MasteryRating; note?: string | null }

export type StudySessionRead = {
  id: string
  topic_id: string
  minutes: number
  rating: MasteryRating
  note: string | null
  studied_at: string
}

export type TopicProgress = {
  topic_id: string
  status: TopicStatus
  minutes_studied: number
  session_count: number
}

export type PromotedTarget = { id: string; name: string; level: string }

export type SessionLogged = {
  session: StudySessionRead
  topic: TopicProgress
  promoted_targets: PromotedTarget[]
}

export type StatusCounts = { mastered: number; in_progress: number; not_started: number }

export type TargetTopic = {
  topic_id: string
  title: string
  status: TopicStatus
  minutes_studied: number
  estimate_minutes: number | null
}

export type TargetProgress = {
  id: string
  name: string
  target_level: string
  priority: string
  achieved_at: string | null
  counts: StatusCounts
  minutes_studied: number
  minutes_remaining: number
  topics: TargetTopic[]
}

export type WeeklyPace = {
  week_start: string
  week_end: string
  minutes_studied: number
  budget_minutes: number | null
  used_pct: number | null
}

export type ProgressRead = {
  plan_version: number | null
  profile_version: number | null
  minutes_studied: number
  week: WeeklyPace
  targets: TargetProgress[]
}

export const progressKeys = {
  all: ['progress'] as const,
  summary: (utcOffsetMinutes: number) =>
    [...progressKeys.all, 'summary', utcOffsetMinutes] as const,
}

/** Queries a logged session changes: the progress summary and the topic it was logged on. */
export const sessionLoggedKeys = (topicId: string): QueryKey[] => [
  progressKeys.all,
  ['topics', topicId],
]

/** The browser's offset from UTC in minutes (UTC-3 is -180), for the local study week. */
export const localUtcOffsetMinutes = () => -new Date().getTimezoneOffset()

export const getProgress = (utcOffsetMinutes: number) =>
  apiGet<ProgressRead>(`/api/progress?utc_offset_minutes=${utcOffsetMinutes}`)

export const logSession = (topicId: string, body: StudySessionCreate) =>
  apiPost<SessionLogged>(`/api/topics/${encodeURIComponent(topicId)}/sessions`, body)
