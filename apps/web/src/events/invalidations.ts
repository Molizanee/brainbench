import type { EventInvalidations } from './useEventSource'

/**
 * Event types whose query keys do not follow the prefix rule in `keysForEvent`.
 * Sections add one entry per such event type.
 */
export const eventInvalidations: EventInvalidations = {}
