import { afterAll, beforeAll, beforeEach, expect, test } from 'bun:test'
import { plansApi } from '../support/plansMock'
import { configureApiClient } from '../../src/api/client'
import {
  curationKeys,
  isRunActive,
  listCurationRuns,
  startCurationRun,
} from '../../src/api/curation'
import { editPlan, getCurrentPlan, getPlan, listPlans, planKeys } from '../../src/api/plans'
import { eventInvalidations, jobTypeInvalidations } from '../../src/events/invalidations'
import { keysForEvent } from '../../src/events/useEventSource'
import { citationLocation, weekLabel } from '../../src/pages/plan/format'
import { makeJob } from '../support/jobsMock'
import { makeRun } from '../support/plansMock'

let upstream: ReturnType<typeof Bun.serve>
let missing = true
const seen: string[] = []

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    async fetch(req) {
      const { pathname } = new URL(req.url)
      const body = req.method === 'GET' ? null : await req.json()
      seen.push(`${req.method} ${pathname}${body ? ` ${JSON.stringify(body)}` : ''}`)
      if (pathname === '/api/plans/current' && missing) {
        return Response.json(
          { detail: { code: 'plan-missing', message: 'No study plan yet.' } },
          { status: 404 },
        )
      }
      return Response.json(
        pathname.endsWith('/runs') || pathname === '/api/plans' ? [] : { version: 1 },
      )
    },
  })
  configureApiClient({ baseUrl: upstream.url.origin })
})

afterAll(() => {
  configureApiClient({ baseUrl: '' })
  upstream.stop(true)
})

beforeEach(() => {
  plansApi.useReal()
  seen.length = 0
})

test('plan and curation requests hit their endpoints', async () => {
  expect(await getCurrentPlan()).toBeNull()
  missing = false
  expect(await getCurrentPlan()).toEqual({ version: 1 } as never)
  await getPlan(3)
  await listPlans()
  await editPlan(3, { op: 'remove', topic_id: 't1' })
  await startCurationRun({ threshold: 0.5 })
  await listCurationRuns()
  expect(seen).toEqual([
    'GET /api/plans/current',
    'GET /api/plans/current',
    'GET /api/plans/3',
    'GET /api/plans',
    'POST /api/plans/3/edits {"op":"remove","topic_id":"t1"}',
    'POST /api/curation/runs {"threshold":0.5}',
    'GET /api/curation/runs',
  ])
})

test('query keys and the curation job invalidation', () => {
  expect(planKeys.current()).toEqual(['plans', 'current'])
  expect(planKeys.version(2)).toEqual(['plans', 'version', 2])
  expect(curationKeys.runs()).toEqual(['curation', 'runs'])
  const job = makeJob({ type: 'curation', payload: { run_id: 'r1' } })
  expect(jobTypeInvalidations.curation?.(job)).toEqual([['plans'], ['curation']])
  expect(keysForEvent({ type: 'jobs.updated', payload: job }, eventInvalidations)).toEqual([
    ['jobs'],
    ['plans'],
    ['curation'],
  ])
})

test('run activity and labels', () => {
  expect(isRunActive(makeRun({ status: 'running' }))).toBe(true)
  expect(isRunActive(makeRun({ status: 'ready' }))).toBe(false)
  expect(isRunActive(undefined)).toBe(false)
  expect(weekLabel({ week_start: 2, week_span: 2 })).toBe('Weeks 2–3')
  expect(weekLabel({ week_start: 4, week_span: 1 })).toBe('Week 4')
  const base = {
    chunk_id: 'c',
    source_id: 's',
    source_title: 't',
    source_type: 'pdf',
    url: null,
    excerpt: '',
  }
  expect(citationLocation({ ...base, location: { page: 3 } })).toBe('page 3')
  expect(citationLocation({ ...base, location: { path: 'docs/a.md' } })).toBe('docs/a.md')
  expect(citationLocation({ ...base, location: {} })).toBe('')
})
