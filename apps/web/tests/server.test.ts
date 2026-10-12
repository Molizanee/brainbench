import { afterAll, beforeAll, describe, expect, test } from 'bun:test'
import { announceWhenReady, startServer } from '../server'

let upstream: ReturnType<typeof Bun.serve>
let server: ReturnType<typeof startServer>

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    fetch(req) {
      const { pathname } = new URL(req.url)
      if (pathname === '/api/health') return Response.json({ status: 'ok' })
      if (pathname === '/events') {
        return new Response('data: {"type":"ping","payload":null}\n\n', {
          headers: { 'content-type': 'text/event-stream' },
        })
      }
      return Response.json({ detail: 'Not Found' }, { status: 404 })
    },
  })
  server = startServer({ port: 0, apiUrl: upstream.url.origin })
})

afterAll(() => {
  server.stop(true)
  upstream.stop(true)
})

describe('server routes', () => {
  test('/api/health is proxied, not answered with the SPA', async () => {
    const response = await fetch(new URL('/api/health', server.url))
    expect(response.headers.get('content-type')).not.toContain('text/html')
    expect(response.headers.get('content-type')).toContain('application/json')
    expect(await response.json()).toEqual({ status: 'ok' })
  })

  test('/events is proxied, not answered with the SPA', async () => {
    const response = await fetch(new URL('/events', server.url))
    expect(response.headers.get('content-type')).not.toContain('text/html')
    expect(response.headers.get('content-type')).toContain('text/event-stream')
    await response.body?.cancel()
  })

  test('unknown API paths keep the API status', async () => {
    const response = await fetch(new URL('/api/nope', server.url))
    expect(response.status).toBe(404)
    expect(response.headers.get('content-type')).not.toContain('text/html')
  })

  test('client routes fall back to the SPA', async () => {
    const response = await fetch(new URL('/providers', server.url))
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toContain('text/html')
  })

  test('API down answers 502 JSON', async () => {
    const lonely = startServer({ port: 0, apiUrl: 'http://127.0.0.1:9' })
    try {
      const response = await fetch(new URL('/api/health', lonely.url))
      expect(response.status).toBe(502)
      expect(response.headers.get('content-type')).toContain('application/json')
    } finally {
      lonely.stop(true)
    }
  })
})

test('announces the URL once the API answers', async () => {
  const lines: string[] = []
  await announceWhenReady(server.url, 'http://localhost:3000', (line) => lines.push(line), 10)
  expect(lines).toEqual(['BrainBench ready at http://localhost:3000'])
})
