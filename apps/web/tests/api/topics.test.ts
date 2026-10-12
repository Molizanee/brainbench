import { afterAll, beforeAll, beforeEach, expect, test } from 'bun:test'
import { topicsApi } from '../support/topicsMock'
import { configureApiClient } from '../../src/api/client'
import { generateForPlan, regenerateAsset } from '../../src/api/assets'
import { generateForTopic, getTopic, topicKeys } from '../../src/api/topics'
import { sessionLoggedKeys } from '../../src/api/progress'
import { eventInvalidations } from '../../src/events/invalidations'
import { keysForEvent } from '../../src/events/useEventSource'
import { makeJob } from '../support/jobsMock'

type Received = { method: string; path: string; body: unknown }
let upstream: ReturnType<typeof Bun.serve>
let received: Received[] = []

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    async fetch(req) {
      const url = new URL(req.url)
      const body = req.headers.get('content-type')?.includes('json') ? await req.json() : null
      received.push({ method: req.method, path: url.pathname + url.search, body })
      return Response.json({ id: 'raft', jobs: [] })
    },
  })
  configureApiClient({ baseUrl: upstream.url.origin })
})

afterAll(() => {
  configureApiClient({ baseUrl: '' })
  upstream.stop(true)
})

beforeEach(() => {
  received = []
  topicsApi.useReal()
})

test('topic and asset functions use the documented paths', async () => {
  await getTopic('t 1')
  await generateForTopic('t1', ['video', 'figure'])
  await regenerateAsset('a1', 'too fast')
  await generateForPlan(true)
  await generateForPlan(false)
  expect(received).toEqual([
    { method: 'GET', path: '/api/topics/t%201', body: null },
    { method: 'POST', path: '/api/topics/t1/generate', body: { formats: ['video', 'figure'] } },
    { method: 'POST', path: '/api/assets/a1/regenerate', body: { feedback: 'too fast' } },
    { method: 'POST', path: '/api/plans/current/generate?dry_run=true', body: null },
    { method: 'POST', path: '/api/plans/current/generate', body: null },
  ])
})

test('the topic key is the literal the log-session dialog invalidates', () => {
  expect(topicKeys.detail('t1')).toEqual(['topics', 't1'])
  expect(sessionLoggedKeys('t1')).toContainEqual(topicKeys.detail('t1'))
})

test('video and figure job updates refresh their topic page', () => {
  for (const type of ['video', 'figure']) {
    const job = makeJob({ type, payload: { topic_id: 't1', format: type } })
    expect(keysForEvent({ type: 'jobs.updated', payload: job }, eventInvalidations)).toEqual([
      ['jobs'],
      ['topics', 't1'],
    ])
  }
})
