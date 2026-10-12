import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Button,
  Chip,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import EditNoteIcon from '@mui/icons-material/EditNote'
import PlaylistPlayIcon from '@mui/icons-material/PlaylistPlay'
import type { TopicRead } from '../../api/topics'
import { formatMinutes, statusLabels } from '../progress/format'
import { formatDateTime } from './format'

type Props = { topic: TopicRead; onLogSession: () => void; onGeneratePlan: () => void }

export function TopicHeader({ topic, onLogSession, onGeneratePlan }: Props) {
  const plan = topic.plan_item
  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="h1" sx={{ flexGrow: 1 }}>
          {topic.title}
        </Typography>
        <Chip
          label={statusLabels[topic.status]}
          aria-label={`Status: ${statusLabels[topic.status]}`}
        />
      </Stack>
      <Typography>{topic.summary}</Typography>
      <Typography variant="body2" color="text.secondary">
        {formatMinutes(topic.stats.minutes_studied)} studied in {topic.stats.session_count}{' '}
        {topic.stats.session_count === 1 ? 'session' : 'sessions'}
        {plan &&
          !plan.excluded &&
          ` · plan v${plan.plan_version}, item ${plan.position + 1}` +
            (plan.week_start !== null ? `, week ${plan.week_start + 1}` : '') +
            (plan.estimate_minutes ? `, about ${formatMinutes(plan.estimate_minutes)}` : '')}
      </Typography>
      <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
        <Button variant="outlined" startIcon={<EditNoteIcon />} onClick={onLogSession}>
          Log study session
        </Button>
        <Button startIcon={<PlaylistPlayIcon />} onClick={onGeneratePlan}>
          Generate visuals for the whole plan
        </Button>
      </Stack>
      {topic.history.length > 1 && (
        <Accordion disableGutters variant="outlined">
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography>Revision history ({topic.history.length})</Typography>
          </AccordionSummary>
          <AccordionDetails>
            <List dense disablePadding aria-label="Revision history">
              {topic.history.map((revision) => (
                <ListItem key={revision.revision} disableGutters>
                  <ListItemText
                    primary={`Revision ${revision.revision}: ${revision.title}`}
                    secondary={`${revision.created_by === 'edit' ? 'Edited' : 'Curation run'} · ${formatDateTime(revision.created_at)}`}
                  />
                </ListItem>
              ))}
            </List>
          </AccordionDetails>
        </Accordion>
      )}
    </Stack>
  )
}
