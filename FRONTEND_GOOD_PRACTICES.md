# Frontend Good Practices

Conventions for `apps/web`: React 19, TypeScript, MUI (Material UI), React Router, TanStack Query, all on Bun. Bun is the package manager, bundler, dev server, production server, and test runner. There is no Node, Vite, webpack, npm, or Jest in this project.

## 1. Folder structure

```
apps/web/
  index.html             # single entry; imports src/main.tsx
  server.ts              # Bun.serve: serves the SPA, proxies /api, /events, websockets to FastAPI
  src/
    main.tsx             # createRoot, providers (theme, query client, router)
    theme.ts             # MUI theme: palette, typography, component defaults
    router.tsx           # route table (React Router, library mode)
    api/
      client.ts          # fetch wrapper: base path, JSON, errors, 401 handling
      profile.ts         # one module per API resource: typed functions + query keys
      sources.ts
      ...
    features/
      profile/           # one folder per screen or domain area
        ProfilePage.tsx
        SkillsForm.tsx
        useProfile.ts    # TanStack Query hooks for this feature
      sources/
      plan/
      topics/
      providers/
      progress/
      tutor/             # sidebar drawer, thread list, streamed messages, citations
    components/          # shared, feature-agnostic UI (AppShell, EmptyState, ConfirmDialog)
    lib/                 # pure helpers (formatting, time), no React
    hooks/               # shared hooks (useEvents for SSE)
  tests/                 # bun test; mirrors src/
  package.json
  bunfig.toml
  tsconfig.json
(root) package.json    # "workspaces": ["apps/web"]; bun.lock is the only web lockfile
```

Rules:

- A feature folder owns its page, its forms, and its query hooks. Code moves to `components/` or `hooks/` only when a second feature needs it.
- `api/` is the only place that calls `fetch`. Components and hooks import typed functions from `api/*`.
- `tests/` mirrors `src/`: `src/features/plan/PlanPage.tsx` is tested by `tests/features/plan/PlanPage.test.tsx`.

## 2. Bun as the toolchain

- Install: `bun install` at the repository root (Bun workspaces); the root `bun.lock` is committed. Docker uses `bun install --frozen-lockfile` with the repository root as build context.
- Dev: `bun --hot server.ts`. Bun's fullstack server bundles `index.html` and its imports on the fly with HMR; no separate bundler process.
- Production: `bun build --target=bun server.ts --outdir dist` then `bun dist/server.js`. The HTML import resolves to a prebuilt asset manifest; `Bun.serve` serves hashed assets with cache headers.
- Test: `bun test` with `@testing-library/react` and `happy-dom` registered in `bunfig.toml`. No Jest.
- Pin the Bun version in `package.json` `engines.bun` and `.bun-version`. Upgrade deliberately and run the full suite.
- Scripts in `package.json`: `dev`, `build`, `start`, `test`, `typecheck` (`bunx tsc --noEmit`), `lint`.

`server.ts` is small and boring. Explicit routes win over the `/*` wildcard, so the API proxy must be registered as routes; a proxy placed only in the `fetch` fallback never runs, because `/*` already matched the request (verified on Bun 1.4.3: `/api/auth/me` returned the SPA HTML).

```ts
import index from './index.html'

const API = process.env.API_URL ?? 'http://api:8000'

const proxy = (req: Request) => {
  const url = new URL(req.url)
  return fetch(new Request(API + url.pathname + url.search, req))
}

Bun.serve({
  port: 3000,
  idleTimeout: 255,
  routes: {
    '/api/*': proxy,
    '/events': proxy,
    '/*': index,
  },
  fetch: () => new Response('Not found', { status: 404 }),
})
```

Add a test that requests `/api/health` and `/events` through the running server and asserts the API's content type, not `text/html`.

The browser only ever talks to this server. Same origin means the API's `HttpOnly` session cookie is sent automatically and no CORS configuration exists anywhere.

## 3. Data fetching and server state

- TanStack Query owns all server state. No server data in `useState`, no global store.
- Query keys are defined next to the API functions (`profileKeys.detail()`, `sourceKeys.list()`), never inline strings.
- Mutations invalidate the queries they affect; optimistic updates only where the spec demands instant feedback (plan reorder).
- Live updates: one `useEvents()` hook opens a single `EventSource` to `/events` and invalidates query keys based on event type (`job.updated` → jobs and the entity it belongs to). Components never open their own `EventSource`.
- `api/client.ts` converts non-2xx responses into a typed `ApiError { status, code, fields? }`. A `401` clears the query cache and navigates to `/login`. Field-level validation errors from FastAPI (`422`) map to form field errors.

## 4. Components and MUI

- Use MUI components for everything the library covers: `TextField`, `Select`, `Autocomplete`, `DataGrid` or `Table`, `Dialog`, `Alert`, `Snackbar`, `Stepper`, `Tabs`. Do not hand-roll a modal, dropdown, or table.
- Theme once in `theme.ts`; style with the `sx` prop or `styled()`. No global CSS files beyond resets, no inline `style={}`.
- Page layout through `AppShell` (MUI `AppBar` + `Drawer` + `Container`). Pages render content, not chrome.
- Forms: `react-hook-form` with MUI inputs via `Controller`, Zod schemas for client-side shape validation only; business validation stays in the API and its errors are rendered per field.
- Components are function components with hooks. Keep them under roughly 200 lines; split by responsibility (data hook, presentational component, dialog).
- Loading and error states are explicit: `Skeleton` while loading, `Alert` with retry on error, `EmptyState` when the list is empty.
- Accessibility is not optional: every icon button has `aria-label`, dialogs have titles, forms have labels, color is never the only signal.

## 5. Routing

- React Router in library mode (`createBrowserRouter`). Routes live in `router.tsx`; pages are lazy-loaded with `React.lazy` so the initial bundle stays small.
- URL is state for anything shareable: selected plan version, topic id, filters. Dialog open state is local.
- A route guard component queries `/api/auth/me`; unauthenticated renders `LoginPage`.

## 6. Browser-heavy widgets and embedded content

- The provider Connect flow is a plain MUI dialog driven by the login endpoints (open-URL button, waiting state, paste field, device code); it holds a `login_id` and polls or posts, nothing more. No terminal emulator, no WebSocket.
- Generated figures are agent-produced standalone HTML. Embed them only with `<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" src={figureUrl}>`; never `allow-same-origin`, never `srcdoc` with app data, never inline the HTML into the React tree. Show the PNG preview until the frame loads and offer a full-screen link to the same URL. Do not add a 3D library; the figure carries its own runtime.
- Video uses the native `<video controls>` with a `<track kind="captions">`; no player library.
- The tutor drawer is part of `AppShell`, mounted once, and reads its context (`topic:<id>`, `plan`, `general`) from the current route. It consumes an SSE stream per message (`delta`, `citation`, `related_topic`, `done`, `error`, `limit`) through a single hook (`useTutorStream`) that appends deltas to local state and persists nothing itself; thread history comes from queries. Render replies as markdown with a sanitizer; citations are chips linking to the source location. Stop aborts the fetch and calls the stop endpoint.

## 7. Types

- `strict: true`. No `any` outside `api/client.ts` boundary parsing.
- API response types are declared in `api/*.ts` and mirror the FastAPI response models by name (`ProfileRead`, `SourceRead`). When the backend changes a model, the matching type changes in the same commit.
- Enums from the specs (`novice | working | proficient | expert`, job statuses) are string-literal unions exported from `api/types.ts` and reused by forms and tables.

## 8. Testing

- `bun test` with Testing Library. Render pages inside a test harness that provides the theme, a fresh `QueryClient`, and a memory router.
- Mock the `api/*` modules at the module boundary with `mock.module`; never mock `fetch` globally.
- Test behavior the specs describe: a validation error appears on the right field, a prerequisite violation blocks a reorder, a `job.updated` event refreshes the topic card. Do not test MUI internals or snapshot whole pages.
- Browser-heavy widgets get a smoke test that they mount and unmount without leaking; their visual behavior is verified manually in the browser.

## 9. Tooling and CI

- `bunx tsc --noEmit`, `bun test`, and the linter (`oxlint` or `eslint` with the React and hooks plugins; pick one and keep it) run in CI.
- Formatting with `prettier` or `oxfmt`; one config, enforced in CI.
- `Dockerfile.web`: `oven/bun` base, `bun install --frozen-lockfile`, `bun build --target=bun server.ts`, run `bun dist/server.js`. Same image locally and deployed; only `API_URL` differs.

## 10. Adding a screen

1. Add the typed API functions and query keys in `src/api/<resource>.ts`.
2. Create `src/features/<area>/` with the page, its forms or dialogs, and `use<Resource>.ts` hooks.
3. Register the route in `router.tsx` with `React.lazy`.
4. Wire live updates by mapping the relevant event type in `useEvents()` if the screen shows job-driven data.
5. Add tests mirroring each file; run the screen in the browser against the compose stack.
