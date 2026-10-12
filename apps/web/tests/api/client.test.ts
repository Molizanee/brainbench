import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test'
import { ApiError, apiDelete, apiGet, apiPost, configureApiClient } from '../../src/api/client'

let upstream: ReturnType<typeof Bun.serve>
let unauthorized = 0

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    async fetch(req) {
      const { pathname } = new URL(req.url)
      switch (pathname) {
        case '/api/things':
          return req.method === 'POST'
            ? Response.json({ received: await req.json() }, { status: 201 })
            : Response.json([{ id: 1 }])
        case '/api/empty':
          return new Response(null, { status: 204 })
        case '/api/private':
          return Response.json({ detail: 'Not authenticated' }, { status: 401 })
        case '/api/conflict':
          return Response.json(
            { detail: { code: 'profile-exists', message: 'A profile already exists' } },
            { status: 409 },
          )
        case '/api/invalid':
          return Response.json(
            {
              detail: [
                { loc: ['body', 'weekly_budget_hours'], msg: 'must be greater than 0' },
                { loc: ['body', 'skills', 0, 'name'], msg: 'required' },
              ],
            },
            { status: 422 },
          )
        default:
          return new Response('boom', { status: 500 })
      }
    },
  })
  configureApiClient({ baseUrl: upstream.url.origin, onUnauthorized: () => unauthorized++ })
})

afterAll(() => {
  configureApiClient({ baseUrl: '', onUnauthorized: null })
  upstream.stop(true)
})

beforeEach(() => {
  unauthorized = 0
})

async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    if (error instanceof ApiError) return error
    throw error
  }
  throw new Error('expected an ApiError')
}

describe('api client', () => {
  test('GET and POST JSON', async () => {
    expect(await apiGet<Array<{ id: number }>>('/api/things')).toEqual([{ id: 1 }])
    expect(await apiPost<unknown>('/api/things', { a: 1 })).toEqual({ received: { a: 1 } })
  })

  test('204 resolves undefined', async () => {
    expect(await apiDelete('/api/empty')).toBeUndefined()
  })

  test('401 calls the unauthorized handler and throws', async () => {
    const error = await rejection(apiGet('/api/private'))
    expect(error.status).toBe(401)
    expect(unauthorized).toBe(1)
  })

  test('401 without redirect leaves the handler alone', async () => {
    await rejection(apiGet('/api/private', { redirectOn401: false }))
    expect(unauthorized).toBe(0)
  })

  test('domain errors keep their code and message', async () => {
    const error = await rejection(apiGet('/api/conflict'))
    expect(error.status).toBe(409)
    expect(error.code).toBe('profile-exists')
    expect(error.message).toBe('A profile already exists')
  })

  test('422 maps to field errors', async () => {
    const error = await rejection(apiPost('/api/invalid', {}))
    expect(error.code).toBe('validation-error')
    expect(error.fields).toEqual({
      weekly_budget_hours: 'must be greater than 0',
      'skills.0.name': 'required',
    })
  })

  test('non-JSON errors still become ApiError', async () => {
    const error = await rejection(apiGet('/api/explode'))
    expect(error.status).toBe(500)
    expect(unauthorized).toBe(0)
  })
})
