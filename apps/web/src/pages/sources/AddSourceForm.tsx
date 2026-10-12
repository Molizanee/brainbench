import { useState } from 'react'
import { Box, Tab, Tabs } from '@mui/material'
import { LinkForm, NoteForm, PdfForm } from './SourceForms'

const TABS = [
  { value: 'url', label: 'URL' },
  { value: 'pdf', label: 'PDF' },
  { value: 'git', label: 'Git repository' },
  { value: 'note', label: 'Note' },
] as const

type TabValue = (typeof TABS)[number]['value']

export function AddSourceForm() {
  const [tab, setTab] = useState<TabValue>('url')
  return (
    <>
      <Tabs
        value={tab}
        onChange={(_, value: TabValue) => setTab(value)}
        aria-label="Source type"
        sx={{ borderBottom: 1, borderColor: 'divider', px: 2 }}
      >
        {TABS.map(({ value, label }) => (
          <Tab
            key={value}
            value={value}
            label={label}
            id={`source-tab-${value}`}
            aria-controls={`source-panel-${value}`}
          />
        ))}
      </Tabs>
      <Box
        role="tabpanel"
        id={`source-panel-${tab}`}
        aria-labelledby={`source-tab-${tab}`}
        sx={{ p: 2 }}
      >
        {tab === 'url' && <LinkForm key="url" type="url" />}
        {tab === 'pdf' && <PdfForm />}
        {tab === 'git' && <LinkForm key="git" type="git" />}
        {tab === 'note' && <NoteForm />}
      </Box>
    </>
  )
}
