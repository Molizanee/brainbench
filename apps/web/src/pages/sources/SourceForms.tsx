import { useRef, useState } from 'react'
import {
  Controller,
  useForm,
  type FieldValues,
  type Path,
  type UseFormReturn,
} from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Alert, Button, Stack, TextField, Typography } from '@mui/material'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import { ApiError } from '../../api/client'
import { PDF_LIMIT_MESSAGE, PDF_MAX_BYTES } from '../../api/sources'
import { useCreateSource, useUploadPdf } from './useSources'

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

const linkSchema = z.object({
  url: z.string().trim().refine(isHttpUrl, 'Enter an http or https address'),
})
type LinkValues = z.infer<typeof linkSchema>

const noteSchema = z.object({
  title: z.string().trim().max(200, 'Use at most 200 characters'),
  text: z.string().trim().min(1, 'Write the note'),
})
type NoteValues = z.infer<typeof noteSchema>

/** Puts API field errors on their fields; returns the error to show above the form, if any. */
function applyFieldErrors<T extends FieldValues>(
  form: UseFormReturn<T>,
  error: Error | null,
  fields: readonly Path<T>[],
): Error | null {
  if (!(error instanceof ApiError) || !error.fields) return error
  const unmatched = Object.keys(error.fields).filter((f) => !fields.includes(f as Path<T>))
  for (const field of fields) {
    const message = error.fields[field]
    if (message) form.setError(field, { message }, { shouldFocus: true })
  }
  return unmatched.length ? error : null
}

export function LinkForm({ type }: { type: 'url' | 'git' }) {
  const form = useForm<LinkValues>({
    resolver: zodResolver(linkSchema),
    defaultValues: { url: '' },
  })
  const create = useCreateSource()
  const [formError, setFormError] = useState<Error | null>(null)

  const submit = form.handleSubmit((values) => {
    setFormError(null)
    create.mutate(
      { type, url: values.url },
      {
        onSuccess: () => form.reset(),
        onError: (error) => setFormError(applyFieldErrors(form, error, ['url'])),
      },
    )
  })

  return (
    <form onSubmit={(event) => void submit(event)} noValidate>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ alignItems: 'flex-start' }}>
        <Controller
          name="url"
          control={form.control}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              type="url"
              label={type === 'url' ? 'Page URL' : 'Repository URL'}
              placeholder={
                type === 'url' ? 'https://raft.github.io/' : 'https://github.com/etcd-io/raft'
              }
              error={!!fieldState.error}
              helperText={
                fieldState.error?.message ??
                (type === 'git'
                  ? 'Public http(s) repository; README, docs and source files under 1 MB'
                  : ' ')
              }
            />
          )}
        />
        <Button type="submit" variant="contained" loading={create.isPending} sx={{ mt: 1 }}>
          Add
        </Button>
      </Stack>
      {formError && <Alert severity="error">{formError.message}</Alert>}
    </form>
  )
}

export function NoteForm() {
  const form = useForm<NoteValues>({
    resolver: zodResolver(noteSchema),
    defaultValues: { title: '', text: '' },
  })
  const create = useCreateSource()
  const [formError, setFormError] = useState<Error | null>(null)

  const submit = form.handleSubmit((values) => {
    setFormError(null)
    create.mutate(
      { type: 'note', title: values.title, text: values.text },
      {
        onSuccess: () => form.reset(),
        onError: (error) => setFormError(applyFieldErrors(form, error, ['title', 'text'])),
      },
    )
  })

  return (
    <form onSubmit={(event) => void submit(event)} noValidate>
      <Stack spacing={2}>
        {formError && <Alert severity="error">{formError.message}</Alert>}
        <Controller
          name="title"
          control={form.control}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Title (optional)"
              error={!!fieldState.error}
              helperText={fieldState.error?.message ?? 'Defaults to the first line of the note'}
            />
          )}
        />
        <Controller
          name="text"
          control={form.control}
          render={({ field, fieldState }) => (
            <TextField
              {...field}
              label="Note"
              multiline
              minRows={4}
              error={!!fieldState.error}
              helperText={fieldState.error?.message}
            />
          )}
        />
        <div>
          <Button type="submit" variant="contained" loading={create.isPending}>
            Add note
          </Button>
        </div>
      </Stack>
    </form>
  )
}

export function PdfForm() {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const upload = useUploadPdf()

  const choose = (chosen: File | null) => {
    setFile(chosen)
    setError(chosen && chosen.size > PDF_MAX_BYTES ? PDF_LIMIT_MESSAGE : null)
  }

  const submit = () => {
    if (!file) return setError('Choose a PDF file')
    if (file.size > PDF_MAX_BYTES) return setError(PDF_LIMIT_MESSAGE)
    upload.mutate(file, {
      onSuccess: () => {
        choose(null)
        if (input.current) input.current.value = ''
      },
      onError: (failure) => setError(failure.message),
    })
  }

  return (
    <Stack spacing={2}>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
        <Button component="label" variant="outlined" startIcon={<UploadFileIcon />}>
          Choose PDF
          <input
            ref={input}
            hidden
            type="file"
            accept="application/pdf,.pdf"
            aria-label="PDF file"
            onChange={(event) => choose(event.target.files?.[0] ?? null)}
          />
        </Button>
        <Typography color={file ? 'text.primary' : 'text.secondary'} noWrap>
          {file ? file.name : 'No file chosen (up to 50 MB)'}
        </Typography>
      </Stack>
      <div>
        <Button
          variant="contained"
          onClick={submit}
          loading={upload.isPending}
          disabled={!file || file.size > PDF_MAX_BYTES}
        >
          Upload
        </Button>
      </div>
    </Stack>
  )
}
