import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { Navigate, type RouteObject } from 'react-router'
import CableIcon from '@mui/icons-material/Cable'
import WorkHistoryIcon from '@mui/icons-material/WorkHistory'
import { AppShell } from './components/AppShell'
import { RequireSession } from './components/RequireSession'

const SetupPage = lazy(() => import('./pages/auth/SetupPage'))
const LoginPage = lazy(() => import('./pages/auth/LoginPage'))

/**
 * The single route table. Each section adds one entry to `sections` (its page lives under
 * `src/pages/<section>/`); the app shell builds its navigation from this list.
 */
export type SectionRoute = {
  path: string
  label: string
  icon: ComponentType
  Page: LazyExoticComponent<ComponentType>
}

export const sections: SectionRoute[] = [
  {
    path: 'providers',
    label: 'Providers',
    icon: CableIcon,
    Page: lazy(() => import('./pages/providers/ProvidersPage')),
  },
  {
    path: 'jobs',
    label: 'Jobs',
    icon: WorkHistoryIcon,
    Page: lazy(() => import('./pages/jobs/JobsPage')),
  },
]

export const routes: RouteObject[] = [
  { path: '/setup', element: <SetupPage /> },
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireSession>
        <AppShell sections={sections} />
      </RequireSession>
    ),
    children: [
      { index: true, element: <Navigate to="/providers" replace /> },
      ...sections.map(({ path, Page }) => ({ path, element: <Page /> })),
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]
