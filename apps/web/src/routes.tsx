import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { Navigate, type RouteObject } from 'react-router'
import CableIcon from '@mui/icons-material/Cable'
import { AppShell } from './components/AppShell'

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
]

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppShell sections={sections} />,
    children: [
      { index: true, element: <Navigate to="/providers" replace /> },
      ...sections.map(({ path, Page }) => ({ path, element: <Page /> })),
    ],
  },
  { path: '*', element: <Navigate to="/" replace /> },
]
