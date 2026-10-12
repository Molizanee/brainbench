# Wave 3 notes: tutor chat (tasks 9.1 to 9.5)

Branch `wave3-tutor`, from `main` at `36bccda`. Tasks 9.1 to 9.5 are checked in `tasks.md`. Commits:

| Commit | Content |
| --- | --- |
| `feat(tutor): tutor agent factory ... (9.1)` | `packages/agents/src/brainbench_agents/tutor/`, its tests, `strands-agents` dependency |
| `feat(tutor): streamed tutor replies ... (9.2)` | `app/models/tutor.py`, `app/services/tutor.py`, `app/services/tutor_tools.py`, config fields, service tests |
| `feat(tutor): tutor threads, messages SSE stream ... (9.3)` | `app/api/v1/tutor.py`, router registration, API, tool, model and Postgres tests |
| `feat(tutor): tutor drawer in the app shell ... (9.4)` | `src/api/tutor.ts`, `src/pages/tutor/`, `AppShell.tsx` mount, web tests, `markdown-to-jsx` |
| `feat(tutor): tutor-chat spec scenario tests ... (9.5)` | spec scenario block, `just tutor-integration` |
| `feat(tutor): keep the tutor header below the app bar ... (9.4)` | two UI defects found in the browser smoke |

No migration: `tutor_threads` and `tutor_messages` from `0001` already have every column used (`title`, `citations`, `related_topics`, `stopped`, `usage`). `infra/schema.rev` is unchanged.

## What I verified and how

- `just lint` (ruff, ruff format, mypy strict, oxlint, prettier, `tsc`): green.
- `just test`: 465 Python passed, 14 skipped (Postgres tests without `DATABASE_URL`); 97 web tests passed. `just test-py-eager`: 465 passed. The web suite ran three times with no flakes. The React `act(...)` warnings it prints come from MUI `FormControl` (see the wave-2 notes); the tutor tests add more of the same kind, nothing new.
- `just integration` against pgvector pg16 on `127.0.0.1:54309`: 14 passed (13 existing plus `test_tutor_postgres.py`). That test runs a reply on Postgres with real `bge-small` embeddings and pgvector retrieval. It checks that the topic's own chunk is in the prompt and the unrelated note is not, that the `search_chunks` tool searches every source, the `delta`/`citation`/`related_topic`/`done` order, a related topic scoring at least 0.8, and that JSONB citations and related topics are persisted with tz-aware timestamps.
- Spec scenarios (scripted fake Strands model, `brainbench_agents.tutor.testing.ScriptedModel`):
  - Grounded answer: citations come from the fixture chunks, with a PDF `#page=` link. The service and HTTP tests cover it.
  - Material gap: when retrieval is empty, the prompt carries the gap instruction. The answer keeps the "does not cover" preface, and a fabricated `[Paxos Made Simple, p. 3]` produces no citation.
  - Stop: the partial text is saved with `stopped`, and the stop call returns in under 5 s. A silent provider is force-cancelled after the grace period. A client disconnect also saves the reply as stopped.
  - Thread per context restored, new thread, merged topic resolved to its survivor.
  - No provider: the real `ensure_provider_for` with no account returns 400 `no-provider`, the message links `/providers`, and nothing is stored.
  - Limit event on throttle: `ProviderThrottledException.reset_at` reaches the event. The withdrawn question can be resent, and a throttle mid-reply keeps the partial text.
  - Depth preference and expert skills are in the system prompt; one reply at a time (409 `tutor-busy`); related topics exclude the context topic and excluded plan items.
  - Web: context from the route, context follows navigation without losing the other thread, thread menu and new thread, streamed text with citation chips and related-topic links, stop, the no-provider alert linking `/providers` with the draft kept, the limit alert with the reset time and Resend.
- Proxy streaming: `tests/api/tutor.test.ts` reads the reply through the real `server.ts` proxy and gets the first `delta` while the upstream still holds the rest. In the smoke, `curl -N` through Bun on :3009 got deltas at 0.25 s, 0.37 s and 0.49 s, then `citation`, `related_topic` and `done` at 21 s.
- Browser smoke (Orca, API uvicorn :8309, Bun :3009, worker, Postgres :54309):
  - Wizard password set through the proxy, then signed in through the login page in the browser.
  - Two notes ("Raft notes", "Consistent hashing notes") were ingested by the real worker (`python -m app.worker`, sandbox probe passed), giving 1 chunk each.
  - Three plan topics were seeded with real embeddings by a throwaway script. Curation is section 6 and is not in this branch.
  - **No provider account exists on this machine**, so the API ran from a throwaway script that is the real app with only `get_tutor_models` overridden by a local slow Strands `Model`. That model answers from the passages the real retrieval put in the prompt. Routing, retrieval, streaming, stop, persistence, related topics and the UI are the real code.
  - In the general context I saw streamed text, a "Raft notes, note" citation chip, and a "Raft leader election" related-topic link.
  - I stopped a reply mid-stream: the "Stopped" marker appeared 0.77 s after the click, and the API logged `stop (150 chars)`. After a reload, the partial message was there with the marker.
  - `/topics/<raft id>` showed the chip "Raft leader election" and an empty conversation. A question there created a separate thread (`GET ?context=topic:<id>` lists only it; general keeps its own).
  - A CRDT question got the "material does not cover this" reply with no chips.
  - For the topic step only, I added a placeholder `topics/:id` route to `routes.tsx` and reverted it before committing. Section 7 owns the real page.
  - The smoke found two defects, fixed in the last commit: on narrow screens the app bar covered the panel header, and the composer stayed red after a successful send.
  - Orca's pane stayed 834 px wide (`set viewport` did not resize it), so the wide layout with the persistent drawer beside the content was not seen in a browser. Only the temporary (narrow) drawer was.
  - Orca's `keypress Enter` and some `click` calls on the composer did not reach React, while `requestSubmit()` and later clicks did. I count this as a driver quirk: the button was the topmost element at its center.
- Teardown: servers stopped, Orca tab closed, `docker compose down -v`, `./data` removed, `git status` clean.

## Shared-file touches (exact)

- `apps/api/app/api/v1/__init__.py`: `tutor` was added to the `from app.api.v1 import ...` line. With it the line passes 100 characters, so `ruff format` rewrote it as a parenthesized one-name-per-line block (`auth, events, health, jobs, profile, progress, providers, search, sources, tutor`). I also appended `router.include_router(tutor.router)`. The merge needs the union of the names in that block.
- `apps/api/app/core/config.py`, fields only, after `embedding_batch_size`:
  ```python
  # Tutor chat (design D10).
  tutor_concurrency: int = 1
  tutor_retrieval_k: int = 8
  tutor_min_chunk_score: float = 0.6
  tutor_related_threshold: float = 0.8
  tutor_related_max: int = 5
  tutor_history_messages: int = 20
  tutor_stop_grace_s: float = 2.0
  tutor_keepalive_s: float = 15.0
  ```
- `packages/agents/pyproject.toml`: `dependencies = ["strands-agents==1.59.0"]` (the same pin as `packages/providers`). `uv.lock` was regenerated by `uv lock`.
- `apps/web/package.json`: `"markdown-to-jsx": "^9.10.3"` (resolved 9.10.3). `bun.lock` was updated by `bun add`. This is the one markdown dependency. It has zero dependencies and renders to React elements (no `dangerouslySetInnerHTML`). It is used with `disableParsingRawHTML: true`, so raw HTML in a reply is shown as text, and its built-in URL sanitizer drops `javascript:`/`data:` links. `optimizeForStreaming` hides half-received syntax while text streams.
- `apps/web/src/components/AppShell.tsx` (section 9 only): `SchoolIcon` toggle button "Open tutor"/"Close tutor" (`aria-pressed`) in the app bar before "Sign out", `useTutorOpen()` state, and `<TutorDrawer open wide onClose />` as the last child of the root flex box.
- `apps/web/tests/components/AppShell.test.tsx`: one test added (the toggle, the context, and that the open state is remembered), plus a localStorage reset after each test.
- `justfile`: recipe `tutor-integration` appended. `.github/workflows/ci.yml`: untouched; `test_tutor_postgres.py` runs in the existing `integration` job.
- Untouched: `routes.tsx` (no route added; the drawer lives in the shell), `invalidations.ts` (the tutor emits nothing on `/events`; its hook invalidates `['tutor', ...]` itself), `app/jobs/__init__.py` (the tutor is not a job), `schema.py`, migrations.

## Changes wanted in never-edit files

1. `apps/web/src/api/client.ts`: `streamMessage` has to call `fetch` itself, because a `POST` cannot use `EventSource`. `client.ts` does not export its `baseUrl` or its 401 handler, so `src/api/tutor.ts` works around both:
   - `configureTutorStream({ baseUrl })` exists for tests only.
   - On a 401 it fires `listThreads('general')`, so the shared client's redirect runs.

   Wanted: export a raw request helper, for example `export function apiFetch(path: string, init: RequestInit): Promise<Response>`, that applies `config.baseUrl` and calls `config.onUnauthorized` on 401. Then delete `configureTutorStream` and the workaround.
2. `apps/api/app/main.py` (optional): replies in flight when the API shuts down are cancelled with the event loop, and their partial text is not saved. If that matters, the lifespan can stop and await runs: `process_runs()` holds them, and each `TutorRun` has `stop("disconnect")` and `finished`. Nothing else needs it.

## Notes for the integrator (duplicates and naming)

- The task class is `tutoring`, the name already in `brainbench_providers.TASK_CLASSES`, the routing UI and the spec. The brief said `tutor`. No new task class was registered.
- `app/services/tutor_tools.py` reads the ORM directly where sections 6 and 7 ship services:
  - `resolve_topic`: follows `merged_into`, like the resolution in `services/sessions.py`.
  - `latest_revisions`: the newest `topic_revisions` row per topic.
  - `current_asset_formats`: current `assets` by format.
  - `topic_summary`, `plan_summary`: plan items with titles, status and prerequisites.

  Collapse these onto the topic and plan services once they exist. `plan_items.prerequisites` is read as a list of topic-id strings; anything else is ignored.
- `GET /api/tutor/sources/{id}/file` serves a stored PDF inline for citation links (`#page=n`). It belongs in the sources router; move it and change `citation_href` in `app/services/tutor.py`.
- Test helpers that will multiply:
  - `brainbench_agents.tutor.testing.ScriptedModel`, a scripted Strands `Model` with text, tool calls, pauses, raises and cancel. Sections 6 and 7 likely wrote their own.
  - A fourth copy of the per-test Postgres schema fixture (`pg_factory`) in `tests/integration/test_tutor_postgres.py`.
  - `FakeModels`/`hits` fixtures repeated in `tests/api/v1/test_tutor.py` and `tests/services/test_tutor.py`.
- Related topics use the reply's passage embedding (`embed_texts`), with citation labels stripped, against `topic_revisions.embedding` of the current plan's non-excluded items. Cosine is computed in Python, which is cheap for a plan's few dozen topics. Section 6 must keep writing `topic_revisions.embedding` with `embed_texts` (the ingest notes already ask for that), otherwise no topic is ever related.
- Link targets the panel assumes for section 7:
  - Related topics link to `/topics/:id`.
  - Asset shortcuts link to `/topics/:id#video` and `/topics/:id#figure`.
  - The panel takes its topic context from `/topics/:id...` and its plan context from `/plan`.

## Public surface (for section 10)

### HTTP (authenticated `router`, prefix `/api/tutor`)

| Method and path | Answer |
| --- | --- |
| `GET /threads?context=general\|plan\|topic:<id>` | `TutorThreadList {context: {type, topic_id, title, label, assets}, threads: TutorThreadRead[]}` (most recent first). 400 `invalid-context`, 404 `topic-not-found`. A merged topic resolves to its survivor. |
| `POST /threads?context=...` | 201 `TutorThreadRead {id, context_type, context_id, title, created_at, updated_at}` |
| `GET /threads/{id}/messages` | `TutorMessageRead[]` oldest first: `{id, thread_id, role, content, citations[], related_topics[], stopped, interrupted: stop\|disconnect\|limit\|error\|null, created_at}`. 404 `thread-not-found`. |
| `POST /threads/{id}/messages` `{content}` (1 to 8000 chars, trimmed) | `text/event-stream` (see below). Before anything is stored: 404, 400 `no-provider` (message names `/providers`), 409 `vault-locked`, 409 `tutor-busy` (one reply in flight per API process), 422 for a blank question. |
| `POST /threads/{id}/stop` | `TutorStopRead {stopped, message}`. It waits until the partial reply is saved (`stopped: true`, `interrupted: "stop"`). With nothing in flight: `stopped: false` and the last reply. |
| `GET /sources/{id}/file` | the stored PDF, `inline`; 404 `source-file-not-found` |

Stream: named SSE events, `data` is JSON.

- `delta {text}`
- `citation CitationRead {chunk_id, source_id, source_title, source_type, location, label, href, excerpt}`
- `related_topic {topic_id, title, score, assets}`
- `done {message, user_message_id}`
- `limit {reset_at, scope, message, reply, user_message_id}`
- `error {code: "tutor-failed", message, reply, user_message_id}`

Comment lines (`: reply`, `: keepalive` every 15 s) carry no event.

On `limit` or `error` with no text, the question is withdrawn (`reply` and `user_message_id` are null) so it can be resent. With partial text, both messages are kept and the reply is marked stopped.

### Behavior

- The question is retrieved first: the context topic's chunks first, then all sources, `k = 8`, score at least 0.6. The passages go into the prompt under their exact `[source-title, location]` labels.
- The agent can search again or call `get_topic`, `get_plan_summary` and `get_profile`.
- A citation event fires only when the reply contains a retrieved passage's label (lenient on case, spacing, quotes, and on `page N` for PDFs).
- `provider_usage` is recorded per model call with task class `tutoring` by the provider adapter. The message's `usage` stores tokens, model id, and `interrupted`.
- Stop sets the Strands cancel signal (the adapter closes the provider stream) and cancels the agent task after `tutor_stop_grace_s` (2 s).

### Packages and web

- `brainbench_agents.tutor`: `make_tutor_agent(model, context, tools, *, ledger, messages, callback_handler, retry_strategy)`, `TutorContext`, `TutorProfile`, `TutorTools`, `ChunkRef`, `CitationLedger`, `retrieve`, `build_system_prompt`, `build_turn_prompt`, `citation_label`; `brainbench_agents.tutor.testing.ScriptedModel`.
- Web:
  - `src/api/tutor.ts`: `tutorKeys` (`['tutor', 'threads', ctx]`, `['tutor', 'messages', id]`), `listThreads`, `createThread`, `listMessages`, `stopReply`, `streamMessage`, `readSse`, `parseSseBlock`, `contextParam`.
  - `src/pages/tutor/`: `TutorDrawer` (with `useTutorOpen`, remembered in `localStorage` key `brainbench.tutor.open`), `TutorPanel`, `TutorMessage`, `Composer`, `ThreadMenu`, `ProblemAlert`, `useTutorStream`, `useTutorContext`.
  - Accessible names the end-to-end smoke can use:
    - buttons "Open tutor", "Threads", "New thread", "Send", "Stop", "Close tutor";
    - chip "Tutor context: <label>";
    - log "Tutor conversation";
    - list "Sources", navigation "Related topics";
    - "Stopped" marker.
  - `tests/support/tutorMock.ts` follows the `authMock` pattern (`useFakes`, `useReal`, `ScriptedReply`). Any test that opens the tutor through the shell should import it.
