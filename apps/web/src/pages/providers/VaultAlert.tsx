import { useState } from 'react'
import {
  Alert,
  AlertTitle,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  providerKeys,
  resetVault,
  VAULT_RESET_CONFIRMATION,
  type VaultRead,
} from '../../api/providers'

/** Locked vault: recovery options (restore the key file, or reset after typed confirmation). */
export function VaultAlert({ vault }: { vault: VaultRead }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState('')
  const reset = useMutation({
    mutationFn: () => resetVault(confirm),
    onSuccess: () => {
      setOpen(false)
      void queryClient.invalidateQueries({ queryKey: providerKeys.all })
    },
  })
  if (vault.state !== 'locked') return null
  return (
    <>
      <Alert
        severity="error"
        action={
          <Button color="inherit" onClick={() => setOpen(true)}>
            Reset vault
          </Button>
        }
      >
        <AlertTitle>Credential vault locked</AlertTitle>
        The key file {vault.key_path} is missing or does not match the one your accounts were stored
        with ({vault.detail}). Restore it from backup and reload this page, or reset the vault.
      </Alert>
      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>Reset the credential vault?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Every stored account is deleted and a new key is generated. You will need to connect
            your providers again. Type “{VAULT_RESET_CONFIRMATION}” to confirm.
          </DialogContentText>
          <TextField
            label="Confirmation"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={reset.isError}
            helperText={reset.error?.message}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            color="error"
            disabled={confirm !== VAULT_RESET_CONFIRMATION}
            loading={reset.isPending}
            onClick={() => reset.mutate()}
          >
            Delete accounts and reset
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
