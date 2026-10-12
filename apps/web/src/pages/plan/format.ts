import type { Citation, PlanWeek } from '../../api/plans'

export { formatMinutes, statusLabels } from '../progress/format'

/** "Week 3", or "Weeks 2–3" for a block spanning several weeks. */
export function weekLabel(week: Pick<PlanWeek, 'week_start' | 'week_span'>): string {
  if (week.week_span <= 1) return `Week ${week.week_start}`
  return `Weeks ${week.week_start}–${week.week_start + week.week_span - 1}`
}

/** Where in the source a citation points: "page 3", "docs/raft.md", or nothing. */
export function citationLocation(citation: Citation): string {
  const { page, path } = citation.location
  if (typeof page === 'number') return `page ${page}`
  if (typeof path === 'string') return path
  return ''
}

export const percent = (value: number | null) =>
  value === null ? '' : `${Math.round(value * 100)}%`
