import index from './index.html'

const DEFAULT_API_URL = 'http://api:8000'

export type ServerOptions = {
  port?: number
  apiUrl?: string
}

/** Forward the request to FastAPI unchanged; redirects and streams (SSE) pass through. */
export function proxyTo(apiUrl: string) {
  return async (req: Request): Promise<Response> => {
    const url = new URL(req.url)
    const headers = new Headers(req.headers)
    headers.delete('host')
    try {
      return await fetch(apiUrl + url.pathname + url.search, {
        method: req.method,
        headers,
        body: req.body,
        redirect: 'manual',
        signal: req.signal,
      })
    } catch {
      return Response.json(
        { detail: { code: 'api-unavailable', message: 'API is not reachable' } },
        { status: 502 },
      )
    }
  }
}

/**
 * Explicit routes win over the `/*` wildcard, so the API proxy must be registered as routes;
 * a proxy placed only in the `fetch` fallback would never run.
 */
export function startServer(options: ServerOptions = {}) {
  const proxy = proxyTo(options.apiUrl ?? process.env.API_URL ?? DEFAULT_API_URL)
  return Bun.serve({
    port: options.port ?? Number(process.env.PORT ?? 3000),
    idleTimeout: 255,
    routes: {
      '/api/*': proxy,
      '/events': proxy,
      '/*': index,
    },
    fetch: () => new Response('Not found', { status: 404 }),
  })
}

/** Poll `/api/health` through our own proxy, then announce the URL once. */
export async function announceWhenReady(
  serverUrl: URL,
  publicUrl: string,
  log: (line: string) => void = console.log,
  intervalMs = 1000,
): Promise<void> {
  const health = new URL('/api/health', serverUrl)
  for (;;) {
    try {
      const response = await fetch(health)
      if (response.ok) {
        log(`BrainBench ready at ${publicUrl}`)
        return
      }
    } catch {
      // API not up yet.
    }
    await Bun.sleep(intervalMs)
  }
}

if (import.meta.main) {
  const server = startServer()
  const publicUrl =
    process.env.BRAINBENCH_PUBLIC_URL || `http://localhost:${process.env.BRAINBENCH_PORT ?? 3000}`
  void announceWhenReady(server.url, publicUrl)
}
