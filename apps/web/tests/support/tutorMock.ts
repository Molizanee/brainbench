import { mock } from 'bun:test'
import type {
  TutorContextRead,
  TutorMessageRead,
  TutorStreamEvent,
  TutorThreadList,
  TutorThreadRead,
} from '../../src/api/tutor'

const real = await import('../../src/api/tutor')
// mock.module below patches the module's live bindings, so keep the original functions here.
const original = {
  listThreads: real.listThreads,
  createThread: real.createThread,
  listMessages: real.listMessages,
  stopReply: real.stopReply,
  streamMessage: real.streamMessage,
}

type StreamOptions = Parameters<typeof real.streamMessage>[2]

let counter = 0
const nextId = () => {
  counter += 1
  return `00000000-0000-4000-9000-${String(counter).padStart(12, '0')}`
}

/** A reply the test drives event by event; `streamMessage` resolves once it ends or aborts. */
export class ScriptedReply {
  threadId = ''
  question = ''
  aborted = false
  private onEvent: ((event: TutorStreamEvent) => void) | null = null
  private finish: (() => void) | null = null
  readonly started: Promise<void>
  private markStarted!: () => void

  constructor() {
    this.started = new Promise((resolve) => (this.markStarted = resolve))
  }

  run(threadId: string, question: string, { signal, onEvent }: StreamOptions): Promise<void> {
    this.threadId = threadId
    this.question = question
    this.onEvent = onEvent
    return new Promise((resolve, reject) => {
      this.finish = resolve
      signal?.addEventListener('abort', () => {
        this.aborted = true
        reject(new DOMException('aborted', 'AbortError'))
      })
      this.markStarted()
    })
  }

  emit(event: TutorStreamEvent) {
    this.onEvent?.(event)
  }

  end() {
    this.finish?.()
  }
}

/**
 * Module-boundary mock of src/api/tutor (authMock pattern). `useFakes()` installs an in-memory
 * server: threads per context, messages per thread; replies are `ScriptedReply`s queued with
 * `nextReply()`. `useReal()` restores the real functions for tests of the module itself.
 */
export const tutorApi = {
  contexts: {} as Record<string, TutorContextRead>,
  threads: {} as Record<string, TutorThreadRead[]>,
  messages: {} as Record<string, TutorMessageRead[]>,
  replies: [] as ScriptedReply[],
  listThreads: mock(original.listThreads),
  createThread: mock(original.createThread),
  listMessages: mock(original.listMessages),
  stopReply: mock(original.stopReply),
  streamMessage: mock(original.streamMessage),

  nextReply(): ScriptedReply {
    const reply = new ScriptedReply()
    tutorApi.replies.push(reply)
    return reply
  },

  addThread(context: string, overrides: Partial<TutorThreadRead> = {}): TutorThreadRead {
    const now = new Date(Date.now() + counter).toISOString()
    const thread: TutorThreadRead = {
      id: nextId(),
      context_type: context.startsWith('topic:') ? 'topic' : (context as 'plan' | 'general'),
      context_id: context.startsWith('topic:') ? context.slice(6) : null,
      title: null,
      created_at: now,
      updated_at: now,
      ...overrides,
    }
    tutorApi.threads[context] = [thread, ...(tutorApi.threads[context] ?? [])]
    tutorApi.messages[thread.id] = []
    return thread
  },

  addMessage(threadId: string, overrides: Partial<TutorMessageRead> = {}): TutorMessageRead {
    const message: TutorMessageRead = {
      id: nextId(),
      thread_id: threadId,
      role: 'assistant',
      content: '',
      citations: [],
      related_topics: [],
      stopped: false,
      interrupted: null,
      created_at: new Date().toISOString(),
      ...overrides,
    }
    tutorApi.messages[threadId] = [...(tutorApi.messages[threadId] ?? []), message]
    return message
  },

  useFakes(contexts: Record<string, TutorContextRead> = {}) {
    tutorApi.contexts = contexts
    tutorApi.threads = {}
    tutorApi.messages = {}
    tutorApi.replies = []
    tutorApi.reset()
    tutorApi.listThreads.mockImplementation(async (context: string) => {
      const list: TutorThreadList = {
        context: tutorApi.contexts[context] ?? {
          type: context.startsWith('topic:') ? 'topic' : (context as 'plan' | 'general'),
          topic_id: context.startsWith('topic:') ? context.slice(6) : null,
          title: null,
          label: context === 'plan' ? 'Study plan' : 'General',
          assets: [],
        },
        threads: [...(tutorApi.threads[context] ?? [])],
      }
      return list
    })
    tutorApi.createThread.mockImplementation(async (context: string) => tutorApi.addThread(context))
    tutorApi.listMessages.mockImplementation(async (id: string) => [
      ...(tutorApi.messages[id] ?? []),
    ])
    tutorApi.stopReply.mockImplementation(async () => ({ stopped: true, message: null }))
    tutorApi.streamMessage.mockImplementation((threadId, content, options) => {
      const reply = tutorApi.replies.shift()
      if (!reply) throw new Error('no ScriptedReply queued')
      return reply.run(threadId, content, options)
    })
  },

  useReal() {
    tutorApi.reset()
    tutorApi.listThreads.mockImplementation(original.listThreads)
    tutorApi.createThread.mockImplementation(original.createThread)
    tutorApi.listMessages.mockImplementation(original.listMessages)
    tutorApi.stopReply.mockImplementation(original.stopReply)
    tutorApi.streamMessage.mockImplementation(original.streamMessage)
  },

  reset() {
    for (const fn of [
      tutorApi.listThreads,
      tutorApi.createThread,
      tutorApi.listMessages,
      tutorApi.stopReply,
      tutorApi.streamMessage,
    ]) {
      fn.mockClear()
    }
  },
}

mock.module('../../src/api/tutor', () => ({
  ...real,
  listThreads: (context: string) => tutorApi.listThreads(context),
  createThread: (context: string) => tutorApi.createThread(context),
  listMessages: (id: string) => tutorApi.listMessages(id),
  stopReply: (id: string) => tutorApi.stopReply(id),
  streamMessage: (id: string, content: string, options: StreamOptions) =>
    tutorApi.streamMessage(id, content, options),
}))
