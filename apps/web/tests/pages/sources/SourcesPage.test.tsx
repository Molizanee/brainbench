import { beforeEach, expect, test } from 'bun:test'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { makeSource, sourcesApi } from '../../support/sourcesMock'
import { ApiError } from '../../../src/api/client'
import { PDF_MAX_BYTES } from '../../../src/api/sources'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

beforeEach(() => {
  authApi.reset({ password_set: true, authenticated: true })
  sourcesApi.useFakes()
})

async function openPage() {
  const view = renderRoutes(routes, '/sources')
  await screen.findByRole('heading', { name: 'Sources' })
  return { ...view, user: userEvent.setup() }
}

test('sources is in the navigation and lists type, title, status, chunks and time', async () => {
  sourcesApi.useFakes([
    makeSource({ type: 'pdf', title: 'raft.pdf', status: 'ready', chunk_count: 42 }),
    makeSource({
      type: 'url',
      title: 'example.com/broken',
      uri: 'https://example.com/broken',
      status: 'failed',
      error: 'Downloading https://example.com/broken failed with HTTP status 404.',
    }),
  ])
  await openPage()
  const nav = screen.getByRole('navigation', { name: 'Main navigation' })
  expect(nav.textContent).toContain('Sources')

  const table = await screen.findByRole('table', { name: 'Sources' })
  const rows = within(table).getAllByRole('row').slice(1)
  expect(rows).toHaveLength(2)
  expect(rows[0]!.textContent).toContain('raft.pdf')
  expect(rows[0]!.textContent).toContain('PDF')
  expect(rows[0]!.textContent).toContain('ready')
  expect(rows[0]!.textContent).toContain('42')
  expect(rows[0]!.textContent).toContain('2026')
  expect(rows[1]!.textContent).toContain('failed')
  expect(rows[1]!.textContent).toContain('HTTP status 404')
  expect(within(rows[1]!).getByRole('link', { name: 'example.com/broken' })).toBeDefined()
})

test('empty list shows the empty state', async () => {
  await openPage()
  expect(await screen.findByText('No sources yet')).toBeDefined()
})

test('URL form validates the address before calling the API, then adds the source', async () => {
  const { user } = await openPage()
  const field = screen.getByLabelText('Page URL')
  await user.type(field, 'ftp://example.com')
  await user.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByText('Enter an http or https address')).toBeDefined()
  expect(sourcesApi.createSource).not.toHaveBeenCalled()

  await user.clear(field)
  await user.type(field, 'https://raft.github.io/')
  await user.click(screen.getByRole('button', { name: 'Add' }))
  await waitFor(() =>
    expect(sourcesApi.createSource).toHaveBeenCalledWith({
      type: 'url',
      url: 'https://raft.github.io/',
    }),
  )
  const table = await screen.findByRole('table', { name: 'Sources' })
  expect(table.textContent).toContain('https://raft.github.io/')
})

test('API validation errors land on the field', async () => {
  sourcesApi.createSource.mockImplementationOnce(async () => {
    throw new ApiError(422, 'validation-error', 'Some fields are invalid', {
      url: 'URL scheme should be http or https',
    })
  })
  const { user } = await openPage()
  await user.click(screen.getByRole('tab', { name: 'Git repository' }))
  await user.type(screen.getByLabelText('Repository URL'), 'https://github.com/etcd-io/raft')
  await user.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByText('URL scheme should be http or https')).toBeDefined()
  expect(sourcesApi.createSource).toHaveBeenCalledWith({
    type: 'git',
    url: 'https://github.com/etcd-io/raft',
  })
})

test('note form sends title and text', async () => {
  const { user } = await openPage()
  await user.click(screen.getByRole('tab', { name: 'Note' }))
  await user.type(screen.getByLabelText('Title (optional)'), 'Raft notes')
  await user.type(screen.getByRole('textbox', { name: 'Note' }), 'Terms are logical clocks.')
  await user.click(screen.getByRole('button', { name: 'Add note' }))
  await waitFor(() =>
    expect(sourcesApi.createSource).toHaveBeenCalledWith({
      type: 'note',
      title: 'Raft notes',
      text: 'Terms are logical clocks.',
    }),
  )
})

test('PDF over 50 MB is refused in the browser; a valid PDF is uploaded', async () => {
  const { user } = await openPage()
  await user.click(screen.getByRole('tab', { name: 'PDF' }))
  const input = screen.getByLabelText('PDF file')

  const big = new File(['%PDF-'], 'big.pdf', { type: 'application/pdf' })
  Object.defineProperty(big, 'size', { value: PDF_MAX_BYTES + 1 })
  await user.upload(input, big)
  expect(await screen.findByText('PDF uploads are limited to 50 MB.')).toBeDefined()
  expect(screen.getByRole('button', { name: 'Upload' }).hasAttribute('disabled')).toBe(true)

  const pdf = new File(['%PDF-1.7'], 'raft.pdf', { type: 'application/pdf' })
  await user.upload(input, pdf)
  await user.click(screen.getByRole('button', { name: 'Upload' }))
  await waitFor(() => expect(sourcesApi.uploadPdf).toHaveBeenCalledTimes(1))
  expect(sourcesApi.uploadPdf.mock.calls[0]![0].name).toBe('raft.pdf')
  const table = await screen.findByRole('table', { name: 'Sources' })
  expect(table.textContent).toContain('raft.pdf')
})

test('server-side upload errors are shown', async () => {
  sourcesApi.uploadPdf.mockImplementationOnce(async () => {
    throw new ApiError(413, 'file-too-large', 'PDF uploads are limited to 50 MB.')
  })
  const { user } = await openPage()
  await user.click(screen.getByRole('tab', { name: 'PDF' }))
  await user.upload(
    screen.getByLabelText('PDF file'),
    new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' }),
  )
  await user.click(screen.getByRole('button', { name: 'Upload' }))
  expect(await screen.findByText('PDF uploads are limited to 50 MB.')).toBeDefined()
})

test('delete asks for confirmation and removes the source', async () => {
  const source = makeSource({ title: 'old notes', status: 'ready', chunk_count: 3 })
  sourcesApi.useFakes([source])
  const { user } = await openPage()
  await user.click(await screen.findByRole('button', { name: 'Delete old notes' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete source?' })
  await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(sourcesApi.deleteSource).toHaveBeenCalledWith(source.id))
  expect(await screen.findByText('No sources yet')).toBeDefined()
})
