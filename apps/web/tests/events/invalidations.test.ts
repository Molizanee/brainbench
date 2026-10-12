import { expect, test } from 'bun:test'
import { eventInvalidations } from '../../src/events/invalidations'
import { keysForEvent } from '../../src/events/useEventSource'

test('sources.updated invalidates every sources query', () => {
  const event = { type: 'sources.updated', payload: { source_id: 's1', status: 'ready' } }
  expect(keysForEvent(event, eventInvalidations)).toEqual([['sources']])
})
