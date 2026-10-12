import { mock } from 'bun:test'
import type { SourceCreate, SourceRead } from '../../src/api/sources'

const real = await import('../../src/api/sources')
// mock.module below patches the module's live bindings, so keep the original functions here.
const original = {
  listSources: real.listSources,
  createSource: real.createSource,
  uploadPdf: real.uploadPdf,
  deleteSource: real.deleteSource,
}

/**
 * Module-boundary mock of src/api/sources (same pattern as authMock). Bun shares mocked modules
 * across test files, so `useFakes()` installs in-memory behavior for page tests and `useReal()`
 * restores the real functions for tests of the module itself.
 */
export const sourcesApi = {
  sources: [] as SourceRead[],
  listSources: mock(original.listSources),
  createSource: mock(original.createSource),
  uploadPdf: mock(original.uploadPdf),
  deleteSource: mock(original.deleteSource),

  useFakes(sources: SourceRead[] = []) {
    sourcesApi.sources = [...sources]
    sourcesApi.reset()
    sourcesApi.listSources.mockImplementation(async () => [...sourcesApi.sources])
    sourcesApi.createSource.mockImplementation(async (body: SourceCreate) => {
      const created = makeSource({
        type: body.type,
        title: body.type === 'note' ? body.title || body.text : body.url,
        uri: body.type === 'note' ? null : body.url,
      })
      sourcesApi.sources.unshift(created)
      return created
    })
    sourcesApi.uploadPdf.mockImplementation(async (file: File) => {
      const created = makeSource({ type: 'pdf', title: file.name })
      sourcesApi.sources.unshift(created)
      return created
    })
    sourcesApi.deleteSource.mockImplementation(async (id: string) => {
      sourcesApi.sources = sourcesApi.sources.filter((s) => s.id !== id)
    })
  },

  useReal() {
    sourcesApi.reset()
    sourcesApi.listSources.mockImplementation(original.listSources)
    sourcesApi.createSource.mockImplementation(original.createSource)
    sourcesApi.uploadPdf.mockImplementation(original.uploadPdf)
    sourcesApi.deleteSource.mockImplementation(original.deleteSource)
  },

  reset() {
    for (const fn of [
      sourcesApi.listSources,
      sourcesApi.createSource,
      sourcesApi.uploadPdf,
      sourcesApi.deleteSource,
    ]) {
      fn.mockClear()
    }
  },
}

let counter = 0

export function makeSource(overrides: Partial<SourceRead> = {}): SourceRead {
  counter += 1
  return {
    id: `00000000-0000-4000-8000-${String(counter).padStart(12, '0')}`,
    type: 'note',
    title: `Source ${counter}`,
    uri: null,
    status: 'queued',
    chunk_count: 0,
    error: null,
    job_id: null,
    created_at: '2026-10-11T12:00:00Z',
    updated_at: '2026-10-11T12:00:00Z',
    ...overrides,
  }
}

mock.module('../../src/api/sources', () => ({
  ...real,
  listSources: () => sourcesApi.listSources(),
  createSource: (body: SourceCreate) => sourcesApi.createSource(body),
  uploadPdf: (file: File) => sourcesApi.uploadPdf(file),
  deleteSource: (id: string) => sourcesApi.deleteSource(id),
}))
