import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getProfile, profileKeys, type ProfileRead } from '../../api/profile'

export function useProfileQuery() {
  return useQuery({ queryKey: profileKeys.detail(), queryFn: getProfile })
}

/** Every profile mutation answers with the whole profile; store it as the cached detail. */
export function useProfileMutation<V>(mutationFn: (variables: V) => Promise<ProfileRead>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: (profile) => queryClient.setQueryData(profileKeys.detail(), profile),
  })
}
