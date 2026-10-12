import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import ListAltIcon from '@mui/icons-material/ListAlt'
import PublicIcon from '@mui/icons-material/Public'
import TopicIcon from '@mui/icons-material/Topic'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  contextParam,
  createThread,
  listMessages,
  listThreads,
  tutorKeys,
  type TutorContext,
} from '../../api/tutor'
import { Composer } from './Composer'
import { ProblemAlert } from './ProblemAlert'
import { ThreadMenu } from './ThreadMenu'
import { TutorMessage } from './TutorMessage'
import { useTutorContext } from './useTutorContext'
import { useTutorStream } from './useTutorStream'

const FALLBACK_LABELS = { general: 'General', plan: 'Study plan', topic: 'Topic' } as const
const CONTEXT_ICONS = { general: PublicIcon, plan: ListAltIcon, topic: TopicIcon } as const

function ContextChip({ context, label }: { context: TutorContext; label?: string }) {
  const Icon = CONTEXT_ICONS[context.type]
  return (
    <Tooltip title="The tutor answers in this context">
      <Chip
        icon={<Icon />}
        label={label ?? FALLBACK_LABELS[context.type]}
        size="small"
        color={context.type === 'general' ? 'default' : 'primary'}
        aria-label={`Tutor context: ${label ?? FALLBACK_LABELS[context.type]}`}
        sx={{ maxWidth: 220 }}
      />
    </Tooltip>
  )
}

/** The tutor: context chip, thread list, conversation, composer. Mounted once by the shell. */
export function TutorPanel({ onClose }: { onClose: () => void }) {
  const context = useTutorContext()
  const key = contextParam(context)
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const { live, problem, send, stop, clearProblem } = useTutorStream()
  const log = useRef<HTMLDivElement>(null)

  const threads = useQuery({ queryKey: tutorKeys.threads(key), queryFn: () => listThreads(key) })
  const threadId = selected[key] ?? threads.data?.threads[0]?.id ?? null
  const messages = useQuery({
    queryKey: tutorKeys.messages(threadId ?? 'none'),
    queryFn: () => listMessages(threadId as string),
    enabled: threadId !== null,
  })

  const newThread = useMutation({
    mutationFn: (contextKey: string) => createThread(contextKey),
    onSuccess: (thread, contextKey) => {
      queryClient.setQueryData(tutorKeys.messages(thread.id), [])
      setSelected((current) => ({ ...current, [contextKey]: thread.id }))
      void queryClient.invalidateQueries({ queryKey: tutorKeys.threads(contextKey) })
    },
  })

  const liveHere = live && live.threadId === threadId ? live : null
  useEffect(() => {
    const el = log.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.data, liveHere?.text, liveHere?.question])

  const setDraft = (contextKey: string, value: string) =>
    setDrafts((current) => ({ ...current, [contextKey]: value }))

  const ask = async (question: string) => {
    const contextKey = key
    clearProblem()
    setDraft(contextKey, '')
    const id = threadId ?? (await newThread.mutateAsync(contextKey).catch(() => null))?.id
    if (!id) {
      setDraft(contextKey, question)
      return
    }
    const failed = await send(id, question)
    if (failed)
      setDrafts((current) => ({ ...current, [contextKey]: current[contextKey] || question }))
  }

  const history = (messages.data ?? []).filter((m) => !liveHere?.savedIds.includes(m.id))
  const label = threads.data?.context.label

  return (
    <Stack sx={{ height: '100%', minHeight: 0 }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', px: 2, py: 1 }}>
        <Typography variant="h6" component="h2" sx={{ flexGrow: 1 }}>
          Tutor
        </Typography>
        <ThreadMenu
          threads={threads.data?.threads ?? []}
          selectedId={threadId}
          onSelect={(id) => setSelected((current) => ({ ...current, [key]: id }))}
          onNew={() => newThread.mutate(key)}
          disabled={!threads.data || newThread.isPending}
        />
        <IconButton aria-label="Close tutor" onClick={onClose}>
          <CloseIcon />
        </IconButton>
      </Stack>
      <Box sx={{ px: 2, pb: 1 }}>
        <ContextChip context={context} label={label} />
      </Box>
      <Box
        ref={log}
        role="log"
        aria-label="Tutor conversation"
        sx={{ flexGrow: 1, overflowY: 'auto', px: 2, py: 1, borderTop: 1, borderColor: 'divider' }}
      >
        {threads.isError && (
          <Alert
            severity="error"
            action={<Button onClick={() => void threads.refetch()}>Retry</Button>}
          >
            {threads.error.message}
          </Alert>
        )}
        {(threads.isPending || messages.isLoading) && (
          <Skeleton variant="rounded" height={64} aria-label="Loading conversation" />
        )}
        <Stack spacing={2}>
          {threads.data && history.length === 0 && !liveHere && !messages.isLoading && (
            <Typography variant="body2" color="text.secondary">
              Ask about {label ?? FALLBACK_LABELS[context.type].toLowerCase()}. Answers cite your
              sources.
            </Typography>
          )}
          {history.map((m) => (
            <TutorMessage
              key={m.id}
              role={m.role}
              content={m.content}
              citations={m.citations}
              related={m.related_topics}
              stopped={m.stopped}
              interrupted={m.interrupted}
            />
          ))}
          {liveHere && (
            <>
              <TutorMessage role="user" content={liveHere.question} />
              <TutorMessage
                role="assistant"
                content={liveHere.text}
                citations={liveHere.citations}
                related={liveHere.related}
                streaming
              />
            </>
          )}
        </Stack>
      </Box>
      <Stack spacing={1} sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
        {problem && (
          <ProblemAlert
            problem={problem}
            onResend={() => void ask(problem.question)}
            onDismiss={clearProblem}
          />
        )}
        <Composer
          draft={drafts[key] ?? ''}
          onDraftChange={(value) => setDraft(key, value)}
          onSend={(question) => void ask(question)}
          onStop={() => void stop()}
          streaming={liveHere !== null}
          stopping={liveHere?.stopping ?? false}
          busy={live !== null && liveHere === null}
        />
      </Stack>
    </Stack>
  )
}
