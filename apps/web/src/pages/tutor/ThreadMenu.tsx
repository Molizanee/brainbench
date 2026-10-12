import { useState } from 'react'
import {
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
} from '@mui/material'
import AddCommentIcon from '@mui/icons-material/AddComment'
import CheckIcon from '@mui/icons-material/Check'
import ForumIcon from '@mui/icons-material/Forum'
import type { TutorThreadRead } from '../../api/tutor'

type Props = {
  threads: TutorThreadRead[]
  selectedId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  disabled: boolean
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })

/** The context's threads (most recent first) and the new-thread action. */
export function ThreadMenu({ threads, selectedId, onSelect, onNew, disabled }: Props) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  return (
    <>
      <Tooltip title="Threads">
        <span>
          <IconButton
            aria-label="Threads"
            aria-haspopup="menu"
            onClick={(event) => setAnchor(event.currentTarget)}
            disabled={threads.length === 0}
          >
            <ForumIcon />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title="New thread">
        <span>
          <IconButton aria-label="New thread" onClick={onNew} disabled={disabled}>
            <AddCommentIcon />
          </IconButton>
        </span>
      </Tooltip>
      <Menu anchorEl={anchor} open={anchor !== null} onClose={() => setAnchor(null)}>
        {threads.map((thread) => (
          <MenuItem
            key={thread.id}
            selected={thread.id === selectedId}
            onClick={() => {
              onSelect(thread.id)
              setAnchor(null)
            }}
            sx={{ maxWidth: 360 }}
          >
            <ListItemIcon>{thread.id === selectedId ? <CheckIcon /> : null}</ListItemIcon>
            <ListItemText
              primary={thread.title ?? 'New thread'}
              secondary={when(thread.updated_at)}
              slotProps={{ primary: { noWrap: true } }}
            />
          </MenuItem>
        ))}
        {threads.length === 0 && (
          <MenuItem disabled>
            <Typography variant="body2">No threads yet</Typography>
          </MenuItem>
        )}
      </Menu>
    </>
  )
}
