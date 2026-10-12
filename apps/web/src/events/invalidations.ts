import { sourceKeys } from '../api/sources'
import type { EventInvalidations } from './useEventSource'

/**
 * Event types whose query keys do not follow the prefix rule in `keysForEvent`.
 * Sections add one entry per such event type.
 */
export const eventInvalidations: EventInvalidations = {
  // Written by the ingest job on every source status change.
  'sources.updated': () => [sourceKeys.all],
}
