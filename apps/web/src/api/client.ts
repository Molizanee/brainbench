/**
 * The only module that calls `fetch`. Every API resource module (`src/api/<resource>.ts`)
 * builds on `apiGet`, `apiPost`, `apiPatch`, and `apiDelete`.
 */

export type FieldErrors = Record<string, string>

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fields?: FieldErrors

  constructor(status: number, code: string, message: string, fields?: FieldErrors) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fields = fields
  }
}

export type RequestOptions = {
  signal?: AbortSignal
  headers?: Record<string, string>
  /** Set to false where a 401 is an expected answer (wrong password on the login form). */
  redirectOn401?: boolean
}

type ClientConfig = {
  baseUrl: string
  onUnauthorized: (() => void) | null
}

const config: ClientConfig = { baseUrl: '', onUnauthorized: null }

/** `onUnauthorized` runs on every 401 (main.tsx clears the query cache and goes to /login). */
export function configureApiClient(options: Partial<ClientConfig>): void {
  Object.assign(config, options)
}

const SOURCE_LOCATIONS = new Set(['body', 'query', 'path', 'header', 'cookie'])

// FastAPI error bodies: {"detail": "text"}, {"detail": {"code", "message", "fields"?}},
// or a 422 {"detail": [{"loc": [...], "msg": "..."}]}.
export function toApiError(status: number, body: unknown, fallback: string): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return new ApiError(status, `http-${status}`, detail)
  if (Array.isArray(detail)) {
    const fields: FieldErrors = {}
    for (const item of detail as Array<{ loc?: unknown[]; msg?: string }>) {
      const loc = (item.loc ?? []).filter((part) => !SOURCE_LOCATIONS.has(String(part)))
      const name = loc.map(String).join('.') || '_'
      fields[name] ??= item.msg ?? 'Invalid value'
    }
    return new ApiError(status, 'validation-error', 'Some fields are invalid', fields)
  }
  if (detail && typeof detail === 'object') {
    const d = detail as { code?: string; message?: string; fields?: FieldErrors }
    return new ApiError(status, d.code ?? `http-${status}`, d.message ?? fallback, d.fields)
  }
  return new ApiError(status, `http-${status}`, fallback)
}

async function request<T>(
  method: string,
  path: string,
  body: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  let payload: BodyInit | undefined
  if (body instanceof FormData) {
    payload = body
  } else if (body !== undefined) {
    payload = JSON.stringify(body)
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(config.baseUrl + path, {
    method,
    headers,
    body: payload,
    credentials: 'same-origin',
    signal: options.signal,
  })

  if (response.status === 401 && options.redirectOn401 !== false) config.onUnauthorized?.()

  const contentType = response.headers.get('content-type') ?? ''
  const isJson = contentType.includes('application/json')
  if (!response.ok) {
    const errorBody: unknown = isJson ? await response.json().catch(() => null) : null
    throw toApiError(response.status, errorBody, response.statusText || 'Request failed')
  }
  if (response.status === 204) return undefined as T
  return (isJson ? await response.json() : await response.text()) as T
}

export const apiGet = <T>(path: string, options?: RequestOptions) =>
  request<T>('GET', path, undefined, options)

export const apiPost = <T>(path: string, body?: unknown, options?: RequestOptions) =>
  request<T>('POST', path, body, options)

export const apiPatch = <T>(path: string, body?: unknown, options?: RequestOptions) =>
  request<T>('PATCH', path, body, options)

export const apiPut = <T>(path: string, body?: unknown, options?: RequestOptions) =>
  request<T>('PUT', path, body, options)

export const apiDelete = <T = void>(path: string, options?: RequestOptions) =>
  request<T>('DELETE', path, undefined, options)
