import { Box } from '@mui/material'
import type { AssetRead } from '../../api/topics'

type Props = { asset: AssetRead; title: string }

/** Native player with the narration as a captions track; no player library. */
export function VideoViewer({ asset, title }: Props) {
  if (!asset.urls.video) return null
  return (
    <Box
      component="video"
      controls
      preload="metadata"
      src={asset.urls.video}
      aria-label={`Video: ${title}`}
      sx={{ width: '100%', borderRadius: 1, bgcolor: 'common.black', display: 'block' }}
    >
      {asset.urls.captions && (
        <track kind="captions" src={asset.urls.captions} label="Narration" default />
      )}
    </Box>
  )
}
