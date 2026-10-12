import type { AnchorHTMLAttributes } from 'react'
import { Link as RouterLink } from 'react-router'
import { Box, Chip, Link, Paper, Stack, Tooltip, Typography } from '@mui/material'
import MenuBookIcon from '@mui/icons-material/MenuBook'
import Markdown from 'markdown-to-jsx/react'
import type { CitationRead, Interruption, RelatedTopicRead } from '../../api/tutor'

/** Links inside a reply open outside the app; raw HTML in the reply is shown as text. */
function ReplyLink(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return <Link {...props} target="_blank" rel="noopener noreferrer" />
}

const MARKDOWN_OPTIONS = {
  disableParsingRawHTML: true,
  optimizeForStreaming: true,
  overrides: { a: ReplyLink },
}

export function CitationChip({ citation }: { citation: CitationRead }) {
  const label = citation.label.replace(/^\[|\]$/g, '')
  const chip = citation.href ? (
    <Chip
      icon={<MenuBookIcon />}
      label={label}
      size="small"
      variant="outlined"
      component="a"
      href={citation.href}
      target="_blank"
      rel="noopener noreferrer"
      clickable
    />
  ) : (
    <Chip icon={<MenuBookIcon />} label={label} size="small" variant="outlined" />
  )
  return (
    <Tooltip title={citation.excerpt} describeChild>
      {chip}
    </Tooltip>
  )
}

const ASSET_LABELS: Record<string, string> = { video: 'Video', figure: 'Figure' }

export function RelatedTopics({ topics }: { topics: RelatedTopicRead[] }) {
  if (topics.length === 0) return null
  return (
    <Box component="nav" aria-label="Related topics" sx={{ mt: 1 }}>
      <Typography variant="caption" color="text.secondary">
        Related topics in your plan
      </Typography>
      <Stack spacing={0.5} sx={{ mt: 0.5 }}>
        {topics.map((topic) => (
          <Stack key={topic.topic_id} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <Link component={RouterLink} to={`/topics/${topic.topic_id}`} variant="body2">
              {topic.title}
            </Link>
            {topic.assets.map((format) => (
              <Chip
                key={format}
                label={`Open ${ASSET_LABELS[format] ?? format}`}
                size="small"
                component={RouterLink}
                to={`/topics/${topic.topic_id}#${format}`}
                clickable
              />
            ))}
          </Stack>
        ))}
      </Stack>
    </Box>
  )
}

const INTERRUPTIONS: Record<Interruption, string> = {
  stop: 'Stopped',
  disconnect: 'Stopped (connection closed)',
  limit: 'Stopped by the provider limit',
  error: 'Stopped by an error',
}

type Props = {
  role: 'user' | 'assistant'
  content: string
  citations?: CitationRead[]
  related?: RelatedTopicRead[]
  stopped?: boolean
  interrupted?: Interruption | null
  streaming?: boolean
}

export function TutorMessage({ role, content, citations = [], related = [], ...state }: Props) {
  if (role === 'user') {
    return (
      <Paper
        variant="outlined"
        sx={{ alignSelf: 'flex-end', maxWidth: '90%', px: 1.5, py: 1, bgcolor: 'action.hover' }}
      >
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
          {content}
        </Typography>
      </Paper>
    )
  }
  return (
    <Box
      aria-busy={state.streaming || undefined}
      sx={{ typography: 'body2', '& p': { my: 1 }, '& pre': { overflowX: 'auto' } }}
    >
      {content ? <Markdown options={MARKDOWN_OPTIONS}>{content}</Markdown> : null}
      {state.streaming && !content && (
        <Typography variant="body2" color="text.secondary">
          Thinking…
        </Typography>
      )}
      {citations.length > 0 && (
        <Stack
          direction="row"
          component="ul"
          aria-label="Sources"
          sx={{ flexWrap: 'wrap', gap: 0.5, mt: 1, mb: 0, p: 0 }}
        >
          {citations.map((citation) => (
            <Box component="li" key={citation.chunk_id} sx={{ listStyle: 'none' }}>
              <CitationChip citation={citation} />
            </Box>
          ))}
        </Stack>
      )}
      <RelatedTopics topics={related} />
      {state.stopped && (
        <Chip
          label={INTERRUPTIONS[state.interrupted ?? 'stop']}
          size="small"
          color="warning"
          variant="outlined"
          sx={{ mt: 1 }}
        />
      )}
    </Box>
  )
}
