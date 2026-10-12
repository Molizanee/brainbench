import { afterAll, beforeAll, expect, test } from 'bun:test'
import { configureApiClient } from '../../src/api/client'
import { cancelJob, getJob, isActive, jobKeys, listJobs } from '../../src/api/jobs'

let upstream: ReturnType<typeof Bun.serve>
const seen: string[] = []

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    fetch(req) {
      const url = new URL(req.url)
      seen.push(`${req.method} ${url.pathname}${url.search}`)
      return Response.json(url.pathname.endsWith('/cancel') ? { id: 'j1' } : [])
    },
  })
  configureApiClient({ baseUrl: upstream.url.origin })
})

afterAll(() => {
  configureApiClient({ baseUrl: '' })
  upstream.stop(true)
})

test('job functions call the jobs endpoints', async () => {
  seen.length = 0
  await listJobs()
  await listJobs({ status: ['queued', 'running'], type: 'video', limit: 20 })
  await getJob('j 1')
  await cancelJob('j1')
  expect(seen).toEqual([
    'GET /api/jobs',
    'GET /api/jobs?status=queued&status=running&type=video&limit=20',
    'GET /api/jobs/j%201',
    'POST /api/jobs/j1/cancel',
  ])
})

test('query keys nest under the jobs prefix', () => {
  expect(jobKeys.list({ limit: 5 })).toEqual(['jobs', 'list', { limit: 5 }])
  expect(jobKeys.detail('j1')).toEqual(['jobs', 'detail', 'j1'])
  expect(isActive({ status: 'running' })).toBe(true)
  expect(isActive({ status: 'skipped' })).toBe(false)
})
