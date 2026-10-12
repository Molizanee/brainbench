import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import {
  stopReply,
  streamMessage,
  tutorKeys,
  type CitationRead,
  type RelatedTopicRead,
  type TutorStreamEvent,
} from '../../api/tutor'

/** The reply being streamed. Nothing here is persisted: once it ends, history comes from the
 * messages query, which the server has already updated. `savedIds` are the stored messages
 * this reply stands for, hidden from the history until the live copy is dropped. */
export type LiveReply = {
  threadId: string
  question: string
  text: string
  citations: CitationRead[]
  related: RelatedTopicRead[]
  stopping: boolean
  savedIds: string[]
}

/** Why the last send did not produce a normal reply; `question` is the text to resend. */
export type TutorProblem =
  | { kind: 'no-provider'; message: string; question: string }
  | { kind: 'limit'; resetAt: string | null; message: string; question: string }
  | { kind: 'error'; message: string; question: string }

function savedIds(event: TutorStreamEvent): string[] {
  if (event.type === 'done') return [event.data.user_message_id, event.data.message.id]
  if (event.type === 'limit' || event.type === 'error') {
    return [event.data.user_message_id, event.data.reply?.id].filter((id) => id != null)
  }
  return []
}

function apply(reply: LiveReply, event: TutorStreamEvent): LiveReply {
  switch (event.type) {
    case 'delta':
      return { ...reply, text: reply.text + event.data.text }
    case 'citation':
      return { ...reply, citations: [...reply.citations, event.data] }
    case 'related_topic':
      return { ...reply, related: [...reply.related, event.data] }
    default:
      return { ...reply, savedIds: savedIds(event) }
  }
}

function problemFrom(event: TutorStreamEvent, question: string): TutorProblem | null {
  if (event.type === 'limit') {
    const { reset_at, message } = event.data
    return { kind: 'limit', resetAt: reset_at, message, question }
  }
  if (event.type === 'error') return { kind: 'error', message: event.data.message, question }
  return null
}

const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/** Sends a question, streams the reply into local state, and stops it on request. */
export function useTutorStream() {
  const queryClient = useQueryClient()
  const [live, setLive] = useState<LiveReply | null>(null)
  const [problem, setProblem] = useState<TutorProblem | null>(null)
  const controller = useRef<AbortController | null>(null)
  const streamingThread = useRef<string | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  const refresh = useCallback(
    (threadId: string) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: tutorKeys.messages(threadId) }),
        queryClient.invalidateQueries({ queryKey: [...tutorKeys.all, 'threads'] }),
      ]),
    [queryClient],
  )

  /** Resolves when the reply has ended; returns the problem, if any, so the caller can keep
   * the question for a resend. */
  const send = useCallback(
    async (threadId: string, question: string): Promise<TutorProblem | null> => {
      const abort = new AbortController()
      controller.current = abort
      streamingThread.current = threadId
      let outcome: TutorProblem | null = null
      setProblem(null)
      setLive({
        threadId,
        question,
        text: '',
        citations: [],
        related: [],
        stopping: false,
        savedIds: [],
      })
      try {
        await streamMessage(threadId, question, {
          signal: abort.signal,
          onEvent: (event) => {
            outcome = problemFrom(event, question) ?? outcome
            setLive((current) => (current ? apply(current, event) : current))
          },
        })
      } catch (error) {
        if (error instanceof ApiError && error.code === 'no-provider') {
          outcome = { kind: 'no-provider', message: error.message, question }
        } else if (!isAbort(error)) {
          const message = error instanceof Error ? error.message : 'The tutor did not answer.'
          outcome = { kind: 'error', message, question }
        }
      } finally {
        if (controller.current === abort) {
          controller.current = null
          streamingThread.current = null
        }
        setProblem(outcome)
        await refresh(threadId)
        setLive((current) => (current?.threadId === threadId ? null : current))
      }
      return outcome
    },
    [refresh],
  )

  const stop = useCallback(async () => {
    const current = controller.current
    const threadId = streamingThread.current
    if (!threadId) return
    setLive((reply) => (reply ? { ...reply, stopping: true } : reply))
    await stopReply(threadId).catch(() => undefined)
    // The server ends the stream with the saved partial reply; abort in case it did not.
    current?.abort()
  }, [])

  const clearProblem = useCallback(() => setProblem(null), [])

  return { live, problem, send, stop, clearProblem }
}
