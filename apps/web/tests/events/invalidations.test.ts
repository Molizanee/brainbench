import { afterEach, expect, test } from 'bun:test'
import { eventInvalidations, jobTypeInvalidations } from '../../src/events/invalidations'
import { keysForEvent } from '../../src/events/useEventSource'
import { makeJob } from '../support/jobsMock'

afterEach(() => {
  delete jobTypeInvalidations.ingest
})

test('jobs.updated refreshes job queries plus the keys its job type registers', () => {
  const job = makeJob({ type: 'ingest', payload: { source_id: 's1' } })
  expect(keysForEvent({ type: 'jobs.updated', payload: job }, eventInvalidations)).toEqual([
    ['jobs'],
  ])
  jobTypeInvalidations.ingest = (j) => [['sources', String(j.payload.source_id)]]
  expect(keysForEvent({ type: 'jobs.updated', payload: job }, eventInvalidations)).toEqual([
    ['jobs'],
    ['sources', 's1'],
  ])
  expect(keysForEvent({ type: 'jobs.updated', payload: null }, eventInvalidations)).toEqual([
    ['jobs'],
  ])
})

test('jobs.event adds nothing on top of jobs.updated', () => {
  expect(keysForEvent({ type: 'jobs.event', payload: {} }, eventInvalidations)).toEqual([])
})
