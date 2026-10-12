import { afterEach, beforeEach, expect, test } from 'bun:test'
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { tutorApi } from '../../support/tutorMock'
import { ApiError } from '../../../src/api/client'
import type { TutorMessageRead } from '../../../src/api/tutor'
import { TutorPanel } from '../../../src/pages/tutor/TutorPanel'
import { tutorContextFromPath } from '../../../src/pages/tutor/useTutorContext'
import { renderElement } from '../../harness'

const RAFT = 'topic:raft-id'
const PAXOS = 'topic:paxos-id'

beforeEach(() =>
  tutorApi.useFakes({
    [RAFT]: { type: 'topic', topic_id: 'raft-id', title: 'Raft', label: 'Raft', assets: [] },
    [PAXOS]: { type: 'topic', topic_id: 'paxos-id', title: 'Paxos', label: 'Paxos', assets: [] },
  }),
)
afterEach(() => tutorApi.useReal())

const panel = (path: string) => renderElement(<TutorPanel onClose={() => undefined} />, path)
const conversation = () => screen.getByRole('log', { name: 'Tutor conversation' })

test('context comes from the route', () => {
  expect(tutorContextFromPath('/topics/abc')).toEqual({ type: 'topic', topicId: 'abc' })
  expect(tutorContextFromPath('/topics/abc/assets')).toEqual({ type: 'topic', topicId: 'abc' })
  expect(tutorContextFromPath('/plan')).toEqual({ type: 'plan' })
  expect(tutorContextFromPath('/planner')).toEqual({ type: 'general' })
  expect(tutorContextFromPath('/sources')).toEqual({ type: 'general' })
})

test('a topic page shows its context chip and restores the topic thread', async () => {
  const thread = tutorApi.addThread(RAFT, { title: 'Why a majority?' })
  tutorApi.addMessage(thread.id, { role: 'user', content: 'Why a majority?' })
  tutorApi.addMessage(thread.id, { content: 'Majorities **overlap**.' })
  panel('/topics/raft-id')
  expect(await screen.findByLabelText('Tutor context: Raft')).toBeDefined()
  expect(await within(conversation()).findByText('overlap')).toBeDefined()
  expect(within(conversation()).getByText('Why a majority?')).toBeDefined()
  expect(tutorApi.listThreads).toHaveBeenCalledWith(RAFT)
})

test('context follows navigation without losing the other thread', async () => {
  const raft = tutorApi.addThread(RAFT)
  tutorApi.addMessage(raft.id, { content: 'About Raft.' })
  const paxos = tutorApi.addThread(PAXOS)
  tutorApi.addMessage(paxos.id, { content: 'About Paxos.' })
  const { router } = panel('/topics/raft-id')
  expect(await screen.findByText('About Raft.')).toBeDefined()
  await act(() => router.navigate('/topics/paxos-id'))
  expect(await screen.findByLabelText('Tutor context: Paxos')).toBeDefined()
  expect(await screen.findByText('About Paxos.')).toBeDefined()
  expect(screen.queryByText('About Raft.')).toBeNull()
  await act(() => router.navigate('/topics/raft-id'))
  expect(await screen.findByText('About Raft.')).toBeDefined()
  await act(() => router.navigate('/sources'))
  expect(await screen.findByLabelText('Tutor context: General')).toBeDefined()
})

test('a new thread starts empty and the old one stays in the thread list', async () => {
  const user = userEvent.setup()
  const old = tutorApi.addThread('plan', { title: 'Earlier question' })
  tutorApi.addMessage(old.id, { content: 'Earlier answer.' })
  panel('/plan')
  expect(await screen.findByText('Earlier answer.')).toBeDefined()
  await user.click(screen.getByRole('button', { name: 'New thread' }))
  await waitFor(() => expect(screen.queryByText('Earlier answer.')).toBeNull())
  expect(tutorApi.createThread).toHaveBeenCalledWith('plan')
  await user.click(screen.getByRole('button', { name: 'Threads' }))
  const menu = await screen.findByRole('menu')
  await user.click(within(menu).getByText('Earlier question'))
  expect(await screen.findByText('Earlier answer.')).toBeDefined()
})

test('the reply streams with citation chips, related topics, and is saved', async () => {
  const user = userEvent.setup()
  const reply = tutorApi.nextReply()
  panel('/topics/raft-id')
  await screen.findByLabelText('Tutor context: Raft')
  await user.type(screen.getByLabelText('Ask the tutor'), 'Why a majority?{Enter}')
  await reply.started
  // Created the thread on first send, posted the question, and shows it at once.
  expect(tutorApi.createThread).toHaveBeenCalledWith(RAFT)
  expect(reply.question).toBe('Why a majority?')
  // A sent question clears the box without flagging it as an empty question.
  const box = screen.getByLabelText('Ask the tutor') as HTMLTextAreaElement
  expect(box.value).toBe('')
  expect(box.getAttribute('aria-invalid')).toBe('false')
  expect(within(conversation()).getByText('Why a majority?')).toBeDefined()

  act(() => reply.emit({ type: 'delta', data: { text: 'Two majorities ' } }))
  expect(await screen.findByText('Two majorities')).toBeDefined()
  expect(screen.getByRole('button', { name: 'Stop' })).toBeDefined()
  act(() => {
    reply.emit({ type: 'delta', data: { text: 'overlap [Raft paper, p. 5].' } })
    reply.emit({
      type: 'citation',
      data: {
        chunk_id: 'c1',
        source_id: 's1',
        source_title: 'Raft paper',
        source_type: 'pdf',
        location: { page: 5 },
        label: '[Raft paper, p. 5]',
        href: '/api/tutor/sources/s1/file#page=5',
        excerpt: 'A candidate needs a majority.',
      },
    })
    reply.emit({
      type: 'related_topic',
      data: { topic_id: 'log-id', title: 'Log replication', score: 0.9, assets: ['video'] },
    })
  })
  const chip = await screen.findByRole('link', { name: /Raft paper, p\. 5/ })
  expect(chip.getAttribute('href')).toBe('/api/tutor/sources/s1/file#page=5')
  const related = screen.getByRole('navigation', { name: 'Related topics' })
  expect(within(related).getByRole('link', { name: 'Log replication' }).getAttribute('href')).toBe(
    '/topics/log-id',
  )
  expect(within(related).getByRole('link', { name: 'Open Video' }).getAttribute('href')).toBe(
    '/topics/log-id#video',
  )

  // The server saved both messages; the stored copies replace the live one.
  const threadId = reply.threadId
  const question = tutorApi.addMessage(threadId, { role: 'user', content: 'Why a majority?' })
  const saved: TutorMessageRead = tutorApi.addMessage(threadId, {
    content: 'Two majorities overlap [Raft paper, p. 5].',
  })
  act(() => {
    reply.emit({ type: 'done', data: { message: saved, user_message_id: question.id } })
    reply.end()
  })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeDefined())
  expect(within(conversation()).getAllByText('Why a majority?')).toHaveLength(1)
})

test('stop calls the stop endpoint and shows the saved partial reply as stopped', async () => {
  const user = userEvent.setup()
  const thread = tutorApi.addThread('general')
  const reply = tutorApi.nextReply()
  tutorApi.stopReply.mockImplementation(async () => {
    tutorApi.addMessage(thread.id, { role: 'user', content: 'Explain Raft' })
    const message = tutorApi.addMessage(thread.id, {
      content: 'Raft elects',
      stopped: true,
      interrupted: 'stop',
    })
    reply.end()
    return { stopped: true, message }
  })
  panel('/sources')
  await screen.findByLabelText('Tutor context: General')
  await user.type(screen.getByLabelText('Ask the tutor'), 'Explain Raft{Enter}')
  await reply.started
  act(() => reply.emit({ type: 'delta', data: { text: 'Raft elects' } }))
  await user.click(await screen.findByRole('button', { name: 'Stop' }))
  expect(tutorApi.stopReply).toHaveBeenCalledWith(thread.id)
  expect(await screen.findByText('Stopped')).toBeDefined()
  expect(screen.getByText('Raft elects')).toBeDefined()
  expect(screen.getByRole('button', { name: 'Send' })).toBeDefined()
})

test('sending an empty question flags the box until the engineer types', async () => {
  const user = userEvent.setup()
  panel('/')
  await screen.findByLabelText('Tutor context: General')
  const box = screen.getByLabelText('Ask the tutor')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  expect(box.getAttribute('aria-invalid')).toBe('true')
  expect(tutorApi.streamMessage).not.toHaveBeenCalled()
  await user.type(box, 'W')
  expect(box.getAttribute('aria-invalid')).toBe('false')
})

test('without a provider the message is not sent and the panel links to providers', async () => {
  const user = userEvent.setup()
  tutorApi.addThread('general')
  tutorApi.streamMessage.mockImplementation(async () => {
    throw new ApiError(400, 'no-provider', 'No connected provider can run tutoring.')
  })
  panel('/')
  await screen.findByLabelText('Tutor context: General')
  await user.type(screen.getByLabelText('Ask the tutor'), 'Explain Raft{Enter}')
  expect(await screen.findByText('No provider connected')).toBeDefined()
  expect(screen.getByRole('link', { name: 'providers page' }).getAttribute('href')).toBe(
    '/providers',
  )
  expect((screen.getByLabelText('Ask the tutor') as HTMLTextAreaElement).value).toBe('Explain Raft')
  expect(within(conversation()).queryByText('Explain Raft')).toBeNull()
})

test('a provider limit shows the reset time and the message can be resent', async () => {
  const user = userEvent.setup()
  tutorApi.addThread('general')
  const limited = tutorApi.nextReply()
  panel('/')
  await screen.findByLabelText('Tutor context: General')
  await user.type(screen.getByLabelText('Ask the tutor'), 'Explain Raft{Enter}')
  await limited.started
  const resetAt = '2026-10-12T18:30:00Z'
  act(() => {
    limited.emit({
      type: 'limit',
      data: {
        reset_at: resetAt,
        scope: '5h',
        message: 'limit',
        reply: null,
        user_message_id: null,
      },
    })
    limited.end()
  })
  expect(await screen.findByText('Provider limit reached')).toBeDefined()
  const when = new Date(resetAt).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
  expect(screen.getByText(`The limit resets at ${when}. Resend your message after that.`))
  const again = tutorApi.nextReply()
  await user.click(screen.getByRole('button', { name: 'Resend' }))
  await again.started
  expect(again.question).toBe('Explain Raft')
  expect(screen.queryByText('Provider limit reached')).toBeNull()
})
