import { expect, test } from 'bun:test'
import { localUtcOffsetMinutes, progressKeys, sessionLoggedKeys } from '../../src/api/progress'
import { formatMinutes } from '../../src/pages/progress/format'

test('a logged session invalidates the progress summary and its topic', () => {
  expect(sessionLoggedKeys('t-1')).toEqual([['progress'], ['topics', 't-1']])
  expect(progressKeys.summary(-180)).toEqual(['progress', 'summary', -180])
})

test('the local offset is minutes east of UTC', () => {
  expect(localUtcOffsetMinutes()).toBe(-new Date().getTimezoneOffset())
})

test('minutes format as hours and minutes', () => {
  expect(formatMinutes(45)).toBe('45 min')
  expect(formatMinutes(120)).toBe('2 h')
  expect(formatMinutes(135)).toBe('2 h 15 min')
})
