import { useState } from 'react'
import {
  Alert,
  Button,
  Chip,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Stack,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/DeleteOutlined'
import EditIcon from '@mui/icons-material/EditOutlined'
import { deleteTarget, type TargetSkillRead } from '../../api/profile'
import { LEVEL_LABELS, PRIORITY_LABELS } from './labels'
import { TargetDialog } from './TargetDialog'
import { useProfileMutation } from './useProfile'

type Props = { targets: TargetSkillRead[] }

type Editing = { target?: TargetSkillRead } | null

/** Target skills with target level, priority, and reason: list, add, edit, delete. */
export function TargetsSection({ targets }: Props) {
  const [editing, setEditing] = useState<Editing>(null)
  const remove = useProfileMutation((id: string) => deleteTarget(id))

  return (
    <section aria-labelledby="target-skills-heading">
      <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
        <Typography id="target-skills-heading" variant="h2">
          Target skills
        </Typography>
        <Button startIcon={<AddIcon />} onClick={() => setEditing({})}>
          Add target
        </Button>
      </Stack>
      {remove.error && <Alert severity="error">{remove.error.message}</Alert>}
      {targets.length === 0 ? (
        <Typography color="text.secondary">
          No targets yet. Curation needs at least one target skill to build a plan.
        </Typography>
      ) : (
        <List aria-label="Target skills">
          {targets.map((target) => (
            <ListItem
              key={target.id}
              divider
              secondaryAction={
                <Stack direction="row" spacing={0.5}>
                  <IconButton
                    aria-label={`Edit ${target.name}`}
                    onClick={() => setEditing({ target })}
                  >
                    <EditIcon />
                  </IconButton>
                  <IconButton
                    aria-label={`Delete ${target.name}`}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(target.id)}
                  >
                    <DeleteIcon />
                  </IconButton>
                </Stack>
              }
            >
              <ListItemText primary={target.name} secondary={target.reason} sx={{ pr: 2 }} />
              <Stack direction="row" spacing={1} sx={{ mr: 10 }}>
                <Chip label={`Target: ${LEVEL_LABELS[target.target_level]}`} size="small" />
                <Chip
                  label={`${PRIORITY_LABELS[target.priority]} priority`}
                  size="small"
                  variant="outlined"
                />
                {target.achieved_at && <Chip label="Achieved" size="small" color="success" />}
              </Stack>
            </ListItem>
          ))}
        </List>
      )}
      {editing && <TargetDialog target={editing.target} onClose={() => setEditing(null)} />}
    </section>
  )
}
