import { Typography } from '@mui/material'
import { EmptyState } from '../../components/EmptyState'

export default function ProvidersPage() {
  return (
    <>
      <Typography variant="h1" gutterBottom>
        Providers
      </Typography>
      <EmptyState
        title="No provider connected"
        description="Connect your ChatGPT or Claude subscription to start generating material."
      />
    </>
  )
}
