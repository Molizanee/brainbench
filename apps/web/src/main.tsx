import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { QueryClientProvider } from '@tanstack/react-query'
import { configureApiClient } from './api/client'
import { createQueryClient } from './queryClient'
import { routes } from './routes'
import { theme } from './theme'

const queryClient = createQueryClient()
const router = createBrowserRouter(routes)

configureApiClient({
  onUnauthorized: () => {
    queryClient.clear()
    if (!['/login', '/setup'].includes(router.state.location.pathname)) {
      void router.navigate('/login', { replace: true })
    }
  },
})

const root = document.getElementById('root')
if (!root) throw new Error('missing #root element')

createRoot(root).render(
  <StrictMode>
    <ThemeProvider theme={theme} defaultMode="system">
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
