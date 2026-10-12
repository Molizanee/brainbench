# Wave 2 notes: material ingestion (tasks 5.1 to 5.6)

Branch `wave2-ingest`, branched from `c146c9a`. Tasks 5.1-5.6 are checked in `tasks.md`. Commits, in order:

| Commit | Content |
| --- | --- |
| `feat(ingest): sources API ... (5.1)` | `POST/GET/DELETE /api/sources`, `enqueue_ingest`, config fields, new dependencies |
| `feat(ingest): local fastembed embeddings ... (5.3)` | `app/services/embeddings.py`, `app/services/chunks.py` (batched insert) |
| `feat(ingest): ingest job ... (5.2)` | chunker, extractors, pipeline, `ingest` handler |
| `feat(ingest): semantic search ... (5.4)` | `search_chunks`, `POST /api/search` |
| `feat(ingest): sources page ... (5.5)` | web API module, sources page, event invalidation, route |
| `feat(ingest): pgvector integration tests ... (5.6)` | `tests/integration/test_ingest_search.py`, `just ingest-integration`, CI job |
| `feat(ingest): expand PDF ligatures ... (5.2)` | two extraction fixes found in the smoke run |
| `feat(ingest): keep integration tables in the per-test schema ... (5.6)` | test isolation fix found in the smoke run |

5.3 is committed before 5.2 because the handler calls the embeddings module and the batch insert.

## What I verified, and how

- `just lint` (ruff, ruff format, mypy strict, oxlint, prettier, tsc) is green. `just test`: 135 Python tests passed, 4 skipped (the integration file without `DATABASE_URL`); 36 web tests passed. `just test-py-eager`: 135 passed. I ran `bun test` three times in a row with no flakes.
- `just ingest-integration` against `pgvector/pgvector:pg16` (pgvector 0.8.6) on `127.0.0.1:54305`: 4 passed. That covers cosine ranking, `k`, the source and topic filters, a filtered query where a plain HNSW scan with `ef_search` 40 would come back short, and a PDF uploaded through the API and ingested with the real `bge-small` model, with page refs on every chunk. It also covers `POST /api/search` with "Raft leader election", k=5 (ordered hits, top hit on page 1 of the fixture), delete removing the hits, and delete racing a running job. It passes both on an empty database and on one already migrated with `alembic upgrade head`.
- Smoke against the dev Postgres (migrated with Alembic) on API `:8305` and web `:3005`. All requests went through the Bun proxy with a session cookie:
  - I submitted `raft.pdf` (the Raft paper, 567 KB, multipart), `https://en.wikipedia.org/wiki/Raft_(algorithm)` and `https://github.com/wheresryan22/anatomy`, then a 50 MB + 5 byte PDF, which got 413 `{"code":"file-too-large","message":"PDF uploads are limited to 50 MB."}`.
  - A throwaway script (not committed) ran each queued `ingest` job through `HANDLERS["ingest"]` with a hand-built `JobContext`, the way section 2's loop will. Results:

    | Source | Time | Chunks | Locations |
    | --- | --- | --- | --- |
    | PDF | 4.3 s | 70 | `{"page": 1..18}` |
    | URL | 1.0 s | 12 | `{"fragment": "#:~:text=...,..."}`; title updated to "Raft (algorithm) - Wikipedia" |
    | Git | 120.6 s | 1409 | `{"path": ...}`; README first, then docs, then sources; 194 `sentence_continuation` pieces from long code blocks |

    Every source had `chunk_count` equal to the stored rows, a maximum chunk length of 2000, and all chunks embedded. Each job wrote 3 `sources.updated` events (extracting, embedding, ready).
  - `POST /api/search {"query":"Raft leader election","k":5}` returned 5 hits in descending score: raft.pdf page 5 (0.820), then Wikipedia (fragment), page 8, Wikipedia, page 3. A query about isometric figures returned anatomy `references/verify.md`, `SKILL.md` and `kit/iso-kit.mjs`. `k=51` returned 422.
  - Two fixes came out of the smoke and are committed: pymupdf kept ligature glyphs ("conﬁguration"; after the fix the paper has 0 ligatures), and table pipes from trafilatura ended up in text fragments.
  - In Orca's browser on `http://localhost:3005/sources`: the login redirect came back to `/sources`, and the table listed the three sources. I picked `raft-browser.pdf` on the PDF tab and clicked Upload. Network showed `POST /api/sources` 201 through the proxy, and the row appeared as `queued`. After running the job, the row turned `ready / 70` without a reload. That was the 5 s in-flight poll, because `/events` is 404 until section 2 lands. Delete asked for confirmation, the row disappeared, and the uploaded file was removed from `data/sources/`.
- Afterwards: the dev servers were stopped, `docker compose down -v` was run, `./data` was removed, and the browser tab was closed. `git status` is clean.

## Shared-file touches (exact)

- `apps/api/app/api/v1/__init__.py`: the import became `from app.api.v1 import auth, health, search, sources`, and two lines were appended: `router.include_router(sources.router)` and `router.include_router(search.router)`.
- `apps/api/app/jobs/__init__.py`: `from app.jobs import ingest as ingest`.
- `apps/api/app/core/config.py` (fields only, plus one derived path property next to the existing ones):
  ```python
  # Material ingestion (design D5).
  pdf_max_bytes: int = 50 * 1024 * 1024
  git_file_max_bytes: int = 1024 * 1024
  git_clone_timeout_s: float = 300.0
  url_fetch_timeout_s: float = 60.0
  embedding_batch_size: int = 64

  @property
  def sources_dir(self) -> Path:  # brainbench_data / "sources"
  ```
- `apps/api/pyproject.toml`: `pymupdf>=1.28.2`, `python-multipart>=0.0.32`, `trafilatura>=2.3.1`. I ran `uv lock` (via `uv add`); the integrator regenerates it.
- `apps/web/src/routes.tsx`: `import LibraryBooksIcon from '@mui/icons-material/LibraryBooks'` and one `sections` entry `{ path: 'sources', label: 'Sources', icon: LibraryBooksIcon, Page: lazy(() => import('./pages/sources/SourcesPage')) }`.
- `apps/web/src/events/invalidations.ts`: `import { sourceKeys } from '../api/sources'` and the entry `'sources.updated': () => [sourceKeys.all]`.
- `justfile`: appended the recipe `ingest-integration`.
- `.github/workflows/ci.yml`: appended the job `ingest-integration` (pgvector service, `BRAINBENCH_DATA=${{ github.workspace }}/data`, which downloads the model, about 67 MB, on each run).
- `apps/web/package.json`: no change.

## Migration

None. Everything uses the `0001` schema: `sources.{uri,note,file_path,error,chunk_count,job_id}`, `chunks.{location,sentence_continuation,embedding}`, `topics.orphaned_at`, `jobs.cancel_requested`, `job_events`.

## Integration points for the integrator and section 2 (nothing in a never-edit file is blocked)

1. **Enqueue.** `app/services/sources.py: enqueue_ingest(session, source) -> Job` is the only place that creates ingest jobs. It inserts `Job(type="ingest", status="queued", payload={"source_id": str(source.id)})`, flushes, and sets `source.job_id`. Switch its body to section 2's `app.services.jobs.enqueue`.
2. **Cancel on delete.** `SourceService.delete` handles the source's job itself: `queued` → `status="cancelled"`, `finished_at=now`, `cancel_requested=True`; `running` → `cancel_requested=True` only. If section 2 ships a cancel service, call it there instead. The handler polls `ctx.cancelled()` after extraction and between embedding batches. If the source row is gone, the handler ends without raising: a deleted source is not a job failure.
3. **Events.** On every source status change, the handler writes `JobEvent(job_id=ctx.job_id, kind="sources.updated", message=source.error, data={"source_id", "status", "chunk_count"})` and commits. Section 2's `/events` forwarder should emit it as `{"type": row.kind, "payload": row.data}` (that is, use `kind` as the SSE type for rows that are not generic job events). The web already maps `sources.updated` → `['sources']`. `kind` is `String(16)` and `sources.updated` is 15 characters.
4. **Session and commits.** The handler commits `ctx.session` at each status change, and the chunk insert commits together with `ready`. On failure it rolls back, marks the source `failed` with zero chunks, and commits. A `session_scope` wrapper around the handler stays correct (the final commit is a no-op).
5. **Error classes.** `ExtractionError` → `PermanentJobError(reason="extraction-failed")`. Cancellation → `PermanentJobError(reason="cancelled")`. A bad payload → `PermanentJobError`. Anything else, such as an ONNX crash or a DB error, is recorded on the source (`"Ingestion failed unexpectedly (...)"`) and re-raised for the retry policy. A retry sets the source back to `extracting`.
6. **Live status without `/events`.** The web list polls every 5 s only while a source is `queued`, `extracting` or `embedding`. Keep it as a fallback or remove it once SSE works; it is one option in `pages/sources/useSources.ts`.

## Public surface for sections 6, 7, 9 and 10

### Backend

- `app/services/search.py`:
  ```python
  MIN_K = 1; MAX_K = 50
  @dataclass(frozen=True)
  class ChunkHit: chunk_id: UUID; source_id: UUID; source_title: str; location: dict[str, Any]; text: str; score: float  # 1 - cosine distance
  async def search_chunks(session, query: str, k: int, *, source_ids: Sequence[UUID] | None = None, topic_id: UUID | None = None) -> list[ChunkHit]  # ValueError outside [1, 50]
  async def nearest_chunks(session, vector: list[float], k: int, *, source_ids=None, topic_id=None) -> list[ChunkHit]  # same, with a precomputed embedding
  ```
  `topic_id` restricts to chunks cited in `topic_chunks`; `source_ids=[]` returns nothing. On Postgres it sets `hnsw.ef_search = max(100, k)` and `hnsw.iterative_scan = strict_order` per transaction, so filtered results stay exact and complete. Ranking needs pgvector; on SQLite, call it only through a monkeypatch.
- `app/services/embeddings.py`: `embed_texts(texts: list[str]) -> list[list[float]]` (passages) and `embed_query(text: str) -> list[float]` (adds the bge instruction `QUERY_INSTRUCTION`). Both are synchronous and CPU-bound; from async code call `await asyncio.to_thread(...)`. The model is loaded once per process from `settings.models_dir` (`TextEmbedding(model_name=settings.embedding_model, cache_dir=str(settings.models_dir))`). Use `embed_texts` for `topic_revisions.embedding` (6.x reconcile) and for the tutor's related-topics step (D10), so every vector lives in the same space. Tests monkeypatch `app.services.embeddings.embed_texts` / `embed_query`.
- `app/services/chunker.py` (pure): `TARGET_CHARS = 1500`, `MAX_CHARS = 2000`, `ChunkPiece(text, sentence_continuation)`, `chunk_text(text, *, target, max_chars) -> list[ChunkPiece]`, `split_sentences(text) -> list[(start, end)]`, `split_oversized(sentence, max_chars) -> list[str]`. Chunks are exact slices of the input.
- `app/services/extraction.py`: `ExtractionError`, `Section(text, location, text_fragments)`, `Extracted(sections, title)`, `extract_url(url, *, timeout_s)`, `extract_pdf(path)`, `async extract_git(url, workdir, *, timeout_s, max_file_bytes)`, `extract_note(text)`, `text_fragment(text)`. The Git clone is http(s) only, with no prompts, credential helpers or LFS. Symlinks are skipped. The allowlist is in `GIT_DOC_SUFFIXES`, `GIT_SOURCE_SUFFIXES` and `GIT_SOURCE_NAMES`.
- `app/services/chunks.py`: `NewChunk(position, text, location, sentence_continuation)`, `async replace_chunks(session, source_id, chunks, embeddings) -> int` (does not commit).
- `app/services/ingestion.py`: `async ingest_source(session, source_id, *, job_id, progress, cancelled)`, `build_chunks(extracted)`, `SOURCES_UPDATED = "sources.updated"`, `IngestionCancelled`.
- `app/services/sources.py`: `SourceService(session).create(body) / create_pdf(filename, file) / list() / delete(source_id)`, `enqueue_ingest(session, source)`. Errors: `SourceNotFound` (404 `source-not-found`), `FileTooLarge` (400 `file-too-large`), `NotAPdf` (400 `not-a-pdf`).
- **Chunk `location` shapes:**
  - PDF: `{"page": n}` (1-based; chunks never span pages).
  - Git: `{"path": "docs/raft.md"}`.
  - URL: `{"fragment": "#:~:text=start,end"}`, a W3C text fragment that points at the chunk itself. Link it as `source.uri` + `fragment`, after stripping any fragment already on `uri`.
  - Note: `{}`; cite the note by its title.
- Uploaded PDFs are stored at `BRAINBENCH_DATA/sources/<source_id>.pdf`; `sources.file_path` is relative to `BRAINBENCH_DATA`. `init` does not create `sources/`; the API creates it on the first upload.
- Job type `ingest`, payload `{"source_id": "<uuid>"}`. A Git job uses `settings.jobs_dir/<job_id>/` and removes it afterwards.

### HTTP

- `POST /api/sources`: JSON `{"type":"url","url"}`, `{"type":"git","url"}` or `{"type":"note","title"?,"text"}` (http/https only, otherwise 422 at `body.url`); or `multipart/form-data` with `file` (and optional `type=pdf`). Returns 201 `SourceRead`. A PDF over 50 MB gets 413 `file-too-large` (checked from `Content-Length`, then from the spooled size, then again while copying).
- `GET /api/sources` → `SourceRead[]`, newest first: `{id, type, title, uri, status, chunk_count, error, job_id, created_at, updated_at}`.
- `DELETE /api/sources/{id}` → 204, or 404 `source-not-found`. Topics whose cited chunks all come from this source get `topics.orphaned_at = now`; topics that are also cited from another source are unchanged.
- `POST /api/search {query, k=10 (1..50), source_ids?, topic_id?}` → `{"hits": [{chunk_id, source_id, source_title, location, text, score}]}`, best first. The first search in an API process loads the model, which takes about 1 s when it is already cached.

### Web

- `src/api/sources.ts`: types `SourceType`, `SourceStatus`, `SourceRead`, `SourceCreate` (+ `UrlSourceCreate`, `GitSourceCreate`, `NoteSourceCreate`); `IN_PROGRESS_STATUSES`, `PDF_MAX_BYTES`, `PDF_LIMIT_MESSAGE`; `sourceKeys.all = ['sources']`, `sourceKeys.list()`; `listSources()`, `createSource(body)`, `uploadPdf(file)`, `deleteSource(id)`.
- Route `/sources` (`pages/sources/SourcesPage.tsx`, plus `AddSourceForm`, `SourceForms`, `SourcesTable`, `useSources`).
- Event type `sources.updated` invalidates `['sources']`.
- Tests: `tests/support/sourcesMock.ts` follows the `authMock` pattern. `sourcesApi.useFakes(list)` installs in-memory fakes for page tests, and `sourcesApi.useReal()` restores the real functions. Any test that renders routes now loads the sources page module only when it navigates there, so the existing tests do not need the mock. Note that happy-dom replaces the global `File`; tests that post `FormData` with Bun's `fetch` must build files with `File` from `node:buffer`.

## Known limits (by design, not deferred work)

- Scanned PDFs without a text layer fail with a readable reason (no OCR).
- trafilatura runs with its defaults, except that the insecure-SSL fallback is turned off and the timeout comes from `url_fetch_timeout_s`. Its SSRF protection stays on, so private-network URLs are refused.
- A large repository is ingested in full, within the allowlist and the 1 MB per-file cap. On the anatomy repository that was 1409 chunks in about 2 minutes on an M-series CPU.
