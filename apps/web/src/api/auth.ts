import { apiGet, apiPost } from './client'

/** Mirrors `AuthState` in apps/api/app/models/auth.py. */
export type AuthState = { password_set: boolean; authenticated: boolean }

export const PASSWORD_MIN_LENGTH = 8

export const authKeys = {
  all: ['auth'] as const,
  me: () => [...authKeys.all, 'me'] as const,
}

export const getAuthState = () => apiGet<AuthState>('/api/auth/me')

export const setupPassword = (password: string) =>
  apiPost<AuthState>('/api/auth/setup', { password }, { redirectOn401: false })

export const login = (password: string) =>
  apiPost<AuthState>('/api/auth/login', { password }, { redirectOn401: false })

export const logout = () => apiPost<void>('/api/auth/logout')
