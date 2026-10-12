import { afterAll, beforeAll, beforeEach, expect, test } from 'bun:test'
import { tutorApi } from '../support/tutorMock'
import { ApiError, configureApiClient } from '../../src/api/client'
import {
  configureTutorStream,
  contextParam,
  createThread,
  listMessages,
  listThreads,
  parseSseBlock,
  readSse,
  stopReply,
  streamMessage,
  type TutorStreamEvent,
} from '../../src/api/tutor'
import { startServer } from '../../server'

type Received = { method: string; path: string; body: unknown }

let upstream: ReturnType<typeof Bun.serve>
let web: ReturnType<typeof startServer>
let received: Received[] = []
let releaseRest: () => void = () => undefined

const encoder = new TextEncoder()
const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

beforeAll(() => {
  upstream = Bun.serve({
    port: 0,
    async fetch(req) {
      const url = new URL(req.url)
      const body = req.headers.get('content-type')?.includes('json') ? await req.json() : null
      received.push({ method: req.method, path: url.pathname + url.search, body })
      if (url.pathname.endsWith('/messages') && req.method === 'POST') {
        if ((body as { content: string }).content === 'no provider') {
          return Response.json(
            { detail: { code: 'no-provider', message: 'Connect one on /providers' } },
            { status: 400 },
          )
        }
        const gate = new Promise<void>((resolve) => (releaseRest = resolve))
        const stream = new ReadableStream<Uint8Array>({
          async start(controller) {
            controller.enqueue(encoder.encode(': reply\n\n' + sse('delta', { text: 'Raft ' })))
            await gate
            // An event split across two network chunks, then the end of the reply.
            const done = sse('done', { message: { id: 'm2' }, user_message_id: 'm1' })
            controller.enqueue(encoder.encode(sse('delta', { text: 'elects' }) + done.slice(0, 9)))
            controller.enqueue(encoder.encode(done.slice(9)))
            controller.close()
          },
        })
        return new Response(stream, { headers: { 'content-type': 'text/event-stream' } })
      }
      if (url.pathname.endsWith('/messages')) return Response.json([])
      if (url.pathname.endsWith('/stop')) return Response.json({ stopped: true, message: null })
      if (req.method === 'POST') return Response.json({ id: 't1' }, { status: 201 })
      return Response.json({ context: { type: 'general' }, threads: [] })
    },
  })
  web = startServer({ port: 0, apiUrl: upstream.url.origin })
  configureApiClient({ baseUrl: web.url.origin })
  configureTutorStream({ baseUrl: web.url.origin })
})

afterAll(() => {
  configureApiClient({ baseUrl: '' })
  configureTutorStream({ baseUrl: '' })
  web.stop(true)
  upstream.stop(true)
})

beforeEach(() => {
  received = []
  tutorApi.useReal()
})

test('context parameters', () => {
  expect(contextParam({ type: 'general' })).toBe('general')
  expect(contextParam({ type: 'plan' })).toBe('plan')
  expect(contextParam({ type: 'topic', topicId: 'abc' })).toBe('topic:abc')
})

test('thread and message calls use the documented paths', async () => {
  await listThreads('topic:abc')
  await createThread('plan')
  await listMessages('t1')
  await stopReply('t1')
  expect(received.map((r) => [r.method, r.path])).toEqual([
    ['GET', '/api/tutor/threads?context=topic%3Aabc'],
    ['POST', '/api/tutor/threads?context=plan'],
    ['GET', '/api/tutor/threads/t1/messages'],
    ['POST', '/api/tutor/threads/t1/stop'],
  ])
})

test('the reply streams through the Bun proxy event by event', async () => {
  const events: TutorStreamEvent[] = []
  let firstSeen: () => void = () => undefined
  const first = new Promise<void>((resolve) => (firstSeen = resolve))
  const done = streamMessage('t1', 'Why a majority?', {
    onEvent: (event) => {
      events.push(event)
      firstSeen()
    },
  })
  // The first delta arrives while the API is still holding the rest of the reply.
  await first
  expect(events).toEqual([{ type: 'delta', data: { text: 'Raft ' } }])
  releaseRest()
  await done
  expect(events.map((e) => e.type)).toEqual(['delta', 'delta', 'done'])
  expect(events[2]).toEqual({
    type: 'done',
    data: { message: { id: 'm2' }, user_message_id: 'm1' },
  } as unknown as TutorStreamEvent)
  expect(received.at(-1)).toEqual({
    method: 'POST',
    path: '/api/tutor/threads/t1/messages',
    body: { content: 'Why a majority?' },
  })
})

test('a rejected message throws the API error before any event', async () => {
  const events: TutorStreamEvent[] = []
  const error = await streamMessage('t1', 'no provider', { onEvent: (e) => events.push(e) }).catch(
    (e: unknown) => e,
  )
  expect(error).toBeInstanceOf(ApiError)
  expect((error as ApiError).code).toBe('no-provider')
  expect((error as ApiError).status).toBe(400)
  expect(events).toEqual([])
})

test('SSE parsing ignores comments, keepalives and unknown events', async () => {
  expect(parseSseBlock(': keepalive')).toBeNull()
  expect(parseSseBlock('event: other\ndata: {}')).toBeNull()
  expect(parseSseBlock('event: delta\ndata: {"text":"a"}')).toEqual({
    type: 'delta',
    data: { text: 'a' },
  })
  const body = new Response('event: delta\r\ndata: {"text":"x"}\r\n\r\n: keepalive\n\n').body!
  const events = []
  for await (const event of readSse(body)) events.push(event)
  expect(events).toEqual([{ type: 'delta', data: { text: 'x' } }])
})
