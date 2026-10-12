import type { ReactNode } from 'react'
import { Box, Chip, Stack, Typography } from '@mui/material'
import type { PlanWeek } from '../../../api/plans'
import { formatMinutes, weekLabel } from '../format'

type Props = { week: PlanWeek; budgetMinutes: number | null; children: ReactNode }

/** A weekly block; a block spanning several weeks holds one oversized topic. */
export function WeekBlock({ week, budgetMinutes, children }: Props) {
  const label = weekLabel(week)
  const budget =
    budgetMinutes === null ? '' : ` of ${formatMinutes(budgetMinutes * week.week_span)}`
  return (
    <Box component="section" aria-label={label}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
        <Typography variant="h2" sx={{ fontSize: '1.25rem' }}>
          {label}
        </Typography>
        <Typography color="text.secondary">
          {formatMinutes(week.total_minutes)}
          {budget}
        </Typography>
        {week.exceeds_weekly_budget && (
          <Chip size="small" color="warning" label="Exceeds weekly budget" />
        )}
      </Stack>
      <Stack spacing={1.5}>{children}</Stack>
    </Box>
  )
}
