import type { BudgetRead, VisualFormat } from '../../api/topics'

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const day = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC' })

export const formatDateTime = (iso: string) => dateTime.format(new Date(iso))

export const formatLabels: Record<VisualFormat, string> = { video: 'Video', figure: 'Figure' }

/** 75.5 → "1:16" (minutes:seconds). */
export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export function budgetSummary(budget: BudgetRead): string {
  const minutes = Math.floor(budget.render_minutes_left)
  return (
    `${minutes} of ${budget.render_minutes_limit} render minutes left this month ` +
    `(resets ${day.format(new Date(budget.render_resets_at))}) · ` +
    `${budget.jobs_left} of ${budget.jobs_limit} generation jobs left today`
  )
}
