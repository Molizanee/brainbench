import { GlobalRegistrator } from '@happy-dom/global-registrator'

// happy-dom replaces the networking globals; keep Bun's so server and client tests talk to
// real Bun.serve instances.
const { fetch, Request, Response, Headers, FormData, AbortController, AbortSignal } = globalThis

GlobalRegistrator.register({ url: 'http://localhost:3000/' })

Object.assign(globalThis, {
  fetch,
  Request,
  Response,
  Headers,
  FormData,
  AbortController,
  AbortSignal,
})
