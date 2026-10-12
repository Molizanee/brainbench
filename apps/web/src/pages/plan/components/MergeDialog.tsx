import { useState } from 'react'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from '@mui/material'
import type { PlanItemRead } from '../../../api/plans'

type Props = {
  item: PlanItemRead
  candidates: PlanItemRead[]
  error?: string | null
  busy?: boolean
  onClose: () => void
  onMerge: (survivorId: string) => void
}

/** Merges a topic into another: its sessions, assets, tutor threads and sources move over. */
export function MergeDialog({ item, candidates, error, busy = false, onClose, onMerge }: Props) {
  const [survivor, setSurvivor] = useState('')
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="merge-topic-title">
      <DialogTitle id="merge-topic-title">Merge “{item.title}”</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          The topic you choose keeps its title and summary. “{item.title}” is merged into it: its
          study sessions, generated assets, tutor threads, and sources move over, and links to it
          open the merged topic.
        </DialogContentText>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <FormControl fullWidth>
          <InputLabel id="merge-into-label">Merge into</InputLabel>
          <Select
            labelId="merge-into-label"
            label="Merge into"
            value={survivor}
            onChange={(event) => setSurvivor(event.target.value)}
          >
            {candidates.map((candidate) => (
              <MenuItem key={candidate.topic_id} value={candidate.topic_id}>
                {candidate.title}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!survivor || busy} onClick={() => onMerge(survivor)}>
          Merge
        </Button>
      </DialogActions>
    </Dialog>
  )
}
