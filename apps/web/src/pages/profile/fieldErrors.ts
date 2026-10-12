import type { FieldValues, Path, UseFormSetError } from 'react-hook-form'
import { ApiError } from '../../api/client'

/**
 * Put the API's per-field messages (`ApiError.fields`) on the matching form fields. A key deeper
 * than any form field (`preferences.formats.0`) lands on its closest ancestor that is one.
 * Returns the error to show as a form-level alert, or `null` when every message found a field.
 */
export function applyFieldErrors<T extends FieldValues>(
  error: Error,
  fields: readonly Path<T>[],
  setError: UseFormSetError<T>,
): Error | null {
  if (!(error instanceof ApiError) || !error.fields) return error
  let unmatched = false
  for (const [key, message] of Object.entries(error.fields)) {
    const field = closestField(key, fields)
    if (field) setError(field, { type: 'server', message })
    else unmatched = true
  }
  return unmatched ? error : null
}

function closestField<T extends FieldValues>(key: string, fields: readonly Path<T>[]) {
  const parts = key.split('.')
  while (parts.length > 0) {
    const candidate = fields.find((field) => field === parts.join('.'))
    if (candidate) return candidate
    parts.pop()
  }
  return undefined
}
