import { Box, Card, CardContent, LinearProgress, Typography } from '@mui/material'
import type { WeeklyPace } from '../../../api/progress'
import { formatMinutes } from '../format'

const dayFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})

export function WeeklyPaceCard({ week }: { week: WeeklyPace }) {
  const lastDay = new Date(new Date(week.week_end).getTime() - 1)
  const range = `${dayFormat.format(new Date(week.week_start))} to ${dayFormat.format(lastDay)}`
  const over = week.used_pct !== null && week.used_pct > 100

  return (
    <Card variant="outlined">
      <CardContent>
        <Typography variant="h2" gutterBottom>
          This week
        </Typography>
        <Typography color="text.secondary" variant="body2" gutterBottom>
          {range}
        </Typography>
        {week.budget_minutes === null || week.used_pct === null ? (
          <Typography>
            {formatMinutes(week.minutes_studied)} studied. Set a weekly time budget in your profile
            to track your pace.
          </Typography>
        ) : (
          <>
            <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
              <Typography variant="h1" component="p">
                {week.used_pct}%
              </Typography>
              <Typography color="text.secondary">of the weekly budget used</Typography>
            </Box>
            <LinearProgress
              variant="determinate"
              value={Math.min(week.used_pct, 100)}
              color={over ? 'warning' : 'primary'}
              aria-label="Weekly budget used"
              sx={{ height: 8, borderRadius: 4, mb: 1 }}
            />
            <Typography variant="body2">
              {formatMinutes(week.minutes_studied)} of {formatMinutes(week.budget_minutes)}
              {over ? ', over budget' : ''}
            </Typography>
          </>
        )}
      </CardContent>
    </Card>
  )
}
