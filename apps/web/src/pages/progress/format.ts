import type { MasteryRating, TopicStatus } from '../../api/progress'

/** 45 → "45 min", 120 → "2 h", 135 → "2 h 15 min". */
export function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours === 0) return `${rest} min`
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`
}

export const ratingLabels: Record<MasteryRating, string> = {
  struggled: 'Struggled',
  understood: 'Understood',
  confident: 'Confident',
  'could-teach': 'Could teach it',
}

export const statusLabels: Record<TopicStatus, string> = {
  'not-started': 'Not started',
  'in-progress': 'In progress',
  mastered: 'Mastered',
}
