import { useEffect, useRef } from 'react'
import { useQueryClient, type QueryKey } from '@tanstack/react-query'

/** One server-sent event. The API sends unnamed SSE messages whose data is this JSON. */
export type AppEvent = { type: string; payload: unknown }

/** Event type → query keys to invalidate. Types without an entry fall back to the prefix rule. */
export type EventInvalidations = Record<string, (payload: unknown) => QueryKey[]>

export function parseEvent(data: string): AppEvent | null {
  try {
    const value: unknown = JSON.parse(data)
    if (value && typeof value === 'object' && typeof (value as AppEvent).type === 'string') {
      return { type: (value as AppEvent).type, payload: (value as AppEvent).payload ?? null }
    }
  } catch {
    // Ignore malformed frames.
  }
  return null
}

/** Explicit mapping first; otherwise `jobs.updated` invalidates every query under `['jobs']`. */
export function keysForEvent(event: AppEvent, invalidations: EventInvalidations): QueryKey[] {
  const explicit = invalidations[event.type]
  if (explicit) return explicit(event.payload)
  const prefix = event.type.split('.')[0]
  return prefix ? [[prefix]] : []
}

/**
 * Opens the single `EventSource` to `/events` and invalidates TanStack Query keys per event.
 * Mounted once in the app shell; components never open their own `EventSource`.
 */
export function useEventSource(invalidations: EventInvalidations = {}, url = '/events'): void {
  const queryClient = useQueryClient()
  const latest = useRef(invalidations)
  useEffect(() => {
    latest.current = invalidations
  })

  useEffect(() => {
    if (typeof EventSource === 'undefined') return
    const source = new EventSource(url, { withCredentials: true })
    source.onmessage = (message: MessageEvent<string>) => {
      const event = parseEvent(message.data)
      if (!event) return
      for (const queryKey of keysForEvent(event, latest.current)) {
        void queryClient.invalidateQueries({ queryKey })
      }
    }
    return () => source.close()
  }, [queryClient, url])
}
