import type { ReactNode } from 'react'
import { Box, Typography } from '@mui/material'

type Props = { title: string; description?: string; action?: ReactNode }

export function EmptyState({ title, description, action }: Props) {
  return (
    <Box sx={{ textAlign: 'center', py: 8, px: 2 }}>
      <Typography variant="h2" component="p" gutterBottom>
        {title}
      </Typography>
      {description && (
        <Typography color="text.secondary" sx={{ mb: action ? 3 : 0 }}>
          {description}
        </Typography>
      )}
      {action}
    </Box>
  )
}
