import { Box, Chip, List, ListItem, Stack, Tooltip, Typography } from '@mui/material'
import type { ChunkCitation, StoryboardRead } from '../../api/topics'

function citationLabel(citation: ChunkCitation): string {
  const where = citation.location
  const page = typeof where.page === 'number' ? `p. ${where.page}` : null
  const path = typeof where.path === 'string' ? where.path : null
  const place = page ?? path
  const source = citation.source_title ?? 'Unknown source'
  return place ? `${source}, ${place}` : source
}

type Props = { storyboard: StoryboardRead }

/** Scenes in order with objective, narration, visual, and the chunks each one cites. */
export function StoryboardView({ storyboard }: Props) {
  return (
    <List aria-label="Storyboard scenes" disablePadding>
      {storyboard.scenes.map((scene, index) => (
        <ListItem key={scene.id} disableGutters divider sx={{ display: 'block', py: 1.5 }}>
          <Typography variant="subtitle2" component="h3">
            {index + 1}. {scene.objective}
          </Typography>
          <Typography variant="body2" sx={{ mt: 0.5 }}>
            {scene.narration}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Visual: {scene.visual}
          </Typography>
          <Stack direction="row" spacing={1} useFlexGap sx={{ mt: 1, flexWrap: 'wrap' }}>
            {scene.citations.map((citation) => (
              <Tooltip key={citation.chunk_id} title={citation.excerpt ?? ''}>
                <Chip size="small" variant="outlined" label={citationLabel(citation)} />
              </Tooltip>
            ))}
          </Stack>
        </ListItem>
      ))}
      {storyboard.figure_brief && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2" component="h3">
            Figure brief
          </Typography>
          <Box
            component="pre"
            sx={{ m: 0, mt: 1, p: 1.5, bgcolor: 'action.hover', borderRadius: 1, overflow: 'auto' }}
          >
            {JSON.stringify(storyboard.figure_brief, null, 2)}
          </Box>
        </Box>
      )}
    </List>
  )
}
