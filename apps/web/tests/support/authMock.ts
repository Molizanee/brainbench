import { mock } from 'bun:test'
import type { AuthState } from '../../src/api/auth'

/**
 * Module-boundary mock of src/api/auth. Bun shares the module registry across test files, so
 * every file that renders auth-dependent UI imports this helper and sets the behavior it needs.
 */
export const authApi = {
  state: { password_set: true, authenticated: true } as AuthState,
  getAuthState: mock(async (): Promise<AuthState> => authApi.state),
  setupPassword: mock(async (_password: string): Promise<AuthState> => ({
    password_set: true,
    authenticated: true,
  })),
  login: mock(async (_password: string): Promise<AuthState> => ({
    password_set: true,
    authenticated: true,
  })),
  logout: mock(async (): Promise<void> => undefined),
  reset(state: AuthState) {
    authApi.state = state
    for (const fn of [authApi.getAuthState, authApi.setupPassword, authApi.login, authApi.logout]) {
      fn.mockClear()
    }
  },
}

const real = await import('../../src/api/auth')

mock.module('../../src/api/auth', () => ({
  ...real,
  getAuthState: () => authApi.getAuthState(),
  setupPassword: (password: string) => authApi.setupPassword(password),
  login: (password: string) => authApi.login(password),
  logout: () => authApi.logout(),
}))
