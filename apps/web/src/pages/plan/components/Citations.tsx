import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Link,
  List,
  ListItem,
  ListItemText,
  Typography,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import type { Citation } from '../../../api/plans'
import { citationLocation } from '../format'

type Props = { topicTitle: string; citations: Citation[] }

/** The chunks a topic was derived from, linked to their source location when it has one. */
export function Citations({ topicTitle, citations }: Props) {
  if (citations.length === 0) return null
  return (
    <Accordion disableGutters variant="outlined" sx={{ mt: 1 }}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />} aria-label={`Sources for ${topicTitle}`}>
        <Typography variant="body2">Sources ({citations.length})</Typography>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0 }}>
        <List dense disablePadding>
          {citations.map((citation) => {
            const where = citationLocation(citation)
            const label = where ? `${citation.source_title}, ${where}` : citation.source_title
            return (
              <ListItem key={citation.chunk_id} disableGutters>
                <ListItemText
                  primary={
                    citation.url ? (
                      <Link href={citation.url} target="_blank" rel="noreferrer">
                        {label}
                      </Link>
                    ) : (
                      label
                    )
                  }
                  secondary={citation.excerpt}
                />
              </ListItem>
            )
          })}
        </List>
      </AccordionDetails>
    </Accordion>
  )
}
