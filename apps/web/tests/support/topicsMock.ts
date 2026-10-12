import { mock } from 'bun:test'
import type { PlanGenerateResponse } from '../../src/api/assets'
import type {
  AssetRead,
  BudgetRead,
  GenerateResponse,
  TopicRead,
  VisualFormat,
} from '../../src/api/topics'
import { makeJob } from './jobsMock'

const realTopics = await import('../../src/api/topics')
const realAssets = await import('../../src/api/assets')
// mock.module below patches the modules' live bindings, so keep the original functions here.
const original = {
  getTopic: realTopics.getTopic,
  generateForTopic: realTopics.generateForTopic,
  regenerateAsset: realAssets.regenerateAsset,
  generateForPlan: realAssets.generateForPlan,
}

export const budget: BudgetRead = {
  render_minutes_used: 42.5,
  render_minutes_limit: 600,
  render_minutes_left: 557.5,
  render_resets_at: '2026-11-01T00:00:00Z',
  jobs_today: 3,
  jobs_limit: 20,
  jobs_left: 17,
  jobs_reset_at: '2026-10-13T00:00:00Z',
}

const scene = {
  id: 'intro',
  objective: 'See why a majority decides',
  narration: 'A candidate needs votes from a majority of servers.',
  visual: 'Five servers, three light up',
  chunk_ids: ['c1'],
  citations: [
    {
      chunk_id: 'c1',
      source_id: 's1',
      source_title: 'Raft paper',
      location: { page: 3 },
      excerpt: 'A candidate wins an election if it receives votes from a majority.',
    },
  ],
}

export function makeAsset(overrides: Partial<AssetRead> = {}): AssetRead {
  const format: VisualFormat = overrides.format ?? 'video'
  const id = overrides.id ?? `${format}-1`
  return {
    id,
    topic_id: 'raft',
    format,
    is_current: true,
    created_at: '2026-10-12T10:00:00Z',
    job_id: 'j1',
    previous_asset_id: null,
    feedback: null,
    duration_s: format === 'video' ? 75.5 : null,
    urls:
      format === 'video'
        ? {
            video: `/api/assets/${id}/lesson.mp4`,
            captions: `/api/assets/${id}/lesson.vtt`,
            html: null,
            preview: null,
          }
        : {
            video: null,
            captions: null,
            html: `/api/assets/${id}/figure.html`,
            preview: `/api/assets/${id}/preview.png`,
          },
    storyboard: {
      id: 'sb1',
      title: 'Raft leader election',
      scenes: [scene],
      figure_brief: format === 'figure' ? { applicable: true, object: 'ballot boxes' } : null,
      created_at: '2026-10-12T10:00:00Z',
    },
    script: format === 'video' ? 'class Lesson(Scene):\n    ...' : null,
    build_source:
      format === 'figure' ? [{ path: 'build.mjs', size: 12, content: '// build it' }] : [],
    transcript_tail: format === 'figure' ? 'assistant: built five ballot boxes' : null,
    details: {},
    ...overrides,
  }
}

export function makeTopic(overrides: Partial<TopicRead> = {}): TopicRead {
  return {
    id: 'raft',
    title: 'Raft leader election',
    summary: 'How Raft elects a leader with a majority of votes.',
    status: 'in-progress',
    stats: { minutes_studied: 90, session_count: 2 },
    revision: 2,
    history: [
      {
        revision: 2,
        title: 'Raft leader election',
        created_by: 'edit',
        created_at: '2026-10-11T10:00:00Z',
      },
      {
        revision: 1,
        title: 'Leader election',
        created_by: 'run',
        created_at: '2026-10-10T10:00:00Z',
      },
    ],
    assets: [],
    plan_item: {
      plan_version: 2,
      position: 0,
      week_start: 1,
      week_span: 1,
      estimate_minutes: 120,
      relevance: 0.9,
      rationale: null,
      excluded: false,
    },
    jobs: [],
    budget,
    ...overrides,
  }
}

export const planPreview: PlanGenerateResponse = {
  plan_version: 2,
  formats: ['video', 'figure'],
  dry_run: true,
  job_count: 3,
  jobs: [
    {
      topic_id: 'raft',
      title: 'Raft',
      format: 'video',
      job_id: null,
      status: null,
      deduplicated: false,
    },
    {
      topic_id: 'raft',
      title: 'Raft',
      format: 'figure',
      job_id: 'j9',
      status: 'running',
      deduplicated: true,
    },
    {
      topic_id: 'paxos',
      title: 'Paxos',
      format: 'video',
      job_id: null,
      status: null,
      deduplicated: false,
    },
    {
      topic_id: 'paxos',
      title: 'Paxos',
      format: 'figure',
      job_id: null,
      status: null,
      deduplicated: false,
    },
  ],
  budget,
}

function queued(format: VisualFormat, deduplicated = false): GenerateResponse {
  return {
    jobs: [
      {
        topic_id: 'raft',
        title: null,
        format,
        job_id: 'j2',
        status: deduplicated ? 'running' : 'queued',
        deduplicated,
      },
    ],
    budget,
  }
}

/**
 * Module-boundary mock of src/api/topics and src/api/assets (sourcesMock pattern): page tests
 * call `useFakes(topic)`; tests of the modules themselves call `useReal()`.
 */
export const topicsApi = {
  topic: makeTopic(),
  getTopic: mock(original.getTopic),
  generateForTopic: mock(original.generateForTopic),
  regenerateAsset: mock(original.regenerateAsset),
  generateForPlan: mock(original.generateForPlan),

  useFakes(topic: TopicRead = makeTopic()) {
    topicsApi.topic = topic
    topicsApi.reset()
    topicsApi.getTopic.mockImplementation(async () => topicsApi.topic)
    topicsApi.generateForTopic.mockImplementation(async (_id: string, formats: VisualFormat[]) => ({
      jobs: formats.map((f) => queued(f).jobs[0]!),
      budget,
    }))
    topicsApi.regenerateAsset.mockImplementation(async () => queued('video'))
    topicsApi.generateForPlan.mockImplementation(async (dryRun: boolean) => ({
      ...planPreview,
      dry_run: dryRun,
    }))
  },

  useReal() {
    topicsApi.reset()
    topicsApi.getTopic.mockImplementation(original.getTopic)
    topicsApi.generateForTopic.mockImplementation(original.generateForTopic)
    topicsApi.regenerateAsset.mockImplementation(original.regenerateAsset)
    topicsApi.generateForPlan.mockImplementation(original.generateForPlan)
  },

  reset() {
    for (const fn of [
      topicsApi.getTopic,
      topicsApi.generateForTopic,
      topicsApi.regenerateAsset,
      topicsApi.generateForPlan,
    ]) {
      fn.mockClear()
    }
  },
}

export const runningVideoJob = makeJob({
  id: 'j2',
  type: 'video',
  status: 'running',
  progress: 'rendering (attempt 1 of 3)',
  payload: { topic_id: 'raft', format: 'video' },
})

mock.module('../../src/api/topics', () => ({
  ...realTopics,
  getTopic: (id: string) => topicsApi.getTopic(id),
  generateForTopic: (id: string, formats: VisualFormat[]) =>
    topicsApi.generateForTopic(id, formats),
}))

mock.module('../../src/api/assets', () => ({
  ...realAssets,
  regenerateAsset: (id: string, feedback: string) => topicsApi.regenerateAsset(id, feedback),
  generateForPlan: (dryRun: boolean) => topicsApi.generateForPlan(dryRun),
}))
