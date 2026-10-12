import { useState } from 'react'
import {
  Button,
  Card,
  CardContent,
  Chip,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import type { AssetRead, GenerateResponse, VisualFormat } from '../../api/topics'
import { AssetDetails } from './AssetDetails'
import { FigureViewer } from './FigureViewer'
import { formatDateTime, formatDuration, formatLabels } from './format'
import { RegenerateDialog } from './RegenerateDialog'
import { VideoViewer } from './VideoViewer'

type Props = {
  format: VisualFormat
  assets: AssetRead[] // this format only, newest first
  title: string
  onQueued: (result: GenerateResponse) => void
}

/** The current asset of one format (or a chosen older one), its provenance, and its history. */
export function AssetSection({ format, assets, title, onQueued }: Props) {
  const current = assets.find((a) => a.is_current) ?? assets[0]
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState(false)
  const selected = assets.find((a) => a.id === selectedId) ?? current
  if (!selected) return null
  const label = formatLabels[format]
  return (
    <Card component="section" aria-label={label} variant="outlined">
      <CardContent>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography variant="h2" component="h2" sx={{ flexGrow: 1 }}>
              {label}
            </Typography>
            {!selected.is_current && <Chip size="small" label="Older version" />}
            <Button startIcon={<RefreshIcon />} onClick={() => setRegenerating(true)}>
              Regenerate with feedback
            </Button>
          </Stack>
          <Typography variant="body2" color="text.secondary">
            Generated {formatDateTime(selected.created_at)}
            {selected.duration_s !== null && ` · ${formatDuration(selected.duration_s)}`}
            {selected.feedback && ` · from feedback: “${selected.feedback}”`}
          </Typography>
          {format === 'video' ? (
            <VideoViewer key={selected.id} asset={selected} title={title} />
          ) : (
            <FigureViewer key={selected.id} asset={selected} title={title} />
          )}
          <AssetDetails key={`details-${selected.id}`} asset={selected} />
          {assets.length > 1 && (
            <>
              <Typography variant="subtitle1" component="h3">
                History
              </Typography>
              <List dense aria-label={`${label} history`} disablePadding>
                {assets.map((asset) => (
                  <ListItemButton
                    key={asset.id}
                    selected={asset.id === selected.id}
                    onClick={() => setSelectedId(asset.id)}
                  >
                    <ListItemText
                      primary={formatDateTime(asset.created_at)}
                      secondary={asset.feedback ? `Feedback: ${asset.feedback}` : 'First version'}
                    />
                    {asset.is_current && <Chip size="small" color="primary" label="Current" />}
                  </ListItemButton>
                ))}
              </List>
            </>
          )}
        </Stack>
      </CardContent>
      {regenerating && (
        <RegenerateDialog
          open
          asset={selected}
          onClose={() => setRegenerating(false)}
          onQueued={onQueued}
        />
      )}
    </Card>
  )
}
