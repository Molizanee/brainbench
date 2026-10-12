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
import { deleteSkill, type CurrentSkillRead } from '../../api/profile'
import { LEVEL_LABELS } from './labels'
import { SkillDialog } from './SkillDialog'
import { useProfileMutation } from './useProfile'

type Props = { skills: CurrentSkillRead[] }

type Editing = { skill?: CurrentSkillRead } | null

/** Current skills with their self-assessed level: list, add, edit, delete. */
export function SkillsSection({ skills }: Props) {
  const [editing, setEditing] = useState<Editing>(null)
  const remove = useProfileMutation((id: string) => deleteSkill(id))

  return (
    <section aria-labelledby="current-skills-heading">
      <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
        <Typography id="current-skills-heading" variant="h2">
          Current skills
        </Typography>
        <Button startIcon={<AddIcon />} onClick={() => setEditing({})}>
          Add skill
        </Button>
      </Stack>
      {remove.error && <Alert severity="error">{remove.error.message}</Alert>}
      {skills.length === 0 ? (
        <Typography color="text.secondary">
          No current skills yet. Add what you already know so plans skip it.
        </Typography>
      ) : (
        <List aria-label="Current skills">
          {skills.map((skill) => (
            <ListItem
              key={skill.id}
              divider
              secondaryAction={
                <Stack direction="row" spacing={0.5}>
                  <IconButton
                    aria-label={`Edit ${skill.name}`}
                    onClick={() => setEditing({ skill })}
                  >
                    <EditIcon />
                  </IconButton>
                  <IconButton
                    aria-label={`Delete ${skill.name}`}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(skill.id)}
                  >
                    <DeleteIcon />
                  </IconButton>
                </Stack>
              }
            >
              <ListItemText primary={skill.name} />
              <Chip label={LEVEL_LABELS[skill.level]} size="small" sx={{ mr: 10 }} />
            </ListItem>
          ))}
        </List>
      )}
      {editing && <SkillDialog skill={editing.skill} onClose={() => setEditing(null)} />}
    </section>
  )
}
