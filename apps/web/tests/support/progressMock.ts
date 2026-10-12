import { mock } from 'bun:test'
import type {
  ProgressRead,
  SessionLogged,
  StudySessionCreate,
  TargetProgress,
} from '../../src/api/progress'

export const emptyProgress: ProgressRead = {
  plan_version: null,
  profile_version: null,
  minutes_studied: 0,
  week: {
    week_start: '2026-10-12T00:00:00Z',
    week_end: '2026-10-19T00:00:00Z',
    minutes_studied: 0,
    budget_minutes: null,
    used_pct: null,
  },
  targets: [],
}

export const consensusTarget: TargetProgress = {
  id: 'target-1',
  name: 'Distributed consensus',
  target_level: 'proficient',
  priority: 'high',
  achieved_at: null,
  counts: { mastered: 1, in_progress: 1, not_started: 1 },
  minutes_studied: 180,
  minutes_remaining: 240,
  topics: [
    {
      topic_id: 'raft',
      title: 'Raft',
      status: 'mastered',
      minutes_studied: 120,
      estimate_minutes: 120,
    },
    {
      topic_id: 'paxos',
      title: 'Paxos',
      status: 'in-progress',
      minutes_studied: 60,
      estimate_minutes: 120,
    },
    {
      topic_id: 'pbft',
      title: 'PBFT',
      status: 'not-started',
      minutes_studied: 0,
      estimate_minutes: 120,
    },
  ],
}

export const sampleProgress: ProgressRead = {
  plan_version: 2,
  profile_version: 4,
  minutes_studied: 180,
  week: { ...emptyProgress.week, minutes_studied: 180, budget_minutes: 300, used_pct: 60 },
  targets: [consensusTarget],
}

export function loggedResult(topicId: string, body: StudySessionCreate): SessionLogged {
  return {
    session: {
      id: 'session-1',
      topic_id: topicId,
      minutes: body.minutes,
      rating: body.rating,
      note: body.note ?? null,
      studied_at: '2026-10-14T09:30:00Z',
    },
    topic: {
      topic_id: topicId,
      status: 'in-progress',
      minutes_studied: body.minutes,
      session_count: 1,
    },
    promoted_targets: [],
  }
}

/**
 * Module-boundary mock of src/api/progress (Bun shares the module registry across test files):
 * every file that renders progress UI imports this helper and resets it.
 */
export const progressApi = {
  data: emptyProgress,
  getProgress: mock(async (_utcOffsetMinutes: number): Promise<ProgressRead> => progressApi.data),
  logSession: mock(async (topicId: string, body: StudySessionCreate): Promise<SessionLogged> =>
    loggedResult(topicId, body),
  ),
  reset(data: ProgressRead = emptyProgress) {
    progressApi.data = data
    progressApi.getProgress.mockReset()
    progressApi.getProgress.mockImplementation(async () => progressApi.data)
    progressApi.logSession.mockReset()
    progressApi.logSession.mockImplementation(async (topicId, body) => loggedResult(topicId, body))
  },
}

const real = await import('../../src/api/progress')

mock.module('../../src/api/progress', () => ({
  ...real,
  getProgress: (utcOffsetMinutes: number) => progressApi.getProgress(utcOffsetMinutes),
  logSession: (topicId: string, body: StudySessionCreate) => progressApi.logSession(topicId, body),
}))
