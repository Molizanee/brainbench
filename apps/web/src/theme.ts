import { createTheme } from '@mui/material/styles'

export const theme = createTheme({
  cssVariables: { colorSchemeSelector: 'class' },
  colorSchemes: {
    light: {
      palette: {
        primary: { main: '#3949ab' },
        secondary: { main: '#00897b' },
        background: { default: '#f6f7fb' },
      },
    },
    dark: {
      palette: {
        primary: { main: '#8c9eff' },
        secondary: { main: '#4db6ac' },
      },
    },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: 'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    h1: { fontSize: '1.75rem', fontWeight: 600 },
    h2: { fontSize: '1.375rem', fontWeight: 600 },
  },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { textTransform: 'none' } },
    },
    MuiTextField: { defaultProps: { fullWidth: true } },
    MuiAppBar: { defaultProps: { elevation: 0, color: 'inherit' } },
  },
})
