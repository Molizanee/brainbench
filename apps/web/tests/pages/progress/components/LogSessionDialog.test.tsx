import { beforeEach, expect, mock, test } from 'bun:test'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { progressApi } from '../../../support/progressMock'
import { ApiError } from '../../../../src/api/client'
import { LogSessionDialog } from '../../../../src/pages/progress/components/LogSessionDialog'
import { renderElement } from '../../../harness'

beforeEach(() => progressApi.reset())

function renderDialog(onClose = mock(() => undefined), onLogged = mock(() => undefined)) {
  const view = renderElement(
    <LogSessionDialog
      open
      topicId="topic-7"
      topicTitle="Raft"
      onClose={onClose}
      onLogged={onLogged}
    />,
  )
  return { ...view, onClose, onLogged, user: userEvent.setup() }
}

test('requires minutes and a rating before calling the API', async () => {
  const { user } = renderDialog()
  await user.click(screen.getByRole('button', { name: 'Log session' }))
  expect(await screen.findByText('Choose how well you mastered it')).toBeDefined()
  expect(screen.getByText('Log at least 1 minute')).toBeDefined()
  expect(progressApi.logSession).not.toHaveBeenCalled()
})

test('rejects zero minutes', async () => {
  const { user } = renderDialog()
  await user.type(screen.getByLabelText('Minutes studied'), '0')
  await user.click(screen.getByRole('button', { name: 'Confident' }))
  await user.click(screen.getByRole('button', { name: 'Log session' }))
  expect(await screen.findByText('Log at least 1 minute')).toBeDefined()
  expect(progressApi.logSession).not.toHaveBeenCalled()
})

test('logs the session, invalidates progress and the topic, then closes', async () => {
  const { user, queryClient, onClose, onLogged } = renderDialog()
  const invalidate = mock(queryClient.invalidateQueries.bind(queryClient))
  queryClient.invalidateQueries = invalidate as typeof queryClient.invalidateQueries

  await user.type(screen.getByLabelText('Minutes studied'), '45')
  await user.click(screen.getByRole('button', { name: 'Understood' }))
  await user.type(screen.getByLabelText('Note (optional)'), '  log replication ')
  await user.click(screen.getByRole('button', { name: 'Log session' }))

  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(progressApi.logSession).toHaveBeenCalledWith('topic-7', {
    minutes: 45,
    rating: 'understood',
    note: 'log replication',
  })
  expect(onLogged).toHaveBeenCalled()
  expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
    ['progress'],
    ['topics', 'topic-7'],
  ])
})

test('API field errors appear on the field', async () => {
  progressApi.logSession.mockImplementation(async () => {
    throw new ApiError(422, 'validation-error', 'Some fields are invalid', {
      minutes: 'Input should be greater than 0',
    })
  })
  const { user, onClose } = renderDialog()
  await user.type(screen.getByLabelText('Minutes studied'), '5')
  await user.click(screen.getByRole('button', { name: 'Struggled' }))
  await user.click(screen.getByRole('button', { name: 'Log session' }))
  expect(await screen.findByText('Input should be greater than 0')).toBeDefined()
  expect(onClose).not.toHaveBeenCalled()
})

test('a missing topic shows the API message', async () => {
  progressApi.logSession.mockImplementation(async () => {
    throw new ApiError(404, 'topic-not-found', 'Topic topic-7 does not exist.')
  })
  const { user } = renderDialog()
  await user.type(screen.getByLabelText('Minutes studied'), '5')
  await user.click(screen.getByRole('button', { name: 'Struggled' }))
  await user.click(screen.getByRole('button', { name: 'Log session' }))
  expect(await screen.findByText('Topic topic-7 does not exist.')).toBeDefined()
})
