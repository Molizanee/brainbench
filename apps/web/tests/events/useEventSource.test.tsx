import { afterEach, beforeEach, expect, test } from 'bun:test'
import { act } from '@testing-library/react'
import { keysForEvent, parseEvent, useEventSource } from '../../src/events/useEventSource'
import { renderElement } from '../harness'

test('parseEvent accepts {type, payload} and rejects garbage', () => {
  expect(parseEvent('{"type":"jobs.updated","payload":{"id":"j1"}}')).toEqual({
    type: 'jobs.updated',
    payload: { id: 'j1' },
  })
  expect(parseEvent('{"type":"ping"}')).toEqual({ type: 'ping', payload: null })
  expect(parseEvent('not json')).toBeNull()
  expect(parseEvent('{"payload":1}')).toBeNull()
})

test('keysForEvent prefers explicit mappings, else the type prefix', () => {
  const explicit = { 'jobs.updated': () => [['jobs'], ['sources', 'list']] }
  expect(keysForEvent({ type: 'jobs.updated', payload: null }, explicit)).toEqual([
    ['jobs'],
    ['sources', 'list'],
  ])
  expect(keysForEvent({ type: 'sources.updated', payload: null }, {})).toEqual([['sources']])
})

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  closed = false
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this)
  }
  close() {
    this.closed = true
  }
  emit(data: string) {
    this.onmessage?.(new MessageEvent('message', { data }))
  }
}

const original = globalThis.EventSource

beforeEach(() => {
  FakeEventSource.instances = []
  Object.assign(globalThis, { EventSource: FakeEventSource })
})

afterEach(() => {
  Object.assign(globalThis, { EventSource: original })
})

function Probe() {
  useEventSource()
  return null
}

test('hook opens one /events stream, invalidates by event, closes on unmount', async () => {
  const { queryClient, unmount } = renderElement(<Probe />)
  const invalidated: unknown[] = []
  queryClient.invalidateQueries = ((filters?: { queryKey?: unknown }) => {
    invalidated.push(filters?.queryKey)
    return Promise.resolve()
  }) as typeof queryClient.invalidateQueries

  expect(FakeEventSource.instances).toHaveLength(1)
  const source = FakeEventSource.instances[0]!
  expect(source.url).toBe('/events')

  await act(async () => source.emit('{"type":"jobs.updated","payload":{}}'))
  expect(invalidated).toEqual([['jobs']])

  unmount()
  expect(source.closed).toBe(true)
})
