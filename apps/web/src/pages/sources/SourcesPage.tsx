import { Alert, Button, Paper, Skeleton, Stack, Typography } from '@mui/material'
import { EmptyState } from '../../components/EmptyState'
import { AddSourceForm } from './AddSourceForm'
import { SourcesTable } from './SourcesTable'
import { useSources } from './useSources'

export default function SourcesPage() {
  const sources = useSources()

  return (
    <Stack spacing={3}>
      <div>
        <Typography variant="h1" gutterBottom>
          Sources
        </Typography>
        <Typography color="text.secondary">
          Add the material you want to study. Each source is extracted, chunked and embedded
          locally.
        </Typography>
      </div>
      <Paper variant="outlined">
        <AddSourceForm />
      </Paper>
      {sources.isPending ? (
        <Skeleton variant="rounded" height={160} aria-label="Loading sources" />
      ) : sources.isError ? (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => void sources.refetch()}>
              Retry
            </Button>
          }
        >
          Could not load sources: {sources.error.message}
        </Alert>
      ) : sources.data.length === 0 ? (
        <EmptyState
          title="No sources yet"
          description="Add a URL, a PDF, a Git repository or a note to get started."
        />
      ) : (
        <SourcesTable sources={sources.data} />
      )}
    </Stack>
  )
}
