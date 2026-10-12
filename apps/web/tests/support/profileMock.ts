import { mock } from 'bun:test'

/**
 * Module-boundary mock of src/api/profile. Each function delegates to the real implementation
 * until a test overrides it with `mockImplementation`; `reset()` restores the real ones, so
 * tests of the API module itself are unaffected by the shared module registry.
 */
const real = await import('../../src/api/profile')
// Bun rewrites the live module namespace once mocked; keep the original functions.
const original = { ...real }

export const profileApi = {
  getProfile: mock(original.getProfile),
  createProfile: mock(original.createProfile),
  updateProfile: mock(original.updateProfile),
  addSkill: mock(original.addSkill),
  updateSkill: mock(original.updateSkill),
  deleteSkill: mock(original.deleteSkill),
  addTarget: mock(original.addTarget),
  updateTarget: mock(original.updateTarget),
  deleteTarget: mock(original.deleteTarget),
  reset() {
    profileApi.getProfile.mockReset().mockImplementation(original.getProfile)
    profileApi.createProfile.mockReset().mockImplementation(original.createProfile)
    profileApi.updateProfile.mockReset().mockImplementation(original.updateProfile)
    profileApi.addSkill.mockReset().mockImplementation(original.addSkill)
    profileApi.updateSkill.mockReset().mockImplementation(original.updateSkill)
    profileApi.deleteSkill.mockReset().mockImplementation(original.deleteSkill)
    profileApi.addTarget.mockReset().mockImplementation(original.addTarget)
    profileApi.updateTarget.mockReset().mockImplementation(original.updateTarget)
    profileApi.deleteTarget.mockReset().mockImplementation(original.deleteTarget)
  },
}

mock.module('../../src/api/profile', () => ({
  ...real,
  getProfile: () => profileApi.getProfile(),
  createProfile: (...args: Parameters<typeof original.createProfile>) =>
    profileApi.createProfile(...args),
  updateProfile: (...args: Parameters<typeof original.updateProfile>) =>
    profileApi.updateProfile(...args),
  addSkill: (...args: Parameters<typeof original.addSkill>) => profileApi.addSkill(...args),
  updateSkill: (...args: Parameters<typeof original.updateSkill>) =>
    profileApi.updateSkill(...args),
  deleteSkill: (...args: Parameters<typeof original.deleteSkill>) =>
    profileApi.deleteSkill(...args),
  addTarget: (...args: Parameters<typeof original.addTarget>) => profileApi.addTarget(...args),
  updateTarget: (...args: Parameters<typeof original.updateTarget>) =>
    profileApi.updateTarget(...args),
  deleteTarget: (...args: Parameters<typeof original.deleteTarget>) =>
    profileApi.deleteTarget(...args),
}))
