import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography,
} from '@mui/material'
import type { TargetProgress, TargetTopic, TopicStatus } from '../../../api/progress'
import { formatMinutes, statusLabels } from '../format'

const statusColor: Record<TopicStatus, 'success' | 'info' | 'default'> = {
  mastered: 'success',
  'in-progress': 'info',
  'not-started': 'default',
}

type Props = { target: TargetProgress; onLog: (topic: TargetTopic) => void }

export function TargetCard({ target, onLog }: Props) {
  const { counts } = target
  return (
    <Card variant="outlined" component="section" aria-labelledby={`target-${target.id}`}>
      <CardContent>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 2 }}>
          <Typography variant="h2" id={`target-${target.id}`} sx={{ mr: 1 }}>
            {target.name}
          </Typography>
          <Chip size="small" label={`Goal: ${target.target_level}`} />
          <Chip size="small" variant="outlined" label={`${target.priority} priority`} />
          {target.achieved_at && <Chip size="small" color="success" label="Achieved" />}
        </Stack>

        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: 'repeat(2, 1fr)', sm: 'repeat(5, 1fr)' },
            gap: 2,
            mb: 2,
          }}
        >
          <Stat label="Mastered" value={counts.mastered} />
          <Stat label="In progress" value={counts.in_progress} />
          <Stat label="Not started" value={counts.not_started} />
          <Stat label="Studied" value={formatMinutes(target.minutes_studied)} />
          <Stat label="Remaining" value={formatMinutes(target.minutes_remaining)} />
        </Box>

        {target.topics.length === 0 ? (
          <Typography color="text.secondary">
            No topic in the current plan serves this target yet.
          </Typography>
        ) : (
          <List dense disablePadding aria-label={`Topics for ${target.name}`}>
            {target.topics.map((topic) => (
              <ListItem key={topic.topic_id} divider sx={{ gap: 2, flexWrap: 'wrap' }}>
                <ListItemText
                  primary={topic.title}
                  secondary={topicDetail(topic)}
                  sx={{ flex: '1 1 12rem' }}
                />
                <Chip
                  size="small"
                  color={statusColor[topic.status]}
                  label={statusLabels[topic.status]}
                />
                <Button size="small" onClick={() => onLog(topic)}>
                  Log session
                </Button>
              </ListItem>
            ))}
          </List>
        )}
      </CardContent>
    </Card>
  )
}

function topicDetail(topic: TargetTopic): string {
  const studied = `${formatMinutes(topic.minutes_studied)} studied`
  return topic.estimate_minutes === null
    ? studied
    : `${studied} of about ${formatMinutes(topic.estimate_minutes)}`
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Box>
      <Typography variant="body2" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h6" component="p" aria-label={`${label}: ${value}`}>
        {value}
      </Typography>
    </Box>
  )
}
