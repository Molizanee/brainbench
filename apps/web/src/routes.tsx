import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { Navigate, type RouteObject } from 'react-router'
import CableIcon from '@mui/icons-material/Cable'
import WorkHistoryIcon from '@mui/icons-material/WorkHistory'
import PersonIcon from '@mui/icons-material/Person'
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks'
import InsightsIcon from '@mui/icons-material/Insights'
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
    path: 'sources',
    label: 'Sources',
    icon: LibraryBooksIcon,
    Page: lazy(() => import('./pages/sources/SourcesPage')),
  },
  {
    path: 'profile',
    label: 'Profile',
    icon: PersonIcon,
    Page: lazy(() => import('./pages/profile/ProfilePage')),
  },
  {
    path: 'jobs',
    label: 'Jobs',
    icon: WorkHistoryIcon,
    Page: lazy(() => import('./pages/jobs/JobsPage')),
  },
  {
    path: 'progress',
    label: 'Progress',
    icon: InsightsIcon,
    Page: lazy(() => import('./pages/progress/ProgressPage')),
  },
]

/**
 * Pages inside the shell that are not navigation sections (reached by links, e.g.
 * `topics/:id` from a plan or progress entry). Sections add entries here for nested routes.
 */
export type PageRoute = { path: string; Page: LazyExoticComponent<ComponentType> }

export const pageRoutes: PageRoute[] = [
  { path: 'topics/:id', Page: lazy(() => import('./pages/topics/TopicPage')) },
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
      ...pageRoutes.map(({ path, Page }) => ({ path, element: <Page /> })),
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]
