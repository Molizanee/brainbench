import { File as NativeFile } from 'node:buffer'
import { afterAll, beforeAll, beforeEach, expect, test } from 'bun:test'
import { sourcesApi } from '../support/sourcesMock'
import { configureApiClient } from '../../src/api/client'
import { createSource, deleteSource, listSources, uploadPdf } from '../../src/api/sources'
import { startServer } from '../../server'

type Received = { method: string; path: string; contentType: string; body: unknown }

let upstream: ReturnType<typeof Bun.serve>
let web: ReturnType<typeof startServer>
let received: Received[] = []

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    async fetch(req) {
      const { pathname } = new URL(req.url)
      const contentType = req.headers.get('content-type') ?? ''
      let body: unknown = null
      if (contentType.startsWith('multipart/form-data')) {
        const form = await req.formData()
        const file = form.get('file') as File
        body = { type: form.get('type'), name: file.name, text: await file.text() }
      } else if (contentType.includes('json')) {
        body = await req.json()
      }
      received.push({ method: req.method, path: pathname, contentType, body })
      if (req.method === 'DELETE') return new Response(null, { status: 204 })
      return Response.json(req.method === 'GET' ? [] : { id: 's1' }, {
        status: req.method === 'POST' ? 201 : 200,
      })
    },
  })
  // Requests go through the real Bun server so the multipart body crosses the /api proxy.
  web = startServer({ port: 0, apiUrl: upstream.url.origin })
  configureApiClient({ baseUrl: web.url.origin })
})

afterAll(() => {
  configureApiClient({ baseUrl: '' })
  web.stop(true)
  upstream.stop(true)
})

beforeEach(() => {
  received = []
  sourcesApi.useReal()
})

test('PDF upload is multipart FormData and reaches the API intact through the proxy', async () => {
  const content = '%PDF-1.7\n' + 'x'.repeat(256 * 1024)
  // happy-dom replaces the global File; Bun's FormData (kept by tests/setup.ts) needs its own.
  const file = new NativeFile([content], 'raft paper.pdf', {
    type: 'application/pdf',
  }) as unknown as File
  expect((await uploadPdf(file)).id).toBe('s1')
  expect(received).toHaveLength(1)
  const [request] = received
  expect(request!.method).toBe('POST')
  expect(request!.path).toBe('/api/sources')
  expect(request!.contentType).toStartWith('multipart/form-data; boundary=')
  expect(request!.body).toEqual({ type: 'pdf', name: 'raft paper.pdf', text: content })
})

test('JSON sources, listing and delete use the documented paths', async () => {
  await createSource({ type: 'url', url: 'https://raft.github.io/' })
  await listSources()
  await deleteSource('s1')
  expect(received.map((r) => [r.method, r.path, r.body])).toEqual([
    ['POST', '/api/sources', { type: 'url', url: 'https://raft.github.io/' }],
    ['GET', '/api/sources', null],
    ['DELETE', '/api/sources/s1', null],
  ])
})
