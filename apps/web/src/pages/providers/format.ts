const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? dateTime.format(new Date(iso)) : 'unknown'
}

export function formatCount(n: number): string {
  return new Intl.NumberFormat().format(n)
}

export function accountLabel(account: { email: string | null; account_id: string | null }) {
  return account.email ?? account.account_id ?? 'Unknown account'
}
