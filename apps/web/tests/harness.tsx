import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { theme } from '../src/theme'

/** Render routes inside the theme, a fresh QueryClient, and a memory router. */
export function renderRoutes(routes: RouteObject[], initialPath = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] })
  const view = render(
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>,
  )
  return { ...view, router, queryClient }
}

export function renderElement(element: ReactElement, initialPath = '/') {
  return renderRoutes([{ path: '*', element }], initialPath)
}
