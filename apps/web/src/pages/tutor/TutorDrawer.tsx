import { useState } from 'react'
import { Drawer, Toolbar } from '@mui/material'
import { TutorPanel } from './TutorPanel'

export const TUTOR_WIDTH = 400
const OPEN_KEY = 'brainbench.tutor.open'

/** Whether the tutor is open, remembered per browser. */
export function useTutorOpen(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(() => {
    try {
      return window.localStorage.getItem(OPEN_KEY) === '1'
    } catch {
      return false
    }
  })
  const update = (value: boolean) => {
    setOpen(value)
    try {
      window.localStorage.setItem(OPEN_KEY, value ? '1' : '0')
    } catch {
      // Storage disabled: the panel still opens, it just is not remembered.
    }
  }
  return [open, update]
}

type Props = { open: boolean; onClose: () => void; wide: boolean }

/**
 * The tutor sidebar on every page: a persistent drawer beside the content on wide screens, a
 * temporary one on small screens. The panel mounts on first open and then stays mounted, so a
 * streaming reply and the selected threads survive closing it and navigating.
 */
export function TutorDrawer({ open, onClose, wide }: Props) {
  // Derived during render: becomes true on the first open and stays true.
  const [mounted, setMounted] = useState(open)
  if (open && !mounted) setMounted(true)

  return (
    <Drawer
      anchor="right"
      variant={wide ? 'persistent' : 'temporary'}
      open={open}
      onClose={onClose}
      ModalProps={{ keepMounted: true }}
      slotProps={{ paper: { 'aria-label': 'Tutor' } }}
      sx={{
        width: wide && open ? TUTOR_WIDTH : 0,
        flexShrink: 0,
        transition: (t) => t.transitions.create('width'),
        '& .MuiDrawer-paper': {
          width: wide ? TUTOR_WIDTH : 'min(100vw, 440px)',
          boxSizing: 'border-box',
        },
      }}
    >
      {wide && <Toolbar />}
      {mounted && <TutorPanel onClose={onClose} />}
    </Drawer>
  )
}
