import { apiGet, apiPost, ApiError, toApiError } from './client'

/** Mirrors `TutorContextType` in apps/api/app/models/tutor.py. */
export type TutorContextType = 'topic' | 'plan' | 'general'
export type TutorRole = 'user' | 'assistant'
export type Interruption = 'stop' | 'disconnect' | 'limit' | 'error'

/** The page context a tutor thread is bound to, derived from the route. */
export type TutorContext =
  { type: 'general' } | { type: 'plan' } | { type: 'topic'; topicId: string }

/** `general`, `plan`, or `topic:<id>`: the `context` query parameter and the cache key. */
export const contextParam = (context: TutorContext): string =>
  context.type === 'topic' ? `topic:${context.topicId}` : context.type

/** Mirrors `TutorContextRead`. */
export type TutorContextRead = {
  type: TutorContextType
  topic_id: string | null
  title: string | null
  label: string
  assets: string[]
}

/** Mirrors `TutorThreadRead`. */
export type TutorThreadRead = {
  id: string
  context_type: TutorContextType
  context_id: string | null
  title: string | null
  created_at: string
  updated_at: string
}

/** Mirrors `TutorThreadList`: threads of one context, most recently active first. */
export type TutorThreadList = { context: TutorContextRead; threads: TutorThreadRead[] }

/** Mirrors `CitationRead`. `href` is null for notes, which have no location to open. */
export type CitationRead = {
  chunk_id: string
  source_id: string
  source_title: string
  source_type: string | null
  location: Record<string, unknown>
  label: string
  href: string | null
  excerpt: string
}

/** Mirrors `RelatedTopicRead`. */
export type RelatedTopicRead = { topic_id: string; title: string; score: number; assets: string[] }

/** Mirrors `TutorMessageRead`. */
export type TutorMessageRead = {
  id: string
  thread_id: string
  role: TutorRole
  content: string
  citations: CitationRead[]
  related_topics: RelatedTopicRead[]
  stopped: boolean
  interrupted: Interruption | null
  created_at: string
}

export type TutorStopRead = { stopped: boolean; message: TutorMessageRead | null }

export type DoneEvent = { message: TutorMessageRead; user_message_id: string }
/** `reply` is the partial answer saved as stopped; without one the question was withdrawn. */
export type LimitEvent = {
  reset_at: string | null
  scope: string | null
  message: string
  reply: TutorMessageRead | null
  user_message_id: string | null
}
export type ErrorEvent = {
  code: string
  message: string
  reply: TutorMessageRead | null
  user_message_id: string | null
}

/** Named SSE events of `POST /api/tutor/threads/{id}/messages`. */
export type TutorStreamEvent =
  | { type: 'delta'; data: { text: string } }
  | { type: 'citation'; data: CitationRead }
  | { type: 'related_topic'; data: RelatedTopicRead }
  | { type: 'done'; data: DoneEvent }
  | { type: 'error'; data: ErrorEvent }
  | { type: 'limit'; data: LimitEvent }

const EVENT_TYPES = new Set(['delta', 'citation', 'related_topic', 'done', 'error', 'limit'])

export const tutorKeys = {
  all: ['tutor'] as const,
  threads: (context: string) => [...tutorKeys.all, 'threads', context] as const,
  messages: (threadId: string) => [...tutorKeys.all, 'messages', threadId] as const,
}

const threadsPath = (context: string) => `/api/tutor/threads?context=${encodeURIComponent(context)}`

export const listThreads = (context: string) => apiGet<TutorThreadList>(threadsPath(context))

export const createThread = (context: string) => apiPost<TutorThreadRead>(threadsPath(context))

export const listMessages = (threadId: string) =>
  apiGet<TutorMessageRead[]>(`/api/tutor/threads/${threadId}/messages`)

export const stopReply = (threadId: string) =>
  apiPost<TutorStopRead>(`/api/tutor/threads/${threadId}/stop`)

/** One SSE block (`event:` and `data:` lines) to a typed event; comments and unknown types
 * (keepalives) give null. */
export function parseSseBlock(block: string): TutorStreamEvent | null {
  let type = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) type = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
  }
  if (!EVENT_TYPES.has(type) || data.length === 0) return null
  return { type, data: JSON.parse(data.join('\n')) } as TutorStreamEvent
}

/** Parse a `text/event-stream` body as it arrives. */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<TutorStreamEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, '\n')
      let end = buffer.indexOf('\n\n')
      while (end !== -1) {
        const event = parseSseBlock(buffer.slice(0, end))
        buffer = buffer.slice(end + 2)
        if (event) yield event
        end = buffer.indexOf('\n\n')
      }
    }
    const last = parseSseBlock(buffer)
    if (last) yield last
  } finally {
    reader.releaseLock()
  }
}

const streamConfig = { baseUrl: '' }

/** Tests point the stream at a test server; the app uses same-origin paths. */
export function configureTutorStream(options: { baseUrl?: string }): void {
  Object.assign(streamConfig, options)
}

type StreamOptions = { signal?: AbortSignal; onEvent: (event: TutorStreamEvent) => void }

/**
 * Send a question and read the reply stream. `EventSource` cannot POST, so this reads the
 * response body with `fetch`. Rejections before the stream starts (no provider, busy, 401)
 * throw `ApiError` like every other API call; aborting `signal` ends the read.
 */
export async function streamMessage(
  threadId: string,
  content: string,
  { signal, onEvent }: StreamOptions,
): Promise<void> {
  const response = await fetch(`${streamConfig.baseUrl}/api/tutor/threads/${threadId}/messages`, {
    method: 'POST',
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
    credentials: 'same-origin',
    signal,
  })
  if (!response.ok) {
    const isJson = (response.headers.get('content-type') ?? '').includes('application/json')
    const body: unknown = isJson ? await response.json().catch(() => null) : null
    // The shared client owns the 401 redirect; a cheap authenticated call routes through it.
    if (response.status === 401) void listThreads('general').catch(() => undefined)
    throw toApiError(response.status, body, response.statusText || 'Request failed')
  }
  if (!response.body) throw new ApiError(502, 'no-stream', 'The tutor reply did not stream.')
  for await (const event of readSse(response.body)) onEvent(event)
}
