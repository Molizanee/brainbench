import type { ReactNode } from 'react'
import { Box, Card, CardContent, Typography } from '@mui/material'

type Props = { title: string; subtitle: string; children: ReactNode }

export function AuthCard({ title, subtitle, children }: Props) {
  return (
    <Box sx={{ minHeight: '100vh', display: 'grid', placeItems: 'center', px: 2 }}>
      <Card variant="outlined" sx={{ width: '100%', maxWidth: 420 }}>
        <CardContent sx={{ p: 4 }}>
          <Typography variant="overline" color="primary">
            BrainBench
          </Typography>
          <Typography variant="h1" gutterBottom>
            {title}
          </Typography>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            {subtitle}
          </Typography>
          {children}
        </CardContent>
      </Card>
    </Box>
  )
}
