import type { QueryKey } from '@tanstack/react-query'
import { jobKeys, type JobRead } from '../api/jobs'
import { sourceKeys } from '../api/sources'
import { topicKeys } from '../api/topics'
import type { EventInvalidations } from './useEventSource'

/**
 * Job type → extra query keys to refresh when a job of that type changes (for example
 * `ingest: (job) => [sourceKeys.all]`). Sections add one entry per job type they own.
 */
export const jobTypeInvalidations: Record<string, (job: JobRead) => QueryKey[]> = {}

// Visual generation (section 7): a video or figure job refreshes its topic page.
jobTypeInvalidations.video = jobTypeInvalidations.figure = (job) => [
  topicKeys.detail(String(job.payload.topic_id)),
]

function isJob(payload: unknown): payload is JobRead {
  return !!payload && typeof payload === 'object' && typeof (payload as JobRead).type === 'string'
}

/**
 * Event types whose query keys do not follow the prefix rule in `keysForEvent`.
 * Sections add one entry per such event type.
 */
export const eventInvalidations: EventInvalidations = {
  'jobs.updated': (payload) => {
    const extra = isJob(payload) ? (jobTypeInvalidations[payload.type]?.(payload) ?? []) : []
    return [jobKeys.all, ...extra]
  },
  // Always sent right after `jobs.updated` for the same job, which already refreshed it.
  'jobs.event': () => [],
  // Written by the ingest job on every source status change.
  'sources.updated': () => [sourceKeys.all],
}
