import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from './client'

/** Value sets from the learner-profile spec (mirror `app/models/profile.py`). */
export const SKILL_LEVELS = ['novice', 'working', 'proficient', 'expert'] as const
export const TARGET_LEVELS = ['working', 'proficient', 'expert'] as const
export const PRIORITIES = ['high', 'medium', 'low'] as const
export const DEPTHS = ['intuition-first', 'rigor-first'] as const
export const CONTENT_FORMATS = ['video', 'figure', 'text'] as const

export type SkillLevel = (typeof SKILL_LEVELS)[number]
export type TargetLevel = (typeof TARGET_LEVELS)[number]
export type Priority = (typeof PRIORITIES)[number]
export type Depth = (typeof DEPTHS)[number]
export type ContentFormat = (typeof CONTENT_FORMATS)[number]

export type Preferences = { formats: ContentFormat[]; depth: Depth; language: string }

export type CurrentSkillRead = { id: string; name: string; level: SkillLevel }

export type TargetSkillRead = {
  id: string
  name: string
  target_level: TargetLevel
  priority: Priority
  reason: string | null
  achieved_at: string | null
}

export type ProfileRead = {
  id: string
  display_name: string
  background: string
  weekly_budget_hours: number
  preferences: Preferences
  current_skills: CurrentSkillRead[]
  target_skills: TargetSkillRead[]
  version: number
  created_at: string
  updated_at: string
}

export type CurrentSkillCreate = { name: string; level: SkillLevel }
export type CurrentSkillUpdate = Partial<CurrentSkillCreate>

export type TargetSkillCreate = {
  name: string
  target_level: TargetLevel
  priority: Priority
  reason?: string | null
}
export type TargetSkillUpdate = Partial<TargetSkillCreate>

export type ProfileCreate = {
  display_name: string
  background?: string
  weekly_budget_hours: number
  preferences?: Preferences
  current_skills?: CurrentSkillCreate[]
  target_skills?: TargetSkillCreate[]
}

export type ProfileUpdate = {
  display_name?: string
  background?: string
  weekly_budget_hours?: number
  preferences?: Partial<Preferences>
}

export const profileKeys = {
  all: ['profile'] as const,
  detail: () => [...profileKeys.all, 'detail'] as const,
}

/** The instance's profile, or `null` before one is created (404 `profile-missing`). */
export async function getProfile(): Promise<ProfileRead | null> {
  try {
    return await apiGet<ProfileRead>('/api/profile')
  } catch (error) {
    if (error instanceof ApiError && error.code === 'profile-missing') return null
    throw error
  }
}

export const createProfile = (body: ProfileCreate) => apiPost<ProfileRead>('/api/profile', body)

export const updateProfile = (body: ProfileUpdate) => apiPatch<ProfileRead>('/api/profile', body)

export const addSkill = (body: CurrentSkillCreate) =>
  apiPost<ProfileRead>('/api/profile/skills', body)

export const updateSkill = (id: string, body: CurrentSkillUpdate) =>
  apiPatch<ProfileRead>(`/api/profile/skills/${id}`, body)

export const deleteSkill = (id: string) => apiDelete<ProfileRead>(`/api/profile/skills/${id}`)

export const addTarget = (body: TargetSkillCreate) =>
  apiPost<ProfileRead>('/api/profile/targets', body)

export const updateTarget = (id: string, body: TargetSkillUpdate) =>
  apiPatch<ProfileRead>(`/api/profile/targets/${id}`, body)

export const deleteTarget = (id: string) => apiDelete<ProfileRead>(`/api/profile/targets/${id}`)
