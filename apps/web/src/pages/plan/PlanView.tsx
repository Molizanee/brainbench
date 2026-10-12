import { useState } from 'react'
import {
  Alert,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Typography,
} from '@mui/material'
import { ApiError } from '../../api/client'
import type { PlanEdit, PlanItemRead, PlanRead, PlanSummary } from '../../api/plans'
import { EditTopicDialog } from './components/EditTopicDialog'
import { ExcludedList } from './components/ExcludedList'
import { MergeDialog } from './components/MergeDialog'
import { RunPanel } from './components/RunPanel'
import { TopicCard, type TopicActions } from './components/TopicCard'
import { WeekBlock } from './components/WeekBlock'
import { usePlanEdit } from './usePlan'

type Props = {
  plan: PlanRead
  versions: PlanSummary[]
  onSelectVersion: (version: number) => void
}

type Violation = { topicId: string; message: string }

/** A plan version: metadata, stale alert, weekly blocks, excluded topics, and edit actions. */
export function PlanView({ plan, versions, onSelectVersion }: Props) {
  const edit = usePlanEdit(plan)
  const [violation, setViolation] = useState<Violation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<PlanItemRead | null>(null)
  const [merging, setMerging] = useState<PlanItemRead | null>(null)
  const editable = plan.is_current

  const apply = (body: PlanEdit, topicId: string, done?: () => void) => {
    setViolation(null)
    setError(null)
    edit.mutate(body, {
      onSuccess: () => done?.(),
      onError: (failure) => {
        const message = failure instanceof ApiError ? failure.message : String(failure)
        if (failure instanceof ApiError && failure.code === 'prerequisite-violation') {
          setViolation({ topicId, message })
        } else if (!editing && !merging) {
          setError(message)
        }
      },
    })
  }

  const order = plan.items.map((item) => item.topic_id)
  const move = (index: number, delta: number) => {
    const next = [...order]
    const [moved] = next.splice(index, 1)
    if (moved === undefined) return
    next.splice(index + delta, 0, moved)
    apply({ op: 'reorder', order: next }, moved)
  }
  const actionsFor = (item: PlanItemRead, index: number): TopicActions | undefined =>
    editable
      ? {
          onMoveUp: index > 0 ? () => move(index, -1) : undefined,
          onMoveDown: index < order.length - 1 ? () => move(index, 1) : undefined,
          onEdit: () => setEditing(item),
          onMerge: () => setMerging(item),
          onRemove: () => apply({ op: 'remove', topic_id: item.topic_id }, item.topic_id),
        }
      : undefined

  const dialogError = edit.error instanceof ApiError ? edit.error.message : null
  return (
    <Stack spacing={3}>
      <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap', rowGap: 2 }}>
        <FormControl size="small" sx={{ minWidth: 200 }}>
          <InputLabel id="plan-version-label">Version</InputLabel>
          <Select
            labelId="plan-version-label"
            label="Version"
            value={versions.some((v) => v.version === plan.version) ? plan.version : ''}
            onChange={(event) => onSelectVersion(Number(event.target.value))}
          >
            {versions.map((v) => (
              <MenuItem key={v.version} value={v.version}>
                Version {v.version} ({v.created_by === 'run' ? 'curation run' : 'edit'})
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <Typography color="text.secondary">
          Built from profile version {plan.profile_version} and {plan.source_ids.length}{' '}
          {plan.source_ids.length === 1 ? 'source' : 'sources'}
          {plan.run ? `, relevance threshold ${plan.run.threshold}` : ''}.
        </Typography>
      </Stack>

      {!editable && (
        <Alert
          severity="info"
          action={
            <Button onClick={() => onSelectVersion(plan.current_version)}>Show current</Button>
          }
        >
          You are viewing version {plan.version}. The current version is {plan.current_version}.
        </Alert>
      )}
      {editable && plan.stale && (
        <Alert severity="warning" aria-label="Stale plan">
          <Typography sx={{ fontWeight: 600 }}>This plan is stale.</Typography>
          {plan.stale_reasons.map((reason) => (
            <Typography key={reason} variant="body2">
              {reason}
            </Typography>
          ))}
        </Alert>
      )}
      {editable && <RunPanel label={plan.stale ? 'Re-run curation' : 'Run curation again'} />}
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {plan.weeks.map((week) => (
        <WeekBlock key={week.week_start} week={week} budgetMinutes={plan.weekly_budget_minutes}>
          {plan.items.map((item, index) =>
            item.week_start === week.week_start ? (
              <TopicCard
                key={item.topic_id}
                item={item}
                actions={actionsFor(item, index)}
                busy={edit.isPending}
                violation={violation?.topicId === item.topic_id ? violation.message : null}
              />
            ) : null,
          )}
        </WeekBlock>
      ))}
      {plan.items.length === 0 && (
        <Alert severity="info">Every topic is excluded. Include one below to plan it.</Alert>
      )}

      <ExcludedList
        items={plan.excluded}
        busy={edit.isPending}
        onInclude={
          editable
            ? (item) => apply({ op: 'include', topic_id: item.topic_id }, item.topic_id)
            : undefined
        }
      />

      {editing && (
        <EditTopicDialog
          item={editing}
          error={dialogError}
          busy={edit.isPending}
          onClose={() => setEditing(null)}
          onSave={(changes) =>
            apply({ op: 'edit', topic_id: editing.topic_id, ...changes }, editing.topic_id, () =>
              setEditing(null),
            )
          }
        />
      )}
      {merging && (
        <MergeDialog
          item={merging}
          candidates={[...plan.items, ...plan.excluded].filter(
            (i) => i.topic_id !== merging.topic_id,
          )}
          error={dialogError}
          busy={edit.isPending}
          onClose={() => setMerging(null)}
          onMerge={(survivor) =>
            apply(
              { op: 'merge', topic_id: merging.topic_id, into_topic_id: survivor },
              merging.topic_id,
              () => setMerging(null),
            )
          }
        />
      )}
    </Stack>
  )
}
