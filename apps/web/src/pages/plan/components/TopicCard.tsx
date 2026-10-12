import { Link as RouterLink } from 'react-router'
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  IconButton,
  Link,
  Stack,
  Typography,
} from '@mui/material'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import CallMergeIcon from '@mui/icons-material/CallMerge'
import EditIcon from '@mui/icons-material/Edit'
import RemoveCircleOutlinedIcon from '@mui/icons-material/RemoveCircleOutlined'
import type { PlanItemRead } from '../../../api/plans'
import type { TopicStatus } from '../../../api/progress'
import { formatMinutes, percent, statusLabels } from '../format'
import { Citations } from './Citations'

const statusColor: Record<TopicStatus, 'success' | 'info' | 'default'> = {
  mastered: 'success',
  'in-progress': 'info',
  'not-started': 'default',
}

export type TopicActions = {
  onMoveUp?: () => void
  onMoveDown?: () => void
  onEdit: () => void
  onMerge: () => void
  onRemove: () => void
}

type Props = {
  item: PlanItemRead
  actions?: TopicActions
  busy?: boolean
  violation?: string | null
}

/** One plan topic: summary, why it is in the plan, what it needs first, and its sources. */
export function TopicCard({ item, actions, busy = false, violation }: Props) {
  const topicId = item.merged_into ?? item.topic_id
  const headingId = `topic-${item.topic_id}`
  return (
    <Card variant="outlined" component="article" aria-labelledby={headingId}>
      <CardContent>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'flex-start' }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="h3" id={headingId} sx={{ fontSize: '1.1rem', fontWeight: 600 }}>
              <Link component={RouterLink} to={`/topics/${topicId}`} underline="hover">
                {item.title}
              </Link>
            </Typography>
            <Stack direction="row" spacing={1} sx={{ my: 1, flexWrap: 'wrap', rowGap: 1 }}>
              <Chip
                size="small"
                color={statusColor[item.status]}
                label={statusLabels[item.status]}
              />
              {item.estimate_minutes !== null && (
                <Chip
                  size="small"
                  variant="outlined"
                  label={formatMinutes(item.estimate_minutes)}
                />
              )}
              {item.relevance !== null && (
                <Chip
                  size="small"
                  variant="outlined"
                  label={`Relevance ${percent(item.relevance)}`}
                />
              )}
              {item.manually_included && (
                <Chip size="small" color="secondary" label="Manually included" />
              )}
              {item.merged_into && <Chip size="small" label="Merged" />}
              {item.serves_targets.map((target) => (
                <Chip
                  key={target.id}
                  size="small"
                  variant="outlined"
                  color="primary"
                  label={target.name}
                />
              ))}
            </Stack>
          </Box>
          {actions && (
            <Stack direction="row" sx={{ flexShrink: 0 }}>
              {[
                {
                  label: `Move ${item.title} up`,
                  hint: 'Move up',
                  icon: ArrowUpwardIcon,
                  onClick: actions.onMoveUp,
                },
                {
                  label: `Move ${item.title} down`,
                  hint: 'Move down',
                  icon: ArrowDownwardIcon,
                  onClick: actions.onMoveDown,
                },
                {
                  label: `Edit ${item.title}`,
                  hint: 'Edit title or summary',
                  icon: EditIcon,
                  onClick: actions.onEdit,
                },
                {
                  label: `Merge ${item.title}`,
                  hint: 'Merge into another topic',
                  icon: CallMergeIcon,
                  onClick: actions.onMerge,
                },
                {
                  label: `Remove ${item.title}`,
                  hint: 'Remove from the plan',
                  icon: RemoveCircleOutlinedIcon,
                  onClick: actions.onRemove,
                },
              ].map(({ label, hint, icon: Icon, onClick }) => (
                <IconButton
                  key={hint}
                  aria-label={label}
                  title={hint}
                  onClick={onClick}
                  disabled={busy || !onClick}
                >
                  <Icon fontSize="small" />
                </IconButton>
              ))}
            </Stack>
          )}
        </Stack>
        {violation && (
          <Alert severity="error" sx={{ mb: 1 }} role="alert">
            {violation}
          </Alert>
        )}
        <Typography variant="body2" sx={{ mb: 1 }}>
          {item.summary}
        </Typography>
        {item.rationale && (
          <Typography variant="body2" color="text.secondary">
            Why: {item.rationale}
          </Typography>
        )}
        {item.prerequisites.length > 0 && (
          <Typography variant="body2" color="text.secondary">
            After: {item.prerequisites.map((p) => p.title).join(', ')}
          </Typography>
        )}
        <Citations topicTitle={item.title} citations={item.citations} />
      </CardContent>
    </Card>
  )
}
