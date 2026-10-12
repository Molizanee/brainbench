import { apiDelete, apiGet, apiPost } from './client'

export type SourceType = 'url' | 'pdf' | 'git' | 'note'
export type SourceStatus = 'queued' | 'extracting' | 'embedding' | 'ready' | 'failed'

/** Statuses of a source whose ingestion job has not finished. */
export const IN_PROGRESS_STATUSES: readonly SourceStatus[] = ['queued', 'extracting', 'embedding']

/** Mirrors `SourceRead` in apps/api/app/models/sources.py. */
export type SourceRead = {
  id: string
  type: SourceType
  title: string
  uri: string | null
  status: SourceStatus
  chunk_count: number
  error: string | null
  job_id: string | null
  created_at: string
  updated_at: string
}

/** Mirrors the JSON bodies of `POST /api/sources` (`UrlSourceCreate`, ...). */
export type UrlSourceCreate = { type: 'url'; url: string }
export type GitSourceCreate = { type: 'git'; url: string }
export type NoteSourceCreate = { type: 'note'; title?: string; text: string }
export type SourceCreate = UrlSourceCreate | GitSourceCreate | NoteSourceCreate

export const PDF_MAX_BYTES = 50 * 1024 * 1024
export const PDF_LIMIT_MESSAGE = 'PDF uploads are limited to 50 MB.'

/** `sources.updated` events invalidate everything under `sourceKeys.all`. */
export const sourceKeys = {
  all: ['sources'] as const,
  list: () => [...sourceKeys.all, 'list'] as const,
}

export const listSources = () => apiGet<SourceRead[]>('/api/sources')

export const createSource = (body: SourceCreate) => apiPost<SourceRead>('/api/sources', body)

/** Multipart upload; the Bun server streams the body through to the API unchanged. */
export const uploadPdf = (file: File) => {
  const form = new FormData()
  form.append('type', 'pdf')
  form.append('file', file, file.name)
  return apiPost<SourceRead>('/api/sources', form)
}

export const deleteSource = (id: string) => apiDelete(`/api/sources/${id}`)
