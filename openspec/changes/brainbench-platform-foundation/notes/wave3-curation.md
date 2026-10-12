# Wave 3 notes: curriculum curation (tasks 6.1 to 6.8)

Branch `wave3-curation`, from `main` at `36bccda`. Tasks 6.1 to 6.8 are checked in `tasks.md`. One commit per task, all prefixed `feat(curation):`, plus this notes file.

| Commit | Content |
| --- | --- |
| 6.1 | `packages/agents/src/brainbench_agents/curation/` (models, prompts, agents, tools), `brainbench_agents/testing.py` (scripted Strands `Model`), task classes registered |
| 6.2 | `app/services/topics.py`, `app/services/curation.py` (preconditions, runs, routing, windows, dedupe, reconcile), `app/jobs/curation.py`, config fields |
| 6.3 | scoring step, `apply_score`, `profile_snapshot` |
| 6.4 | prerequisites step, `break_cycles` in `app/services/planner.py` |
| 6.5 | the rest of `planner.py`, `app/services/plans.py` (writing versions), the planning step |
| 6.6 | `app/models/{curation,plans}.py`, plan read/edit service, `app/api/v1/{curation,plans}.py` |
| 6.7 | `apps/web/src/api/{curation,plans}.ts`, `src/pages/plan/` |
| 6.8 | scenario tests, synthetic-graph tests, `tests/integration/test_curation_postgres.py`, `just curation-integration` |

## What I verified, and how

macOS arm64, Python 3.14.8 (the repo runs the 3.14 fallback with `__lazy_modules__`), uv, Bun 1.3.13, Postgres `pgvector/pgvector:pg16` from `infra/compose.dev.yml` on `127.0.0.1:54306`.

- `just lint` (ruff, ruff format, mypy strict, oxlint, prettier, `tsc --noEmit`): green.
- `just test`: Python 521 passed, 16 skipped (the Postgres tests without `DATABASE_URL`). Web: 96 passed. `just test-py-eager`: 521 passed.
- `DATABASE_URL=...54306 just integration`: 16 passed (the 13 existing tests plus my 3).
- Each import order of `app.services.{curation,plans,providers}`, `app.jobs`, `app.jobs.curation`, `app.main` and `app.worker` works (see "Import cycle" below).
- Unit tests use the scripted model (`brainbench_agents.testing.ScriptedModel`) driven by `tests/support/curation.py: Script`, which answers each step from its prompt, and fake embeddings that put one concept on one axis. They cover:
  - planner ordering on synthetic graphs (25 random graphs, cycles included);
  - cycle breaking;
  - weekly packing within the budget and oversized topics spanning weeks;
  - a reorder before a prerequisite being rejected, with the prerequisite named;
  - stale detection for profile and source changes;
  - re-curation after a logged session keeping the same `topic_id` with its sessions, assets and threads attached;
  - a summary change creating a revision while old plan versions render the old text;
  - merge forwarding history, with `resolve_topic(loser)` returning the survivor (service level) and `GET /api/plans/current` after a merge (router level);
  - deferral on a provider limit resuming after the last completed window.
- The Postgres integration test runs the real worker path: `start_run` enqueues, a `Runner` claims and runs the handler, the checkpoint is stored in `jobs.checkpoint` (JSONB), and revision embeddings are stored in `vector(384)`. A provider limit during scoring requeues the job with its attempt count unchanged and the checkpoint holding the reconciled topics. The resumed job does not repeat extraction. All five plan edits also run on Postgres.
- Smoke on my ports (Postgres 54306, uvicorn on 8306, `bun --hot server.ts` on 3006 proxying to it):
  - **Setup through the API (curl via the Bun proxy):** I set the password and created a profile with 4 h/week, the current skill Python at expert, and two targets: Distributed consensus (high) and Storage engines (low).
  - **Rejections before seeding:** `POST /api/curation/runs` returned 400 `targets-required` before the targets existed and 400 `ready-sources-required` before the sources were ready.
  - **Real ingest:** I posted two notes with four "Title: summary" lines each. A throwaway script (not committed) ran the real `ingest` handler on both jobs with the real `bge-small` model, and both sources became `ready`.
  - **No provider:** with no connected account, `POST /api/curation/runs` returned 400 `no-provider` naming `topic_extraction`. No provider account is connected on this machine, so the same throwaway script ran curation through the real worker path (`start_run` with `ensure_provider_for` stubbed, then `Runner.tick()`). It used a `ScriptedModel` whose responder answers each step from its prompt, together with the real embeddings.
  - **First run:** the job finished `ready` on attempt 1. The run log shows 8 candidates, 8 new topics, 6 kept and 2 excluded below 0.4, `cycle broken: dropped 'Raft terms requires Log compaction' (confidence 0.2)`, 3 prerequisite relations, and plan version 1. With real bge embeddings, "Leader election" and "Leader election in Raft" stayed below the 0.85 dedupe threshold, so the merge step had no group to merge.
  - **Plan page in Orca's browser (`/plan`):** it showed weekly blocks, including "Weeks 2–3" with "Exceeds weekly budget" for a 6 h topic against a 4 h budget, and topic cards with status, estimate, relevance, served target, rationale, "After:" prerequisites and sources.
  - **Edits on the plan page:**
    1. Moving "Leader election" above "Raft terms" returned 400 and showed "'Leader election' must come after its prerequisite 'Raft terms'." inline on that card. The plan stayed at version 1.
    2. Move Paxos down → version 2.
    3. Remove "Log compaction" → version 3. It moved to the excluded list.
    4. Merge "Leader election in Raft" into "Leader election" through the dialog → version 4. The survivor now cites chunks from both sources.
    5. Edit Paxos's title to "Single-decree Paxos" through the dialog → version 5 with revision 2. `GET /api/plans/4` still shows "Paxos", revision 1.
    6. Include "LSM trees" → version 6, marked manually included.

    `GET /api/plans` listed versions 1 to 6.
  - **Stale plan:** I changed the weekly budget through `PATCH /api/profile` and reloaded. The page showed "This plan is stale. Your profile changed (version 5; this plan used version 4)." with "Re-run curation" (screenshot checked).
  - **Second run:** plan version 7 has 7 topics, 0 new and 0 revised, so every `topic_id` was reused. "Single-decree Paxos" kept the title I wrote, and the merged topic's candidate folded into the survivor through its old title. The plan is no longer stale.
  - **Teardown:** I closed the Orca tab, stopped both servers, ran `docker compose down -v`, and removed `./data`.
  - **Caution for the integrator:** I stopped the web server with `pkill -f "bun --hot server.ts"`, which would also have stopped a `bun --hot` dev server another wave 3 worktree was running at that moment. If the visual or tutor agent reports its web server dying around 10:30 local time on 2026-10-12, this is the cause.

## Shared-file touches (exact)

- `apps/api/app/api/v1/__init__.py`: the import line became the parenthesized `from app.api.v1 import (auth, curation, events, health, jobs, plans, profile, progress, providers, search, sources)`. Ruff splits it because one line would exceed 100 characters. Two lines were appended at the end: `router.include_router(curation.router)` and `router.include_router(plans.router)`.
- `apps/api/app/jobs/__init__.py`: `from app.jobs import curation as curation`.
- `apps/api/app/core/config.py` (fields only, after `embedding_batch_size`):
  ```python
  # Curriculum curation (design D6).
  curation_threshold: float = 0.4
  curation_window_chars: int = 12_000
  curation_merge_similarity: float = 0.85
  curation_scoring_batch: int = 20
  curation_default_estimate_minutes: int = 60
  ```
- `packages/agents/pyproject.toml`: `dependencies = ["pydantic>=2.12", "strands-agents==1.59.0"]` (both already in the lock through `brainbench-providers`). `uv.lock` was regenerated offline (10 lines, `brainbench-agents` dependencies only).
- Task classes, registered where routing declares them (the brief's grant). The section 3 tests' expected count changed with them:
  - `packages/providers/src/brainbench_providers/__init__.py`: the comment and four entries `topic_extraction`, `topic_merge`, `scoring`, `prerequisites` were appended to `TASK_CLASSES`.
  - `packages/providers/tests/test_package.py`: `== 6` became `== 10`.
  - `apps/api/app/models/providers.py`: the same four values were added to `TaskClass`, which is now one value per line.
  - `apps/web/src/api/providers.ts`: the same four were added to `TaskClass`.
  - `apps/web/src/pages/providers/RoutingCard.tsx`: four `TASK_LABELS` entries (`'Curation: topic extraction'` and so on).
  - `apps/web/tests/support/providersMock.ts`: the four in `task_classes` and `effective`.
- `apps/web/src/routes.tsx`: `import EventNoteIcon from '@mui/icons-material/EventNote'` and one `sections` entry `{ path: 'plan', label: 'Plan', icon: EventNoteIcon, Page: lazy(() => import('./pages/plan/PlanPage')) }`. No nested route.
- `apps/web/src/events/invalidations.ts`: imports of `curationKeys` and `planKeys`, and `jobTypeInvalidations = { curation: () => [planKeys.all, curationKeys.all] }`. Other sections add their entries to the same object literal.
- `justfile`: recipe `curation-integration`. The CI file is not touched; my Postgres tests run in the existing `integration` job.
- `tasks.md`: the 6.1 to 6.8 checkboxes only.

## Migration

None. Every column used exists in `0001`.

## Blocked changes in never-edit files, and bugs found there

1. **`app/services/profile.py` (section 4): `POST /api/profile` with inline `current_skills` or `target_skills` returns 500 on Postgres.** The `current_skills` row is flushed before its `profiles` row (`ForeignKeyViolationError`, `fk_current_skills_profile_id_profiles`), because the ORM has no relationship to order the inserts. SQLite does not enforce the key, so the unit tests miss it. Fix it in `ProfileService.create` by calling `await self.session.flush()` after `session.add(profile)` and before adding the skills. The smoke worked around it by creating the profile first, then the skill and the targets.
2. **Import cycle (not blocking, worked around).** `app/services/providers.py` imports `app.jobs.errors`, which runs `app/jobs/__init__.py` and imports every handler. A handler that imports a service that imports `providers` therefore re-enters a partially initialized module. My modules use module imports (`from app.services import curation as service`, `plans as plan_service`, `providers`) and look names up at call time. Sections 7 and 9 will hit the same cycle. The structural fix is for `providers.py` to import `ProviderLimitReached` from a module that does not load the handlers, for example by moving `app/jobs/errors.py` out of the `app.jobs` package, or by having `app/jobs/__init__.py` not import handlers and letting `worker.py` do it.

## Duplicated helpers the integrator should collapse

- `app/services/sessions.py: SessionService._resolve_topic` (section 8) duplicates `app/services/topics.py: resolve_topic`. Section 7's private `merged_into` hop for `GET /api/topics/{id}` should also call `resolve_topic`, with `TopicNotFound` (404 `topic-not-found`).
- `app/services/progress.py: normalize_name` (`strip().casefold()`) and `app/services/profile.py: normalize_name` (whitespace collapsed). I use the profile one.
- `apps/web/src/pages/plan/format.ts` re-exports `formatMinutes` and `statusLabels` from `pages/progress/format.ts`. Move them to `src/lib` if a third section needs them.
- `tests/support/curation.py` holds fixtures as plain functions imported as `tests.support.curation` (`apps/api` is on `sys.path`), so no shared `conftest.py` is touched. `tests/integration/test_curation_postgres.py` has its own `pg_factory`, like the ingest test. Hoist both into `tests/integration/conftest.py` when convenient.

## Design decisions to know

- **Structured output.** `run_structured` calls `agent.invoke_async(prompt, structured_output_model=M)`. That is the current Strands form of `agent.structured_output`, which Strands 1.59 deprecates. It lets the agent call its tools first, and the `AfterInvocationEvent` hook then gets an `AgentResult`; `structured_output_async` passes `result=None` and skips tools. Each call clears the agent's messages, so one agent per step serves every window or batch.
- **Usage hook.** `UsageRecorder` reports each invocation's token difference to a sink. The job appends `{"step", "message": "usage", "input_tokens", "output_tokens", "total_tokens"}` entries to the run's step log. Provider-level `provider_usage` rows come from the adapter, tagged with the step's task class.
- **Routing.** Each step uses its own task class. When a step has no explicit route and `curation` has one, the step uses the `curation` route, and `model.update_config(task_class=step)` keeps usage tagged per step. `POST /api/curation/runs` calls `ensure_provider_for` on each step's effective class.
- **Run row.** `start_run` creates the `curation_runs` row at enqueue time, with the profile version, the ready sources and the threshold. The handler re-snapshots them on its first attempt and records them on the run; a resumed attempt reuses the snapshot. A duplicate request returns the active run (`dedupe_key="curation"`).
- **Checkpoint keys.** `inputs`, `windows` (`"<source_id>:<n>"` to candidates), `candidates`, `topics`, `scores`, `edges`, `log`, `models`. Reconciled topics are committed before the checkpoint is saved, so a deferral keeps their ids, and a retry that repeats reconcile matches the same topics.
- **Reconcile.**
  - A title match uses any revision's normalized title, with current revisions first. A topic the engineer renamed is still found by its old title, and a merged topic resolves to its survivor.
  - A case-only or punctuation-only title change is not a change.
  - A run never writes over a revision with `created_by="edit"`, so engineer edits survive re-curation.
  - Edit revisions get an embedding, computed with `embed_texts` in the API process.
- **Scoring.**
  - `already_held` caps relevance at 0.15 and puts "You already hold this skill." in the rationale.
  - A topic serving no target says "Unrelated to your target skills.".
  - `serves_targets` stores target ids, which section 8 matches by id.
  - A topic the model did not score gets relevance 0 and that reason.
- **Planner.**
  - A prerequisite inherits the best priority of the topics that need it, so the foundation of a high-priority topic is not queued behind medium-priority work.
  - Mastered topics go last unless an unmastered topic needs them.
  - `order_topics` also breaks any cycle left in its input, so the order is always complete.
- **Edits.** Every edit requires `version` to be the current version (409 `plan-outdated` otherwise) and repacks the weeks with the current budget.
  - **Reorder** takes the full new order.
  - **Remove** moves the topic to the excluded list with "Removed from the plan by you." and drops edges to it.
  - **Merge** works for included and excluded topics. The survivor takes the loser's place when only the loser was included, the edges are re-pointed, and the order is fixed minimally (`stable_order`).
  - **Include** inserts after the topic's prerequisites, using the planner's tie-break, and sets `manually_included`.

  Edits copy `profile_version` and `source_ids` forward, so an edited plan stays stale until a run.
- **A run starts over.** A curation run rebuilds the plan from scratch: manual removals, includes and order are not carried over (the smoke's run 2 brought back "Log compaction" and excluded "LSM trees" again). The spec does not ask for carry-over; carrying `manually_included` forward would be a later change.
- `GET /api/plans/{version}` items carry `merged_into` when the topic was merged after that version was written. The web links those cards to the survivor.

## Public surface (for section 10)

### HTTP (authenticated)

| Method and path | Body | Answer |
| --- | --- | --- |
| `POST /api/curation/runs` | `{threshold?: 0..1}` | 202 `CurationRunRead`. 400 `targets-required`, `ready-sources-required`, `no-provider`; 409 `vault-locked`. A run already active is returned as is. |
| `GET /api/curation/runs?limit=20` | | `CurationRunRead[]`, newest first: `id, job_id, status, progress, error, profile_version, source_ids, threshold, models{step: {provider, model}}, step_log[], plan_version, created_at, finished_at` |
| `GET /api/plans` | | `PlanSummary[]` (`version, created_at, created_by`), newest first |
| `GET /api/plans/current` | | `PlanRead`; 404 `plan-missing` |
| `GET /api/plans/{version}` | | `PlanRead`; 404 `plan-not-found` |
| `POST /api/plans/{version}/edits` | one of `{op:"reorder", order:[topic_id]}`, `{op:"remove", topic_id}`, `{op:"merge", topic_id, into_topic_id}`, `{op:"edit", topic_id, title?, summary?}`, `{op:"include", topic_id}` | 201 `PlanRead` (new version). 409 `plan-outdated`; 400 `prerequisite-violation` (message names each violated prerequisite), `invalid-edit`; 404 `topic-not-in-plan`; 409 `invalid-merge`; 422 validation |

`PlanRead`: `version, created_at, created_by, curation_run_id, profile_version, source_ids, current_version, is_current, stale, stale_reasons[], weekly_budget_minutes, run{id, threshold, models, finished_at}, weeks[{week_start, week_span, exceeds_weekly_budget, total_minutes, topic_ids}], items[], excluded[]`.

`PlanItemRead`: `topic_id, topic_revision_id, revision, title, summary, position, week_start, week_span, exceeds_weekly_budget, prerequisites[{topic_id, title}], manually_included, excluded, relevance, rationale, serves_targets[{id, name}], estimate_minutes, status, merged_into, citations[{chunk_id, source_id, source_title, source_type, location, url, excerpt}]`. A URL source's `url` is its `uri` plus the text fragment; a Git source's is the repository; PDFs and notes have none.

### Job and events

- Job type `curation`, payload `{"run_id"}`, `dedupe_key="curation"`. Deferrable: it resumes from its checkpoint.
- No new SSE type. `jobs.updated` for a curation job invalidates `['plans']` and `['curation']` through `jobTypeInvalidations.curation`. The run panel also polls every 5 s while a run is active.

### Services

- `app/services/topics.py`: `resolve_topic`, `merge_topics`, `current_revision`, `topic_history`, `create_topic`, `add_revision`, `cite_chunks`, `normalize_title`, `TopicNotFound` (404 `topic-not-found`), `InvalidMerge`.
- `app/services/curation.py`: `start_run`, `check_preconditions`, `ready_source_ids`, `routed_model`, `effective_task_class`, `ensure_curation_providers`, `reconcile`, `apply_score`, `profile_snapshot`, `CurationService`.
- `app/services/planner.py` (pure): `Edge`, `PlanTopic`, `break_cycles`, `order_topics`, `pack_weeks`, `plan_topics`, `check_order`, `insert_position`, `stable_order`.
- `app/services/plans.py`: `write_plan`, `write_run_plan`, `read_plan`, `stale_reasons`, `PlanService(session).versions/current/get/edit`.

### Package

`brainbench_agents.curation`: output models (`TopicExtraction`, `TopicMerge`, `TopicScoring`, `Prerequisites`, ...), prompt builders, `topic_extraction_agent`, `topic_merge_agent`, `scoring_agent`, `prerequisites_agent`, `run_structured`, `UsageRecorder`, `make_search_chunks`, `make_get_profile`, `make_get_sessions`, `CURATION_TASK_CLASSES`. `brainbench_agents.testing`: `ScriptedModel`, `Text`, `ToolCall`, `Structured`, `ModelCall` (section 9 can drive its tutor agent with it).

### Web

- `src/api/plans.ts`: types, `planKeys` (`all = ['plans']`, `list()`, `current()`, `version(n)`), `getCurrentPlan` (`null` on `plan-missing`), `getPlan`, `listPlans`, `editPlan`.
- `src/api/curation.ts`: types, `curationKeys` (`all = ['curation']`, `runs()`), `startCurationRun`, `listCurationRuns`, `isRunActive`.
- Route `/plan` (nav label "Plan"; `?version=N` shows an older, read-only version). Topic cards link to `/topics/:id`, which section 7 provides.
- `tests/support/plansMock.ts`: `plansApi.useFakes(plan, runs)` and `useReal()`, plus `samplePlan`, `makeItem` and `makeRun`. Any test that renders routes and reaches `/plan` should import it.
