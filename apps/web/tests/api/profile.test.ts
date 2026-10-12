import { afterAll, beforeAll, beforeEach, expect, test } from 'bun:test'
import { profileApi } from '../support/profileMock'
import { ApiError, configureApiClient } from '../../src/api/client'
import { addTarget, deleteSkill, getProfile, profileKeys, updateSkill } from '../../src/api/profile'

let upstream: ReturnType<typeof Bun.serve>
let missing = true
const seen: string[] = []

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    async fetch(req) {
      const { pathname } = new URL(req.url)
      const body = req.method === 'GET' || req.method === 'DELETE' ? null : await req.json()
      seen.push(`${req.method} ${pathname}${body ? ` ${JSON.stringify(body)}` : ''}`)
      if (pathname === '/api/profile' && req.method === 'GET') {
        return missing
          ? Response.json(
              { detail: { code: 'profile-missing', message: 'No learner profile' } },
              { status: 404 },
            )
          : Response.json({ id: 'p1', version: 1 })
      }
      if (pathname === '/api/profile/targets') {
        return Response.json(
          {
            detail: {
              code: 'target-not-above-current',
              message: 'The target level must be higher',
              fields: { target_level: 'The target level must be higher' },
            },
          },
          { status: 422 },
        )
      }
      if (pathname === '/api/broken') return new Response('boom', { status: 500 })
      return Response.json({ id: 'p1', version: 2 })
    },
  })
  configureApiClient({ baseUrl: upstream.url.origin })
})

afterAll(() => {
  configureApiClient({ baseUrl: '' })
  upstream.stop(true)
})

beforeEach(() => {
  profileApi.reset()
  missing = true
  seen.length = 0
})

test('getProfile answers null before a profile exists', async () => {
  expect(await getProfile()).toBeNull()
  missing = false
  expect(await getProfile()).toMatchObject({ id: 'p1', version: 1 })
})

test('skill helpers call the item routes', async () => {
  await updateSkill('s1', { level: 'expert' })
  await deleteSkill('s1')
  expect(seen).toEqual([
    'PATCH /api/profile/skills/s1 {"level":"expert"}',
    'DELETE /api/profile/skills/s1',
  ])
})

test('rule violations surface as ApiError fields', async () => {
  const error = await addTarget({
    name: 'Raft',
    target_level: 'working',
    priority: 'high',
  }).catch((e: unknown) => e)
  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).code).toBe('target-not-above-current')
  expect((error as ApiError).fields).toEqual({ target_level: 'The target level must be higher' })
})

test('query keys share the profile prefix used by events', () => {
  expect(profileKeys.detail()).toEqual(['profile', 'detail'])
})
