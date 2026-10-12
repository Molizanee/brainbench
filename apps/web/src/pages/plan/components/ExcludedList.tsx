import { Button, Card, CardContent, List, ListItem, ListItemText, Typography } from '@mui/material'
import type { PlanItemRead } from '../../../api/plans'
import { percent } from '../format'

type Props = {
  items: PlanItemRead[]
  onInclude?: (item: PlanItemRead) => void
  busy?: boolean
}

/** Topics below the relevance threshold (or removed), with the reason, and an include action. */
export function ExcludedList({ items, onInclude, busy = false }: Props) {
  return (
    <Card variant="outlined" component="section" aria-labelledby="excluded-heading">
      <CardContent>
        <Typography variant="h2" id="excluded-heading" sx={{ fontSize: '1.25rem', mb: 1 }}>
          Excluded topics ({items.length})
        </Typography>
        {items.length === 0 ? (
          <Typography color="text.secondary">No topic was excluded.</Typography>
        ) : (
          <List dense disablePadding aria-label="Excluded topics">
            {items.map((item) => (
              <ListItem key={item.topic_id} divider sx={{ gap: 2, flexWrap: 'wrap' }}>
                <ListItemText
                  primary={`${item.title}${item.relevance === null ? '' : ` (relevance ${percent(item.relevance)})`}`}
                  secondary={item.rationale}
                  sx={{ flex: '1 1 16rem' }}
                />
                {onInclude && (
                  <Button
                    size="small"
                    onClick={() => onInclude(item)}
                    disabled={busy}
                    aria-label={`Include ${item.title}`}
                  >
                    Include
                  </Button>
                )}
              </ListItem>
            ))}
          </List>
        )}
      </CardContent>
    </Card>
  )
}
