import { useLocation } from 'react-router'
import type { TutorContext } from '../../api/tutor'

const TOPIC_PATH = /^\/topics\/([^/]+)/
const PLAN_PATH = /^\/plan(\/|$)/

/** `/topics/:id` binds the tutor to that topic, `/plan` to the plan, anything else is general. */
export function tutorContextFromPath(pathname: string): TutorContext {
  const topic = TOPIC_PATH.exec(pathname)
  if (topic?.[1]) return { type: 'topic', topicId: decodeURIComponent(topic[1]) }
  if (PLAN_PATH.test(pathname)) return { type: 'plan' }
  return { type: 'general' }
}

export function useTutorContext(): TutorContext {
  return tutorContextFromPath(useLocation().pathname)
}
