import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './api/client'

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        // Client errors (4xx) are answers, not outages: do not retry them.
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status < 500) && failures < 2,
      },
    },
  })
}
