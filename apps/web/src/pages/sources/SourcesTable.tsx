import { useState } from 'react'
import {
  Alert,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  Link,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material'
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined'
import type { SourceRead, SourceStatus, SourceType } from '../../api/sources'
import { useDeleteSource } from './useSources'

const TYPE_LABELS: Record<SourceType, string> = {
  url: 'URL',
  pdf: 'PDF',
  git: 'Git',
  note: 'Note',
}

const STATUS_COLORS: Record<SourceStatus, 'default' | 'info' | 'success' | 'error'> = {
  queued: 'default',
  extracting: 'info',
  embedding: 'info',
  ready: 'success',
  failed: 'error',
}

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

function SourceTitle({ source }: { source: SourceRead }) {
  return (
    <>
      {source.uri ? (
        <Link href={source.uri} target="_blank" rel="noopener noreferrer">
          {source.title}
        </Link>
      ) : (
        <Typography component="span">{source.title}</Typography>
      )}
      {source.error && (
        <Typography variant="body2" color="error" sx={{ mt: 0.5 }}>
          {source.error}
        </Typography>
      )}
    </>
  )
}

export function SourcesTable({ sources }: { sources: SourceRead[] }) {
  const [pending, setPending] = useState<SourceRead | null>(null)
  const remove = useDeleteSource()

  const confirm = () => {
    if (!pending) return
    remove.mutate(pending.id, { onSettled: () => setPending(null) })
  }

  return (
    <>
      {remove.error && <Alert severity="error">{remove.error.message}</Alert>}
      <TableContainer component={Paper} variant="outlined">
        <Table aria-label="Sources">
          <TableHead>
            <TableRow>
              <TableCell>Title</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Status</TableCell>
              <TableCell align="right">Chunks</TableCell>
              <TableCell>Submitted</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {sources.map((source) => (
              <TableRow key={source.id}>
                <TableCell sx={{ maxWidth: 420, wordBreak: 'break-word' }}>
                  <SourceTitle source={source} />
                </TableCell>
                <TableCell>{TYPE_LABELS[source.type]}</TableCell>
                <TableCell>
                  <Chip size="small" label={source.status} color={STATUS_COLORS[source.status]} />
                </TableCell>
                <TableCell align="right">{source.chunk_count}</TableCell>
                <TableCell sx={{ whiteSpace: 'nowrap' }}>
                  {dateFormat.format(new Date(source.created_at))}
                </TableCell>
                <TableCell align="right">
                  <Tooltip title="Delete">
                    <IconButton
                      aria-label={`Delete ${source.title}`}
                      onClick={() => setPending(source)}
                    >
                      <DeleteOutlinedIcon />
                    </IconButton>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <Dialog open={!!pending} onClose={() => setPending(null)} aria-labelledby="delete-title">
        <DialogTitle id="delete-title">Delete source?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            “{pending?.title}” and its chunks will be removed. A running ingestion is cancelled, and
            topics that came only from this source are marked orphaned.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPending(null)}>Cancel</Button>
          <Button color="error" variant="contained" onClick={confirm} loading={remove.isPending}>
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
