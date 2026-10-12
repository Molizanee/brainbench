# Wave 3 integration notes

Branches `wave3-curation`, `wave3-visual`, `wave3-tutor` merged into `main` in that order, then one integration commit. Each branch was lint- and test-green before merging; after merging and the changes below, `just lint`, `just test` (649 Python, 123 web) and `just integration` are green.

## Changes made at merge

- **Handler registration no longer runs on package import.** `app/jobs/__init__.py` is a docstring; `app/jobs/handlers.py` imports every handler module (one line per job type; sections add theirs there). `app/worker.py` imports `app.jobs.handlers`; `apps/api/tests/conftest.py` imports it for the whole test session, so `HANDLERS["<type>"]` keeps working in tests. Reason: `from app.jobs.errors import ...` in any service used to execute every handler import, which pulled every service into the import of `app.services.providers` (the cycle sections 6 and 7 worked around with call-time module lookups). Those workaround comments are removed; the module-style imports they introduced are harmless and stay.
- **`ProfileService.create` flushes the profile before its skills and targets** (`app/services/profile.py`). On Postgres the children were flushed first and violated `fk_current_skills_profile_id_profiles`; SQLite does not enforce the key, so the unit suite had not caught it. Found by the curation smoke.
- **One `resolve_topic` / `TopicNotFound`.** `app/services/topics.py` is the only definition (merge chain up to 32 hops, cycle reads as missing, code `topic-not-found`). The copies in `sessions.py` (`SessionService._resolve_topic`), `storyboard.py` and `tutor_tools.py` are gone; `generation.py`, `topic_view.py`, `tutor.py` and the tests import from `topics`. The duplicate resolve test in `tests/services/test_tutor_tools.py` is dropped (`tests/services/test_topics.py` covers it).
- **Web client exports `apiFetch(path, init, {redirectOn401?})`** (base URL, same-origin credentials, 401 handler). `src/api/tutor.ts` streams through it; `configureTutorStream` and the "call `listThreads` to trigger the redirect" workaround are removed.
- `justfile`: `curation-integration`, `visual-integration`, `tutor-integration` recipes kept for local runs; CI runs everything under `tests/integration` through the single `integration` job. `uv.lock` and `bun.lock` regenerated.

## Left as shipped (documented, not changed)

- Task-class names: section 7 maps its steps onto the registry's existing `storyboarding`, `script-generation`, `script-repair`, `figure-building` (`app.services.storyboard.STEP_TASK_CLASSES`); section 9 uses `tutoring`; section 6 added `topic_extraction`, `topic_merge`, `scoring`, `prerequisites` to `brainbench_providers.TASK_CLASSES` and the routing UI. The names in the wave-3 briefs were advisory; the registry is the source of truth.
- `SandboxRunner` resolves the host data dir itself (same call as `prepare_sandboxes()`); two resolutions per worker process, both cached.
- The anatomy sandbox wraps Chromium with `--no-sandbox --disable-dev-shm-usage` at run time (`run_node`) instead of in `infra/Dockerfile.anatomy`.
- `GET /api/tutor/sources/{id}/file` serves a stored PDF for citation links from the tutor router.
- Three scripted Strands `Model` fakes exist (`brainbench_agents.testing`, `brainbench_agents.visual.testing`, `brainbench_agents.tutor.testing`) and four copies of the per-test Postgres schema fixture (`pg_factory`) under `tests/integration/`. Consolidation is a test-only refactor; not done here.
- `tutor_tools.py` reads plans and revisions from the ORM rather than through `app.services.plans`; the reads are small and tested.

## Surface for section 10

- Pages: `/setup`, `/login`, `/providers`, `/sources`, `/profile`, `/jobs`, `/progress`, `/plan`, `/topics/:id` (route table `apps/web/src/routes.tsx`: `sections` plus `pageRoutes`), tutor drawer in the app shell (toggle in the app bar).
- Job types: `ingest`, `curation`, `video`, `figure` (`noop` is diagnostic). SSE on `/events`: `jobs.updated`, `jobs.event`, `sources.updated`.
- HTTP: see the per-section notes (`wave2-*.md`, `wave3-*.md`, "Public surface").
- Local run: `just dev` (Postgres on 54329, API on 8000, web on 3000) or the full stack with `docker compose up --build`.
