import { useState } from 'react'
import { Box, Link, Stack } from '@mui/material'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import type { AssetRead } from '../../api/topics'

type Props = { asset: AssetRead; title: string }

/**
 * The agent-built figure in an isolated frame: `sandbox="allow-scripts"` without
 * `allow-same-origin` (opaque origin, no cookies, no API access), served under the sandboxing
 * CSP. The PNG preview shows until the frame has loaded.
 */
export function FigureViewer({ asset, title }: Props) {
  const [loaded, setLoaded] = useState(false)
  if (!asset.urls.html) return null
  return (
    <Stack spacing={1}>
      <Box sx={{ position: 'relative', width: '100%', aspectRatio: '16 / 10' }}>
        {!loaded && asset.urls.preview && (
          <Box
            component="img"
            src={asset.urls.preview}
            alt={`Preview of the figure: ${title}`}
            sx={{
              position: 'absolute',
              inset: 0,
              width: '100%',
              height: '100%',
              objectFit: 'contain',
              borderRadius: 1,
            }}
          />
        )}
        <Box
          component="iframe"
          src={asset.urls.html}
          title={`Interactive figure: ${title}`}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          onLoad={() => setLoaded(true)}
          sx={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            border: 0,
            borderRadius: 1,
            opacity: loaded ? 1 : 0,
          }}
        />
      </Box>
      <Link
        href={asset.urls.html}
        target="_blank"
        rel="noopener noreferrer"
        sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, alignSelf: 'flex-start' }}
      >
        Open full screen <OpenInNewIcon fontSize="inherit" />
      </Link>
    </Stack>
  )
}
