import { mock } from 'bun:test'
import type { CurationRunCreate, CurationRunRead } from '../../src/api/curation'
import type { PlanEdit, PlanItemRead, PlanRead, PlanSummary } from '../../src/api/plans'

const realPlans = await import('../../src/api/plans')
const realCuration = await import('../../src/api/curation')
// mock.module below patches the modules' live bindings, so keep the originals here.
const original = {
  getCurrentPlan: realPlans.getCurrentPlan,
  getPlan: realPlans.getPlan,
  listPlans: realPlans.listPlans,
  editPlan: realPlans.editPlan,
  startCurationRun: realCuration.startCurationRun,
  listCurationRuns: realCuration.listCurationRuns,
}

export function makeItem(overrides: Partial<PlanItemRead> = {}): PlanItemRead {
  const title = overrides.title ?? 'Topic'
  return {
    topic_id: `topic-${title.toLowerCase()}`,
    topic_revision_id: `rev-${title.toLowerCase()}`,
    revision: 1,
    title,
    summary: `${title} summary.`,
    position: 0,
    week_start: 1,
    week_span: 1,
    exceeds_weekly_budget: false,
    prerequisites: [],
    manually_included: false,
    excluded: false,
    relevance: 0.8,
    rationale: `${title} serves Distributed consensus.`,
    serves_targets: [{ id: 'target-1', name: 'Distributed consensus' }],
    estimate_minutes: 120,
    status: 'not-started',
    merged_into: null,
    citations: [],
    ...overrides,
  }
}

const terms = makeItem({
  title: 'Terms',
  position: 0,
  citations: [
    {
      chunk_id: 'c1',
      source_id: 's1',
      source_title: 'Raft paper',
      source_type: 'pdf',
      location: { page: 3 },
      url: null,
      excerpt: 'Raft divides time into terms.',
    },
    {
      chunk_id: 'c2',
      source_id: 's2',
      source_title: 'Raft (algorithm)',
      source_type: 'url',
      location: { fragment: '#:~:text=Leader' },
      url: 'https://en.wikipedia.org/wiki/Raft#:~:text=Leader',
      excerpt: 'Leader election.',
    },
  ],
})
const logs = makeItem({
  title: 'Logs',
  position: 1,
  week_start: 2,
  week_span: 2,
  exceeds_weekly_budget: true,
  estimate_minutes: 450,
  prerequisites: [{ topic_id: 'topic-terms', title: 'Terms' }],
})
const css = makeItem({
  title: 'CSS',
  position: 2,
  excluded: true,
  week_start: null,
  week_span: null,
  relevance: 0.2,
  serves_targets: [],
  rationale: 'Unrelated to your target skills. CSS layout.',
})

export const samplePlan: PlanRead = {
  version: 2,
  created_at: '2026-10-12T10:00:00Z',
  created_by: 'run',
  curation_run_id: 'run-1',
  profile_version: 3,
  source_ids: ['s1', 's2'],
  current_version: 2,
  is_current: true,
  stale: false,
  stale_reasons: [],
  weekly_budget_minutes: 300,
  run: { id: 'run-1', threshold: 0.4, models: {}, finished_at: '2026-10-12T10:00:00Z' },
  weeks: [
    {
      week_start: 1,
      week_span: 1,
      exceeds_weekly_budget: false,
      total_minutes: 120,
      topic_ids: [terms.topic_id],
    },
    {
      week_start: 2,
      week_span: 2,
      exceeds_weekly_budget: true,
      total_minutes: 450,
      topic_ids: [logs.topic_id],
    },
  ],
  items: [terms, logs],
  excluded: [css],
}

export const sampleVersions: PlanSummary[] = [
  { version: 2, created_at: '2026-10-12T10:00:00Z', created_by: 'run' },
  { version: 1, created_at: '2026-10-11T10:00:00Z', created_by: 'run' },
]

export function makeRun(overrides: Partial<CurationRunRead> = {}): CurationRunRead {
  return {
    id: 'run-2',
    job_id: 'job-2',
    status: 'queued',
    progress: null,
    error: null,
    profile_version: 3,
    source_ids: ['s1', 's2'],
    threshold: 0.4,
    models: {},
    step_log: [],
    plan_version: null,
    created_at: '2026-10-12T11:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

/** Applies an edit the way the API would, enough for page tests: a new current version. */
export function applyEdit(plan: PlanRead, edit: PlanEdit): PlanRead {
  const version = plan.version + 1
  const next = { ...plan, version, current_version: version, created_by: 'edit' as const }
  switch (edit.op) {
    case 'reorder':
      return {
        ...next,
        items: edit.order.map((id) => plan.items.find((i) => i.topic_id === id)!),
      }
    case 'remove':
      return { ...next, items: plan.items.filter((i) => i.topic_id !== edit.topic_id) }
    case 'include': {
      const item = plan.excluded.find((i) => i.topic_id === edit.topic_id)!
      return {
        ...next,
        items: [
          ...plan.items,
          { ...item, excluded: false, manually_included: true, week_start: 2 },
        ],
        excluded: plan.excluded.filter((i) => i.topic_id !== edit.topic_id),
      }
    }
    case 'edit':
      return {
        ...next,
        items: plan.items.map((i) =>
          i.topic_id === edit.topic_id
            ? {
                ...i,
                title: edit.title ?? i.title,
                summary: edit.summary ?? i.summary,
                revision: 2,
              }
            : i,
        ),
      }
    case 'merge':
      return { ...next, items: plan.items.filter((i) => i.topic_id !== edit.topic_id) }
  }
}

/**
 * Module-boundary mock of src/api/plans and src/api/curation (authMock pattern). `useFakes()`
 * serves an in-memory plan for page tests; `useReal()` restores the real functions.
 */
export const plansApi = {
  plan: samplePlan as PlanRead | null,
  versions: sampleVersions,
  runs: [] as CurationRunRead[],
  getCurrentPlan: mock(original.getCurrentPlan),
  getPlan: mock(original.getPlan),
  listPlans: mock(original.listPlans),
  editPlan: mock(original.editPlan),
  startCurationRun: mock(original.startCurationRun),
  listCurationRuns: mock(original.listCurationRuns),

  useFakes(plan: PlanRead | null = samplePlan, runs: CurationRunRead[] = []) {
    plansApi.plan = plan
    plansApi.runs = [...runs]
    plansApi.useReal()
    plansApi.getCurrentPlan.mockImplementation(async () => plansApi.plan)
    plansApi.getPlan.mockImplementation(async (version: number) => ({
      ...samplePlan,
      version,
      is_current: version === samplePlan.current_version,
    }))
    plansApi.listPlans.mockImplementation(async () => plansApi.versions)
    plansApi.editPlan.mockImplementation(async (_version: number, edit: PlanEdit) => {
      plansApi.plan = applyEdit(plansApi.plan ?? samplePlan, edit)
      return plansApi.plan
    })
    plansApi.startCurationRun.mockImplementation(async (_body?: CurationRunCreate) => {
      const run = makeRun()
      plansApi.runs.unshift(run)
      return run
    })
    plansApi.listCurationRuns.mockImplementation(async () => [...plansApi.runs])
  },

  useReal() {
    for (const name of Object.keys(original) as Array<keyof typeof original>) {
      plansApi[name].mockReset()
      // oxlint-disable-next-line typescript/no-explicit-any
      plansApi[name].mockImplementation(original[name] as any)
    }
  },
}

mock.module('../../src/api/plans', () => ({
  ...realPlans,
  getCurrentPlan: () => plansApi.getCurrentPlan(),
  getPlan: (version: number) => plansApi.getPlan(version),
  listPlans: () => plansApi.listPlans(),
  editPlan: (version: number, edit: PlanEdit) => plansApi.editPlan(version, edit),
}))

mock.module('../../src/api/curation', () => ({
  ...realCuration,
  startCurationRun: (body?: CurationRunCreate) => plansApi.startCurationRun(body),
  listCurationRuns: () => plansApi.listCurationRuns(),
}))
