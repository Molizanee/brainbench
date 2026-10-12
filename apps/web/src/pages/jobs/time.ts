/** Short local date and time for a job timestamp. */
export function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? '-'
    : date.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' })
}

/** The run-after time when it is still ahead (a retry backoff or a provider-limit wait). */
export function pendingRunAfter(iso: string, now: number = Date.now()): string | null {
  const at = new Date(iso).getTime()
  return Number.isNaN(at) || at <= now ? null : formatTime(iso)
}
