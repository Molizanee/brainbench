import { useState } from 'react'
import { Box, Stack, Tab, Tabs, Typography } from '@mui/material'
import type { AssetRead } from '../../api/topics'
import { StoryboardView } from './StoryboardView'

const codeSx = {
  m: 0,
  p: 1.5,
  bgcolor: 'action.hover',
  borderRadius: 1,
  overflow: 'auto',
  maxHeight: 420,
  fontSize: '0.8rem',
} as const

type TabId = 'storyboard' | 'source' | 'transcript'

/** Provenance of an asset: storyboard with citations, the script or build source, transcript. */
export function AssetDetails({ asset }: { asset: AssetRead }) {
  const [tab, setTab] = useState<TabId>('storyboard')
  const sourceLabel = asset.format === 'video' ? 'Script' : 'Build source'
  return (
    <Box>
      <Tabs
        value={tab}
        onChange={(_, value: TabId) => setTab(value)}
        aria-label="Asset provenance"
        variant="scrollable"
      >
        <Tab value="storyboard" label="Storyboard" />
        <Tab value="source" label={sourceLabel} />
        {asset.transcript_tail && <Tab value="transcript" label="Session transcript" />}
      </Tabs>
      <Box role="tabpanel" sx={{ pt: 2 }}>
        {tab === 'storyboard' &&
          (asset.storyboard ? (
            <StoryboardView storyboard={asset.storyboard} />
          ) : (
            <Typography color="text.secondary">No storyboard stored.</Typography>
          ))}
        {tab === 'source' && asset.format === 'video' && (
          <Box component="pre" sx={codeSx} aria-label="Manim script">
            {asset.script ?? 'No script stored.'}
          </Box>
        )}
        {tab === 'source' && asset.format === 'figure' && (
          <Stack spacing={2}>
            {asset.build_source.length === 0 && (
              <Typography color="text.secondary">No build source stored.</Typography>
            )}
            {asset.build_source.map((file) => (
              <Box key={file.path}>
                <Typography variant="subtitle2" component="h3">
                  {file.path} ({file.size} bytes)
                </Typography>
                <Box component="pre" sx={codeSx}>
                  {file.content ?? 'Too large to show here.'}
                </Box>
              </Box>
            ))}
          </Stack>
        )}
        {tab === 'transcript' && (
          <Box component="pre" sx={codeSx} aria-label="Session transcript">
            {asset.transcript_tail}
          </Box>
        )}
      </Box>
    </Box>
  )
}
