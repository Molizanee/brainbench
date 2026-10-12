import { Suspense, useState, type ComponentType } from 'react'
import { NavLink, Outlet } from 'react-router'
import {
  AppBar,
  Box,
  Container,
  Drawer,
  IconButton,
  LinearProgress,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import MenuIcon from '@mui/icons-material/Menu'
import { eventInvalidations } from '../events/invalidations'
import { useEventSource } from '../events/useEventSource'

const DRAWER_WIDTH = 232

export type NavSection = { path: string; label: string; icon: ComponentType }

type Props = { sections: NavSection[] }

export function AppShell({ sections }: Props) {
  const theme = useTheme()
  const wide = useMediaQuery(theme.breakpoints.up('md'))
  const [open, setOpen] = useState(false)
  useEventSource(eventInvalidations)

  const nav = (
    <List component="nav" aria-label="Main navigation" sx={{ px: 1 }}>
      {sections.map(({ path, label, icon: Icon }) => (
        <ListItemButton
          key={path}
          component={NavLink}
          to={`/${path}`}
          onClick={() => setOpen(false)}
          sx={{ borderRadius: 1, '&.active': { bgcolor: 'action.selected' } }}
        >
          <ListItemIcon>
            <Icon />
          </ListItemIcon>
          <ListItemText primary={label} />
        </ListItemButton>
      ))}
    </List>
  )

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        sx={{ zIndex: (t) => t.zIndex.drawer + 1, borderBottom: 1, borderColor: 'divider' }}
      >
        <Toolbar>
          {!wide && (
            <IconButton edge="start" aria-label="Open navigation" onClick={() => setOpen(true)}>
              <MenuIcon />
            </IconButton>
          )}
          <Typography variant="h6" component="span" sx={{ flexGrow: 1, fontWeight: 600 }}>
            BrainBench
          </Typography>
        </Toolbar>
      </AppBar>
      <Drawer
        variant={wide ? 'permanent' : 'temporary'}
        open={wide || open}
        onClose={() => setOpen(false)}
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
        }}
      >
        <Toolbar />
        {nav}
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, minWidth: 0 }}>
        <Toolbar />
        <Container maxWidth="lg" sx={{ py: 4 }}>
          <Suspense fallback={<LinearProgress aria-label="Loading page" />}>
            <Outlet />
          </Suspense>
        </Container>
      </Box>
    </Box>
  )
}
