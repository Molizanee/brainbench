import type { ContentFormat, Depth, Priority, SkillLevel } from '../../api/profile'

export const LEVEL_LABELS: Record<SkillLevel, string> = {
  novice: 'Novice',
  working: 'Working',
  proficient: 'Proficient',
  expert: 'Expert',
}

export const PRIORITY_LABELS: Record<Priority, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export const DEPTH_LABELS: Record<Depth, string> = {
  'intuition-first': 'Intuition first',
  'rigor-first': 'Rigor first',
}

export const FORMAT_LABELS: Record<ContentFormat, string> = {
  video: 'Video',
  figure: 'Interactive figure',
  text: 'Text',
}
