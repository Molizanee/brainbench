# Wave 3 notes: visual content generation (tasks 7.1 to 7.8)

Branch `wave3-visual`, from `main` at `36bccda`. Tasks 7.1 to 7.8 are checked in `tasks.md`. One commit per task, all `feat(visual): ... (7.x)`:

| Commit | Content |
| --- | --- |
| 7.1 | `packages/agents/src/brainbench_agents/visual/`: output models, prompts, Manim contract checker, storyboard / figure_brief / manim_script / script_repair agents, `ScriptedModel` |
| 7.2 | `app/services/sandbox.py` (Docker sibling containers), unit tests with a fake client, Docker tests |
| 7.3 | `app/services/{storyboard,video,assets,budget}.py`, `app/jobs/video.py`, real render and real timeout tests |
| 7.4 | `brainbench_agents/visual/figure.py` (`make_figure_agent`, workspace tools, session runner, collector), `app/services/figure.py`, `app/jobs/figure.py` |
| 7.5 | budgets and claim gates (`app/services/budget.py`), generation requests with dedupe (`app/services/generation.py`) |
| 7.6 | `app/api/v1/{topics,assets}.py`, `app/models/{topics,assets}.py`, `app/services/topic_view.py` |
| 7.7 | web: `src/api/{topics,assets}.ts`, `src/pages/topics/*`, route `topics/:id`, invalidations |
| 7.8 | figure job outcomes through the runner; the rest of the 7.8 list ships with the task it tests (see below) |

No migration. Every column used exists in `0001` (`storyboards.figure_brief`, `assets.{previous_asset_id,is_current,feedback,script,files,provenance,duration_s}`, `usage_counters`, `settings.render_minutes_month/generation_jobs_day`, `jobs.dedupe_key`).

## What I verified, and how

macOS arm64, Docker Desktop, uv, Bun 1.3.13, Python 3.14.8, pgvector pg16 on `127.0.0.1:54307`.

- `just lint` (ruff, ruff format, mypy strict on 185 files, oxlint, prettier, `tsc --noEmit`): green.
- `just test-py`: 498 passed, 16 skipped (Postgres tests without `DATABASE_URL`); `just test-py-eager`: the same. Before this branch: 417.
- `just test-web`: 94 passed (82 before), run three times, no flakes.
- Docker tests (`apps/api/tests/integration/visual`, real `brainbench/manim` and `brainbench/anatomy`, pinned Anatomy skill installed into the data dir): 6 passed. They cover isolation (read-only `/work`, writable `/out`, read-only root, no route table under `--network none`), the wall-clock timeout killing and removing the container, a kill hook stopping a running container, the fixture script rendering to MP4 plus WebVTT with cues on the real section boundaries, a `time.sleep(120)` script failing the job with `error_reason = timeout` through the real `Runner` (attempt 1, no leftover container), and a figure built through the tools in the real anatomy sandbox (`node build.mjs`, `node -e` refused, `capture.mjs` producing a real PNG that the model receives as an image).
- Postgres (`DATABASE_URL=... pytest -m integration apps/api/tests/integration/visual/test_budget_postgres.py`): 2 passed. An exhausted render budget makes the claim gate skip the queued video while a `noop` is claimed, and raising the limit lets the video be claimed; two sequential requests for the same topic and format share one job.
- Smoke, against the dev Postgres, using the real API (`app.main:app` under uvicorn on :8307, with `providers.ensure_provider_for` stubbed because no provider account is connected), the real worker (`app.worker.main()`, including `prepare_sandboxes()` with `BRAINBENCH_DATA_HOST` and the probe), and `bun server.ts` on :3007. The only throwaway wiring was a wrapper that replaced `storyboard.VisualDeps` with one whose model factory returns `ScriptedModel`s. The storyboard cites the seeded chunk ids. The Manim script is a real 3-section lesson. The figure session writes a `build.mjs` that copies the skill's `examples/dial-indicator/dial-indicator.html`, runs it, then runs `capture.mjs`. Nothing from it is committed. Seeded data: a profile, a source with 3 chunks, a topic, and plan v1.
  - In Orca's browser I set the password and opened `/topics/<id>`. The page showed the header, the plan line, the budget, and "No visuals yet". The "whole plan" dialog's dry run reported "This queues 2 jobs for 1 topic of plan version 1 (Video and Figure)", and confirming it queued both jobs (`POST /api/plans/current/generate?dry_run=true`, then without the flag).
  - The worker claimed both jobs (one video and one figure at the same time, the separate per-type caps). The figure finished in 4.5 s (build 0.3 s, Chromium capture 3.8 s) and the video in 7 s (render 5.9 s, ffprobe 0.2 s). Both ended `ready`, the page refreshed through `/events`, and render minutes went to 0.17.
  - Video: `currentSrc` was `/api/assets/<id>/lesson.mp4`, and after `play()` `currentTime` advanced to 4.0 of 17 s with no media error. The captions track had 3 cues (0–5, 5–12, 12–17 s, the real section durations), and the active cue was the first scene's narration. A screenshot at 0:10 shows the majority scene with the caption "A candidate needs votes from a majority: three of five servers."
  - Figure: the iframe had `sandbox="allow-scripts"` and `referrerpolicy="no-referrer"`, and once it loaded the preview PNG was replaced (opacity 1). `iframe.contentDocument` was `null` (opaque origin). The response carried exactly `sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:`. A screenshot shows the dial-indicator figure rendered in the frame, with the full-screen link, the Storyboard, Build source and Session transcript tabs, and chunk-citation chips.
  - A `Range: bytes=0-99` request through the Bun proxy returned 206 `bytes 0-99/226447`.
  - Automation note: Orca's `click` on the "Generate visuals for the whole plan" button did not open the dialog (no request was sent), and Orca's screenshot did not show the open MUI dialog even though the DOM had it. A DOM `click()` through `orca eval` worked, and the dialog's text was read from the DOM. The web tests cover the dialog flow.
- Teardown: servers stopped, the Orca tab closed, `docker compose down -v`, scratch data removed, no `./data` in the worktree, and no `brainbench.sandbox` containers left.

## Shared-file touches (exact)

- `apps/api/app/api/v1/__init__.py`: the import line now also imports `assets` and `topics`. ruff wrapped it into a parenthesized multi-line import because it passed 100 characters: `from app.api.v1 import (assets, auth, events, health, jobs, profile, progress, providers, search, sources, topics)`. Two lines were appended: `router.include_router(topics.router)` and `router.include_router(assets.router)`. The `assets` router also serves `POST /api/plans/current/generate` (absolute paths, no prefix).
- `apps/api/app/jobs/__init__.py`: `from app.jobs import figure as figure` and `from app.jobs import video as video`.
- `apps/api/app/core/config.py` (fields only): `manim_image = "brainbench/manim"`, `anatomy_image = "brainbench/anatomy"`, `sandbox_pids_limit = 256`, `render_timeout_s = 900.0`, `render_cpus = 2.0`, `render_memory = "4g"`, `render_attempts = 3`, `figure_max_turns = 60`, `figure_wall_clock_s = 1800.0`, `figure_node_timeout_s = 180.0`, `figure_cpus = 2.0`, `figure_memory = "2g"`.
- `packages/agents/pyproject.toml`: `dependencies = ["pydantic>=2.12", "strands-agents==1.59.0"]` (it was empty), then `uv lock` (`uv.lock` +10 lines; regenerate at merge).
- `apps/web/src/routes.tsx`: added `export type PageRoute = { path; Page }` and `export const pageRoutes: PageRoute[] = [{ path: 'topics/:id', Page: lazy(() => import('./pages/topics/TopicPage')) }]`, plus one line in the shell's `children`: `...pageRoutes.map(({ path, Page }) => ({ path, element: <Page /> })),`. This is the additive extension for nested routes; `SectionRoute` and `sections` are unchanged. Section 6 can add `plan/...` routes to `pageRoutes`.
- `apps/web/src/events/invalidations.ts`: `import { topicKeys } from '../api/topics'` and `jobTypeInvalidations.video = jobTypeInvalidations.figure = (job) => [topicKeys.detail(String(job.payload.topic_id))]`. No new event type. Job progress reaches the page through `jobs.updated`.
- `justfile`: appended the `visual-integration` recipe. It builds the sandbox images, installs the pinned Anatomy skill into `$BRAINBENCH_DATA/skills/anatomy` with `skill_install.install_pinned_skill`, and runs `pytest -m integration apps/api/tests/integration/visual`. `.github/workflows/ci.yml` is untouched. The existing `integration` job already collects `tests/integration/visual`: there the Postgres tests run, and the Docker and skill tests skip because CI does not build the sandbox images or install the skill.
- `tasks.md`: the 7.1–7.8 checkboxes only.
- Not touched: `schema.py`, migrations, `infra/schema.rev`, `apps/api/pyproject.toml`, `apps/web/package.json`, `bun.lock`, CI.

## Changes needed in never-edit files

1. **Task classes.** The brief names `storyboard`, `figure_brief`, `manim_script`, `script_repair` and `figure`. The only registry is `brainbench_providers.TASK_CLASSES` in `packages/providers/src/brainbench_providers/__init__.py` (never-edit), which already ships `storyboarding`, `script-generation`, `script-repair` and `figure-building`. As a workaround, `app.services.storyboard.STEP_TASK_CLASSES` maps each agent step to an existing class: `storyboard -> storyboarding`, `figure_brief -> storyboarding`, `manim_script -> script-generation`, `script_repair -> script-repair`, `figure -> figure-building`. Routing, usage records and `ensure_provider_for` all use those names. If the integrator wants the brief's names, the change is to replace the tuple with `("curation", "storyboard", "figure_brief", "manim_script", "script_repair", "figure", "tutoring")` and update `STEP_TASK_CLASSES` to identity, then fix `packages/providers/tests/test_package.py` (it asserts 6 classes) and any stored `settings.provider_routing` keys.
2. **Host data dir.** `app/worker.py` resolves the host data path in `prepare_sandboxes()` and throws it away. `SandboxRunner` therefore resolves it again, lazily and once per process, with the same `resolve_host_data_dir(client, settings.brainbench_data, settings.brainbench_data_host)`. If the integrator wants a single resolution, `prepare_sandboxes()` could store its result, for example `app.services.sandbox._host_data_dirs[settings.brainbench_data] = host_data`.
3. **Chromium in the anatomy image.** The skill's `capture.mjs` and `drive.mjs` start Chromium without `--no-sandbox`, which fails as user `node` in a container ("Chrome exited before startup: 1"). Without touching `infra/Dockerfile.anatomy`, `run_node` uses the entry point `sh -c '<write /tmp/.bb/chromium wrapper adding --no-sandbox --disable-dev-shm-usage>; exec node "$@"'` with `CHROME_PATH=/tmp/.bb/chromium`, and the `/tmp` tmpfs is mounted `exec`. The alternative is a wrapper baked into `Dockerfile.anatomy`.

## Decisions worth knowing

- **Sandbox runs.** Every container gets `--network none`, `nano_cpus`, `mem_limit = memswap_limit`, `pids_limit`, `read_only`, a `/tmp` tmpfs (`rw,exec,nosuid,nodev,size=1g`), `cap_drop ALL`, `no-new-privileges`, and the label `brainbench.sandbox=<video|probe|figure>:<job id>`. A container is always removed afterwards. Only `jobs/<uuid>/work`, `jobs/<uuid>/out` and `skills/anatomy` may be mounted (`ALLOWED_MOUNTS`). Directories a sandbox writes are `chmod 0777` because the worker runs as root while the images run as uid 1000. The ffprobe run mounts `/out` read-only.
- **Video.** At most `render_attempts = 3` attempts in total. A script that breaks the `Lesson(Scene)` / `next_section` contract (`check_script`, via `ast`) counts as a failed attempt without a render and goes to `script_repair` with the contract problems. A render timeout fails at once with reason `timeout` (no repair), a killed sandbox fails with `killed`, and three failures give `render-failed` with the last error. Captions come from `sections/lesson.json` plus ffprobe on each clip and on `lesson.mp4`; a section starts where the previous one ended. Checkpoint keys are `storyboard`, `script`, `attempts`, `needs_repair`, `last_log` and `last_problems`, so a provider-limit deferral during a repair resumes with the repair. Render minutes are recorded and committed right after each sandbox run, so they also count for failed attempts.
- **Figure.** Statuses: `skipped` when the brief says `applicable: false` (the storyboard row with its brief is committed and no session starts); `failed` with reason `timeout`, `turn-budget` or `no-figure` and the transcript tail in `jobs.error`; `failed`/`skill-missing` when `skills/anatomy/SKILL.md` is absent. Limits use Strands `limits={"turns": 60}` and a `cancel_signal` set by a watchdog at 30 minutes or on job cancel; `run_node` sandboxes are killed by the job's kill hook. The collector picks `figure.html` if it is standalone, otherwise the largest standalone page (light variants last). Standalone means `<html` plus `<svg`/`<canvas`, with no external `src`/`href`/`url()`/`@import`. It picks `preview.png`, otherwise the newest valid PNG. When only the HTML exists, the job runs `capture.mjs` itself. Build source is `.mjs/.js/.cjs/.ts/.css/.json` files of at most 512 KB each; it is copied to `assets/.../source/` and up to 200 KB of it is inlined in `provenance.build_source`. On regeneration the previous build source is copied into the fresh workspace. Tools run sequentially (`SequentialToolExecutor`) and `run_node` calls are serialized.
- **Images to the model.** The provider transports send tool results as text only, so `ImageRelay` (a Strands `MessageAddedEvent` hook) moves PNGs out of `run_node`'s tool result into the same user message as image blocks (at most 2, each up to 1.5 MB), and replaces older images with a text note.
- **Budgets.** `render_minutes_month` counts sandbox wall-clock minutes per UTC month, for video renders and figure Node runs alike. `generation_jobs_day` counts jobs created per UTC day; it is incremented at enqueue, and deduplicated requests do not count. Enqueue raises 409 `budget-exhausted`, with the reset date, when the month's minutes are used up or the request would pass the daily cap. A whole-plan request is all-or-nothing. The claim gates for `video` and `figure` return False while the minutes are used up, or when today's count exceeds a cap that was lowered after the jobs were queued.
- **Requests.** `dedupe_key = "<format>:<topic_id>"`, checked first, so a duplicate is answered even when the budget is exhausted. New jobs need `ensure_provider_for` to pass for every task class the format uses and the topic to cite at least one chunk (400 `topic-without-chunks`). Merged topics resolve to the survivor. Payload: `{"topic_id", "format", "feedback"?, "previous_asset_id"?}`.
- **Whole plan.** `POST /api/plans/current/generate?dry_run=true` counts without creating anything (the session is rolled back); the web shows `job_count` and asks for confirmation before calling without the flag. It reads the latest `study_plans` row and its non-excluded `plan_items` through the ORM, in `position` order, with the formats from `profiles.preferred_formats` limited to video and figure (both when there is no profile).

## Duplicated helpers for the integrator to collapse

- `app.services.storyboard.resolve_topic` (merge chain, 32 hops) duplicates section 6's `app.services.topics.resolve_topic` and `SessionService._resolve_topic` in `sessions.py`. `TopicNotFound` (code `topic-not-found`) is also defined in `sessions.py`.
- `brainbench_agents.visual.testing.ScriptedModel` (scripted Strands `Model`: text and tool-call turns, structured output through tool calls) may overlap with fakes that sections 6 and 9 write; it could move to a shared `brainbench_agents.testing`.
- `apps/api/tests/integration/visual/test_budget_postgres.py` copies the `pg_factory` fixture from `tests/integration/jobs/conftest.py`; the integrator can hoist it to `tests/integration/conftest.py`.
- `apps/api/tests/visual_seed.py` (imported as `tests.visual_seed`) holds this section's seeding helpers and fake sandbox.

## Public surface (for section 10)

### HTTP (authenticated)

| Method and path | Body / query | Answer |
| --- | --- | --- |
| `GET /api/topics/{id}` | | `TopicRead {id, title, summary, status, stats{minutes_studied, session_count}, revision, history[{revision, title, created_by, created_at}] (newest first), assets[AssetRead] (newest first), plan_item{plan_version, position, week_start, week_span, estimate_minutes, relevance, rationale, excluded} \| null, jobs[JobRead] (latest video and figure job), budget}`; 307 `Location: /api/topics/{survivor}` for a merged topic; 404 `topic-not-found` |
| `POST /api/topics/{id}/generate` | `{formats: ["video" \| "figure", ...]}` (non-empty) | 202 `GenerateResponse {jobs[{topic_id, title, format, job_id, status, deduplicated}], budget}`; 400 `no-provider` / `topic-without-chunks`; 409 `budget-exhausted` / `vault-locked`; 404; 422 |
| `POST /api/assets/{id}/regenerate` | `{feedback}` (1–4000 characters, not blank) | 202 `GenerateResponse`; 404 `asset-not-found`; the same errors as generate |
| `POST /api/plans/current/generate` | `?dry_run=true` | 200 `PlanGenerateResponse {plan_version, formats, dry_run, job_count, jobs[GenerationJob + title], budget}`; 404 `plan-not-found`; 400 `nothing-to-generate`; 409 `budget-exhausted` |
| `GET /api/assets/{id}/{name}` | `name` ∈ `lesson.mp4`, `lesson.vtt`, `figure.html`, `preview.png` | the file (`FileResponse`, `Accept-Ranges: bytes`, 206 for `Range`), `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=3600`; `figure.html` adds `Content-Security-Policy: sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:` and `Referrer-Policy: no-referrer`; 404 `asset-file-not-found` / `asset-not-found`; 422 for other names |

`AssetRead {id, topic_id, format, is_current, created_at, job_id, previous_asset_id, feedback, duration_s, urls{video, captions, html, preview}, storyboard{id, title, scenes[{id, objective, narration, visual, chunk_ids, citations[{chunk_id, source_id, source_title, location, excerpt}]}], figure_brief, created_at}, script, build_source[{path, size, content}], transcript_tail, details{render_attempts?, failed_attempts?, missing_sections?, sections?, stop_reason?, turns?, timed_out?}}`. `BudgetRead {render_minutes_used, render_minutes_limit, render_minutes_left, render_resets_at, jobs_today, jobs_limit, jobs_left, jobs_reset_at}`.

### Job types

- `video` and `figure`, payload `{"topic_id", "format", "feedback"?, "previous_asset_id"?}`, `dedupe_key` `"<format>:<topic_id>"`. Each runs one at a time (`render_type_limits`) and has a budget claim gate.
- Failure reasons: `timeout`, `killed`, `render-failed`, `no-figure`, `turn-budget`, `skill-missing`, `topic-not-found`, `topic-without-chunks`, `no-provider`, `vault-locked`, plus the runner's own reasons. `skipped` means the figure does not apply; `progress` holds the rationale.
- Progress messages: "writing the storyboard", "writing the Manim script", "rendering (attempt n of 3)", "repairing the script (attempt n of 3)", "writing captions", "no video section for scenes: ...", "deriving the figure brief", "building the figure", "capturing the preview".
- Files: `$BRAINBENCH_DATA/assets/<topic>/<asset>/{lesson.mp4, lesson.vtt}` or `{figure.html, preview.png, source/...}`. Job workspaces `jobs/<id>/{work,out}` are removed when the handler ends.

### Event types

None new. `jobs.updated` for `video` or `figure` invalidates `['topics', topic_id]`.

### Web

- `src/api/topics.ts`: types mirroring the models, `VISUAL_FORMATS`, `topicKeys` (`all = ['topics']`, `detail(id) = ['topics', id]`), `getTopic`, `generateForTopic`. `src/api/assets.ts`: `PlanGenerateResponse`, `regenerateAsset`, `generateForPlan(dryRun)`.
- Route `/topics/:id` (`pages/topics/TopicPage.tsx`, not in the navigation; reached by link) with `TopicHeader`, `GeneratePanel`, `AssetSection` (viewer, provenance tabs, history, regenerate), `VideoViewer`, `FigureViewer`, `AssetDetails`, `StoryboardView`, `RegenerateDialog`, and `PlanGenerateDialog`, which is exported for section 6's plan page: `<PlanGenerateDialog open onClose onQueued />`. Nothing links to a topic page yet; section 6's plan page and section 8's progress list are the natural entry points.
- `tests/support/topicsMock.ts` mocks `src/api/topics` and `src/api/assets` at the module boundary (`topicsApi.useFakes(topic)` / `useReal()`, `makeTopic`, `makeAsset`, `planPreview`). Any test that renders `/topics/:id` should import it, together with `authMock` and `progressMock`.

### Agents package (`brainbench_agents.visual`)

`generate_storyboard(model, search, StoryboardRequest, *, allowed_chunk_ids)`, `generate_figure_brief`, `generate_manim_script`, `repair_manim_script`, the `make_*_agent` factories, `make_figure_agent(model, FigureWorkspace, *, brief, storyboard)`, `run_figure_session`, `collect_outputs`, `check_script`, `template_for`, and the models `Storyboard`, `FigureBrief`, `ManimScript`, `ChunkRef`, `Learner`.

## Known limits (by design)

- The search tool is scoped to the topic's chunks (`search_chunks(..., topic_id=...)`), which needs pgvector and the embedding model; the storyboard prompt also lists up to 8 of the topic's chunks directly, so a model can ground without searching.
- The intuition-first rule is enforced through the prompt only; no automated check reads the narration.
- No real provider was used. The first live run is the real check of the prompts.
