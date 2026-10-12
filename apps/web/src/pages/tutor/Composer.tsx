import { useState, type KeyboardEvent } from 'react'
import { Button, Stack, TextField } from '@mui/material'
import SendIcon from '@mui/icons-material/Send'
import StopIcon from '@mui/icons-material/Stop'

type Props = {
  draft: string
  onDraftChange: (value: string) => void
  onSend: (question: string) => void
  onStop: () => void
  streaming: boolean
  stopping: boolean
  /** Another context's reply is still streaming; the tutor answers one question at a time. */
  busy: boolean
}

/** Question box with Send, or Stop while a reply streams. Enter sends, Shift+Enter breaks. */
export function Composer({
  draft,
  onDraftChange,
  onSend,
  onStop,
  streaming,
  stopping,
  busy,
}: Props) {
  const [touched, setTouched] = useState(false)
  const empty = draft.trim() === ''

  const submit = () => {
    setTouched(true)
    if (!empty && !busy && !streaming) onSend(draft.trim())
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <Stack
      component="form"
      direction="row"
      spacing={1}
      sx={{ alignItems: 'flex-end' }}
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <TextField
        label="Ask the tutor"
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={onKeyDown}
        multiline
        maxRows={6}
        fullWidth
        size="small"
        error={touched && empty && !streaming}
        helperText={busy ? 'Waiting for the other reply to finish' : undefined}
      />
      {streaming ? (
        <Button
          variant="outlined"
          color="warning"
          startIcon={<StopIcon />}
          onClick={onStop}
          disabled={stopping}
        >
          {stopping ? 'Stopping' : 'Stop'}
        </Button>
      ) : (
        <Button type="submit" variant="contained" endIcon={<SendIcon />} disabled={busy}>
          Send
        </Button>
      )}
    </Stack>
  )
}
