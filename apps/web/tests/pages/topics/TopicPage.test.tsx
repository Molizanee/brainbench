import { afterEach, beforeEach, expect, test } from 'bun:test'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { authApi } from '../../support/authMock'
import { progressApi } from '../../support/progressMock'
import { makeAsset, makeTopic, runningVideoJob, topicsApi } from '../../support/topicsMock'
import { routes } from '../../../src/routes'
import { renderRoutes } from '../../harness'

const video = makeAsset({ id: 'video-2', format: 'video', feedback: 'slower please' })
const olderVideo = makeAsset({
  id: 'video-1',
  format: 'video',
  is_current: false,
  created_at: '2026-10-11T09:00:00Z',
})
const figure = makeAsset({ id: 'figure-1', format: 'figure' })

// happy-dom does not load iframe pages and logs that on the console; the frame's attributes are
// what these tests check.
const consoleError = console.error
afterEach(() => {
  console.error = consoleError
})

beforeEach(() => {
  console.error = (...args: unknown[]) => {
    if (String(args[0]).includes('Iframe page loading is disabled')) return
    consoleError(...args)
  }
  authApi.reset({ password_set: true, authenticated: true })
  progressApi.reset()
  topicsApi.useFakes(makeTopic({ assets: [video, olderVideo, figure], jobs: [runningVideoJob] }))
})

test('the topic page shows the video with captions and the sandboxed figure', async () => {
  renderRoutes(routes, '/topics/raft')
  expect(
    await screen.findByRole('heading', { level: 1, name: 'Raft leader election' }),
  ).toBeDefined()
  expect(screen.getByLabelText('Status: In progress')).toBeDefined()
  expect(topicsApi.getTopic).toHaveBeenCalledWith('raft')

  const player = screen.getByLabelText('Video: Raft leader election') as HTMLVideoElement
  expect(player.tagName).toBe('VIDEO')
  expect(player.getAttribute('src')).toBe('/api/assets/video-2/lesson.mp4')
  expect(player.hasAttribute('controls')).toBe(true)
  const track = player.querySelector('track')
  expect(track?.getAttribute('kind')).toBe('captions')
  expect(track?.getAttribute('src')).toBe('/api/assets/video-2/lesson.vtt')

  const frame = screen.getByTitle('Interactive figure: Raft leader election')
  expect(frame.tagName).toBe('IFRAME')
  expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
  expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer')
  expect(frame.getAttribute('src')).toBe('/api/assets/figure-1/figure.html')
  const preview = screen.getByAltText('Preview of the figure: Raft leader election')
  expect(preview.getAttribute('src')).toBe('/api/assets/figure-1/preview.png')
  const fullScreen = screen.getByRole('link', { name: /Open full screen/ })
  expect(fullScreen.getAttribute('href')).toBe('/api/assets/figure-1/figure.html')
  expect(fullScreen.getAttribute('rel')).toContain('noopener')

  // The PNG preview is a placeholder until the frame has loaded.
  fireEvent.load(frame)
  expect(screen.queryByAltText('Preview of the figure: Raft leader election')).toBeNull()
})

test('the plan line numbers weeks from 1, as the planner and the plan page do', async () => {
  topicsApi.useFakes(
    makeTopic({
      plan_item: {
        plan_version: 1,
        position: 1,
        week_start: 1,
        week_span: 1,
        estimate_minutes: 60,
        relevance: 0.9,
        rationale: null,
        excluded: false,
      },
    }),
  )
  renderRoutes(routes, '/topics/raft')
  expect(await screen.findByText(/plan v1, item 2, week 1, about 1 h/)).toBeDefined()
})

test('storyboard citations, script and build source are shown', async () => {
  const user = userEvent.setup()
  renderRoutes(routes, '/topics/raft')
  const videoCard = await screen.findByRole('region', { name: 'Video' })
  expect(within(videoCard).getByText('1. See why a majority decides')).toBeDefined()
  expect(within(videoCard).getByText('Raft paper, p. 3')).toBeDefined()
  await user.click(within(videoCard).getByRole('tab', { name: 'Script' }))
  expect(within(videoCard).getByLabelText('Manim script').textContent).toContain('class Lesson')

  const figureCard = screen.getByRole('region', { name: 'Figure' })
  expect(within(figureCard).getByText('Figure brief')).toBeDefined()
  await user.click(within(figureCard).getByRole('tab', { name: 'Build source' }))
  expect(within(figureCard).getByText('// build it')).toBeDefined()
  await user.click(within(figureCard).getByRole('tab', { name: 'Session transcript' }))
  expect(within(figureCard).getByLabelText('Session transcript').textContent).toContain(
    'five ballot boxes',
  )
})

test('generate queues the chosen formats and reports a running duplicate', async () => {
  const user = userEvent.setup()
  renderRoutes(routes, '/topics/raft')
  const panel = await screen.findByRole('region', { name: 'Generate visuals' })
  expect(within(panel).getByText('rendering (attempt 1 of 3)')).toBeDefined()
  expect(within(panel).getByText(/557 of 600 render minutes left/)).toBeDefined()
  await user.click(within(panel).getByLabelText('Figure'))
  await user.click(within(panel).getByRole('button', { name: 'Generate' }))
  await waitFor(() => expect(topicsApi.generateForTopic).toHaveBeenCalledWith('raft', ['video']))
  expect(await screen.findByText('Video queued')).toBeDefined()

  topicsApi.generateForTopic.mockImplementation(async () => ({
    jobs: [
      {
        topic_id: 'raft',
        title: null,
        format: 'video',
        job_id: 'j2',
        status: 'running',
        deduplicated: true,
      },
    ],
    budget: topicsApi.topic.budget,
  }))
  await user.click(within(panel).getByRole('button', { name: 'Generate' }))
  expect(await screen.findByText('A video job is already running')).toBeDefined()
})

test('regenerate asks for feedback and sends it', async () => {
  const user = userEvent.setup()
  renderRoutes(routes, '/topics/raft')
  const videoCard = await screen.findByRole('region', { name: 'Video' })
  await user.click(within(videoCard).getByRole('button', { name: 'Regenerate with feedback' }))
  const dialog = await screen.findByRole('dialog', { name: 'Regenerate the video' })
  await user.click(within(dialog).getByRole('button', { name: 'Regenerate' }))
  expect(await within(dialog).findByText('Describe what should change')).toBeDefined()
  expect(topicsApi.regenerateAsset).not.toHaveBeenCalled()
  await user.type(
    within(dialog).getByLabelText('What should change?'),
    'show the proof step by step',
  )
  await user.click(within(dialog).getByRole('button', { name: 'Regenerate' }))
  await waitFor(() =>
    expect(topicsApi.regenerateAsset).toHaveBeenCalledWith(
      'video-2',
      'show the proof step by step',
    ),
  )
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
})

test('asset history lists older versions and shows the one picked', async () => {
  const user = userEvent.setup()
  renderRoutes(routes, '/topics/raft')
  const videoCard = await screen.findByRole('region', { name: 'Video' })
  const history = within(videoCard).getByRole('list', { name: 'Video history' })
  const items = within(history).getAllByRole('button')
  expect(items).toHaveLength(2)
  expect(within(items[0]!).getByText('Current')).toBeDefined()
  expect(within(items[0]!).getByText('Feedback: slower please')).toBeDefined()
  await user.click(items[1]!)
  expect(within(videoCard).getByText('Older version')).toBeDefined()
  const player = within(videoCard).getByLabelText('Video: Raft leader election')
  expect(player.getAttribute('src')).toBe('/api/assets/video-1/lesson.mp4')
})

test('whole-plan generation shows the job count before queueing', async () => {
  const user = userEvent.setup()
  renderRoutes(routes, '/topics/raft')
  await user.click(
    await screen.findByRole('button', { name: 'Generate visuals for the whole plan' }),
  )
  const dialog = await screen.findByRole('dialog', { name: 'Generate visuals for the whole plan' })
  expect(await within(dialog).findByText('3')).toBeDefined()
  expect(within(dialog).getByText(/2 topics of plan version 2/)).toBeDefined()
  expect(within(dialog).getByText(/1 already queued or running/)).toBeDefined()
  expect(topicsApi.generateForPlan).toHaveBeenCalledWith(true)
  expect(topicsApi.generateForPlan).not.toHaveBeenCalledWith(false)
  await user.click(within(dialog).getByRole('button', { name: 'Queue 3 jobs' }))
  await waitFor(() => expect(topicsApi.generateForPlan).toHaveBeenCalledWith(false))
  expect(await screen.findByText('3 jobs queued for the plan')).toBeDefined()
})

test('log session opens the progress dialog for this topic', async () => {
  const user = userEvent.setup()
  renderRoutes(routes, '/topics/raft')
  await user.click(await screen.findByRole('button', { name: 'Log study session' }))
  expect(
    await screen.findByRole('dialog', { name: 'Log session: Raft leader election' }),
  ).toBeDefined()
})

test('a topic without visuals shows the empty state and revision history', async () => {
  const user = userEvent.setup()
  topicsApi.useFakes(makeTopic())
  renderRoutes(routes, '/topics/raft')
  expect(await screen.findByText('No visuals yet')).toBeDefined()
  await user.click(screen.getByText('Revision history (2)'))
  expect(screen.getByText('Revision 1: Leader election')).toBeDefined()
})

test('a merged topic lands on the survivor URL', async () => {
  topicsApi.useFakes(makeTopic({ id: 'survivor' }))
  const { router } = renderRoutes(routes, '/topics/merged')
  await waitFor(() => expect(router.state.location.pathname).toBe('/topics/survivor'))
})
