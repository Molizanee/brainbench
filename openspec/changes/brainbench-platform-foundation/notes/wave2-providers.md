# Wave 2 notes: provider connection (tasks 3.1 to 3.9)

Branch `wave2-providers`, from `main` at `c146c9a`. Tasks 3.1 to 3.9 are checked in `tasks.md`. One commit per task (`feat(providers): ... (3.x)`), plus one follow-up commit for 3.7 (`unwrap agent-level throttles in provider_limit_from`). No migration: every column needed already existed in `0001`.

## Source of the OAuth parameters

omp is reachable: `https://github.com/can1357/oh-my-pi`, cloned at commit `b83200eb866821f02625332025b453f06ce4e12a` (2026-10-12). OpenCode was not needed. Each registry file records the commit and the files it was transcribed from:

- `packages/catalog/src/compat/rules/auth/anthropic.kdl`, `openai-codex.kdl`, `openai-codex-device.kdl`: client ids (Anthropic's stored base64, as omp does), authorize and token URLs, scopes, extra authorize params, callback ports and paths, token body format (`json` for Anthropic with `state` in the body, `form` for OpenAI), refresh headers.
- `packages/ai/src/registry/oauth/anthropic.ts` and `openai-codex.ts`: identity (Anthropic token `account`/`organization` with the `claude_cli/bootstrap` fallback; ChatGPT JWT claims), device-code endpoints and the 3 s poll margin.
- `packages/ai/src/providers/anthropic.ts`, `claude-code-fingerprint.ts`: OAuth request headers, the Claude Code beta list, the `x-anthropic-billing-header` system block with its `cch` xxh64 attestation, the "You are Claude Code" identity block, the `_` tool-name prefix, `/v1/messages?beta=true`.
- `packages/catalog/src/wire/codex.ts`, `packages/ai/src/providers/openai-codex-responses.ts`: `/codex/responses`, `chatgpt-account-id`, `OpenAI-Beta: responses=experimental`, `originator: omp`, `version: 0.159.0`, `store: false`, no output caps or sampling params.
- `packages/ai/src/usage/claude.ts`, `openai-codex.ts`: usage endpoints and rate-limit headers.
- Models from `packages/catalog/src/models.json`. Codex's KDL names `default-model "gpt-5.5"`, which the bundled catalog no longer lists, so `gpt-6.1-sol` is the default. Codex's `originator` is omp's own value (OpenAI may check it); the codex transport sends `User-Agent: brainbench/0.1.0`.
- Neither shipped provider declares token revocation in omp, so `revoke_url` is absent; `DELETE /api/providers/accounts/{id}` revokes only when a registry entry sets it (tested with a modified entry).

## What I verified and how

- `just lint` (ruff, ruff format, mypy strict, oxlint, prettier, tsc) and `just test`: green. Python 180 passed, 1 skipped (the Postgres test without `DATABASE_URL`); `just test-py-eager` the same. Web 34 tests, run twice, no flakes.
- `just image-smoke`: the app image builds with the new dependencies (`strands-agents` 1.59.0, cryptography, httpx, pyyaml, xxhash), `import app.main, app.worker` works on 3.14.8, and the suite passes inside the image in both import modes. The wheel ships `registry/*.yaml`.
- `just providers-integration` against pgvector Postgres on `127.0.0.1:54303`: two accounts connected through the API, a refresh sweep rotating both, usage fetch, a model call that refreshes after a 401, a rate-limit block; then every issued token is searched in a `row_to_json` dump of `provider_credentials`, `provider_blocks`, `provider_usage_windows`, `provider_usage` and `settings`, in the raw encrypted columns, in every API response body, and in the DEBUG log capture. None found; the dump does hold the accounts.
- Contract tests: both transports replay the exchanges in `packages/providers/tests/fixtures/{anthropic,openai-codex}/` (text stream, tool use, structured output, 401 then refresh then retry, second 401 then `needs-reauthentication`, 429 then block). The fixtures are transcribed from the providers' wire formats as omp parses them, not captured from live accounts (each file says so in `provenance`). A full Strands `Agent` tool loop runs over replayed exchanges.
- Browser smoke on my ports (API uvicorn on 8003 with `BRAINBENCH_OAUTH_CALLBACKS=1`, Bun on 3003, Postgres on 54303), in Orca's browser:
  - The providers page lists both providers, login kinds, models, policy notice, routing form.
  - Codex Connect: authorize URL `https://auth.openai.com/oauth/authorize` with `client_id=app_EMoamEEZ73f0CkXaXp7hrann`, `redirect_uri=http://localhost:1455/auth/callback`, S256 `code_challenge` (43 chars), 43-char `state`, the omp extras; the paste field and the "Listening for the callback on localhost:1455 ... Docker host" indicator. Device code: OpenAI's real endpoint returned a user code and the dialog polled.
  - Anthropic Connect: `https://claude.ai/oauth/authorize`, client `9d1c250a-...`, `redirect_uri=http://localhost:54545/callback`, PKCE, state, `code=true`; paste field and indicator on 54545.
  - A real Anthropic sign-in was completed in that tab by the browser session (not by me): the loopback listener received the callback, the token exchange worked, identity (email, organization name and uuid) was stored, the listener closed (`completed by callback`), and the account shows `connected`. I then decrypted its tokens in memory only and searched for them in the Postgres row dump, the encrypted columns, four API responses and the API and web server logs: not found. I made no inference or usage call with that account. Teardown removed the volume and the key, so that credential no longer exists anywhere.
  - Someone set the instance password in that tab before my wizard submit (the log shows the 201 before my 409), so I did not know the password; to sign in I minted a session cookie from the dev database for that dev stack only, without changing the password.
- Teardown: servers stopped, `docker compose down -v`, Orca tab closed, no `./data` in the worktree.

## Shared-file touches (exact)

- `apps/api/app/api/v1/__init__.py`: `from app.api.v1 import auth, health` became `from app.api.v1 import auth, health, providers` (ruff merges imports, so a separate line is not possible), and `router.include_router(providers.router)` appended. The single router serves both `/api/providers...` and `/api/settings/providers` (absolute paths, no prefix).
- `apps/api/app/core/config.py`: field `brainbench_oauth_callback_host: str = "0.0.0.0"` (listener bind host; compose already publishes the ports on host loopback only).
- `packages/providers/pyproject.toml`: dependencies `cryptography>=46`, `httpx>=0.28.1,<1`, `pyyaml>=6.0.2`, `strands-agents==1.59.0`, `xxhash>=3.5`. `uv.lock` regenerated by `uv sync`.
- `justfile`: recipe `providers-integration` (migrate `DATABASE_URL`, then `pytest -m integration apps/api/tests/integration`).
- `.github/workflows/ci.yml`: job `providers-integration` (pgvector service, `just providers-integration`).
- `apps/web/src/events/invalidations.ts`, `routes.tsx`: untouched. Login status is polled with TanStack `refetchInterval`; no event type is emitted.
- No migration, `infra/schema.rev` unchanged.

## Changes needed in never-edit files

1. `apps/api/app/main.py` (integrator): run the refresher (60 s sweep: token refresh, then usage windows) and close pending logins and callback listeners on shutdown. In `lifespan`:

   ```python
   from app.services.providers import start_refresher
   ...
           if schema_guard:
               await ensure_schema_at_head(engine, "api")
           async with start_refresher(app):
               yield
   ```

   Until then tokens are still refreshed on demand when an account is leased (in the API and the worker), but usage windows are not fetched in the background.
2. `apps/web/src/api/client.ts`: there is no PUT helper, so `PUT /api/settings/providers` (full replace, kept per the task) cannot be called from the web. I added `PATCH /api/settings/providers` (partial update) and the page uses it. The change wanted: `export const apiPut = <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>('PUT', path, body, options)`.
3. `BACKEND_GOOD_PRACTICES.md`: nothing new beyond the wave 1 note.

## Public surface for sections 6, 7, 9 and 10

### App entry points: `apps/api/app/services/providers.py`

```python
async def ensure_provider_for(session, task_class: str, runtime: ProviderRuntime | None = None) -> Route
    # call before enqueueing a job or accepting a tutor message.
    # raises NoProvider (DomainError, code "no-provider", HTTP 400, message links /providers)
    #        VaultLockedError (Conflict, code "vault-locked", 409, message names the key path)
    #        InvalidRouting (code "invalid-task-class") for an unknown task class
    # blocked accounts pass (jobs defer later).
async def model_for(session, task_class: str, runtime: ProviderRuntime | None = None) -> strands.models.Model
    # same checks, then AnthropicOAuthModel | CodexOAuthModel for the routed provider and model;
    # the model selects an account per call and records provider_usage with this task_class.
def provider_limit_from(exc: BaseException) -> app.jobs.errors.ProviderLimitReached
    # accepts the ModelThrottledException or the Agent's EventLoopException wrapping it.
def find_throttle(exc: BaseException) -> ModelThrottledException | None
async def resolve_route(session, task_class, runtime=None) -> Route   # Route(task_class, spec, model, source)
@asynccontextmanager async def start_refresher(app: FastAPI, runtime=None) -> AsyncIterator[ProviderRuntime]
def get_provider_runtime() -> ProviderRuntime   # FastAPI dependency; tests override it
class ProviderRuntime(registry, session_factory, key_path, clock=..., http=..., callbacks_enabled=False,
                      callback_host="0.0.0.0", callback_port_overrides={}, refresh_interval_s=60)
    .store (SqlProviderStore) .pool (AccountPool) .refresher .usage .logins
    async vault_status() / cipher(create=False) / reset_vault(confirm) / overview()
    async start_login(provider_id, kind) / get_login(id) / complete_login(id, text)
    async set_priority(id, n) / delete_account(id)
```

Pattern for a job handler (sections 6, 7):

```python
from brainbench_providers.strands_models import ProviderRetryStrategy
from app.services.providers import model_for, provider_limit_from, find_throttle

model = await model_for(ctx.session, "curation")
agent = Agent(model=model, retry_strategy=ProviderRetryStrategy(), ...)
try:
    result = await agent.invoke_async(prompt)        # or agent.structured_output(...)
except Exception as exc:
    if find_throttle(exc) is not None:
        raise provider_limit_from(exc) from exc      # the worker defers until reset_at
    raise
```

Use `ProviderRetryStrategy()`: Strands' default strategy retries every `ModelThrottledException` for about two minutes; this one re-raises at once when every account is blocked until later (so jobs defer), and still retries when another account can serve or the reset is under a minute away. The tutor (section 9) reports `find_throttle(exc).reset_at`. Tests can build a runtime over their own session factory with `http=FakeProviders().http_factory()` or a `Replay`.

### Package: `packages/providers/src/brainbench_providers/`

- `strands_models.py`: `AnthropicOAuthModel`, `CodexOAuthModel` (Strands `Model`; config `model_id`, `max_tokens`, `temperature`, `task_class`, `context_window_limit`), `build_model(spec, *, pool, http, model=None, task_class=...)`, `ProviderThrottledException(ModelThrottledException)` with `.reset_at`, `.scope` (`5h`, `7d`, `usage-limit`, `rate-limit`, `requests`, `all-accounts`), `ProviderRetryStrategy`. `stream()` honors `cancel_signal` (closes the HTTP stream), emits native `toolUse` blocks, reasoning deltas, and `metadata.usage`. `structured_output()` uses the transport's structured mode and validates with the Pydantic model.
- `transports/base.py`: `ChatRequest`, `ToolDef`, `ToolChoice`, wire events (`MessageStart`, `BlockStart`, `TextDelta`, `ToolInputDelta`, `ReasoningDelta`, `BlockStop`, `MessageStop`, `Usage`), `TokenSource`. `transports/anthropic_messages.py`, `transports/openai_responses.py`: `stream(source, request, cancelled=None)`, `structured(source, request, name=, schema=) -> (dict, Usage | None)`.
- `registry.py` (+ `registry/*.yaml`): `load_registry(extra_files)`, `Registry.get/enabled/is_enabled/require_enabled`, `ProviderSpec` (`login`, `refresh`, `identity`, `usage`, `transport`, `models`, `default_model`, `model(id)`, `login_for(kind)`). `$BRAINBENCH_DATA/models.yaml` is loaded as extra providers when present. Grammar note: `login` is a list (Codex has `oauth-code` and `device-code`); the first entry is the primary login.
- `vault.py`: `VaultCipher` (AES-256-GCM, associated data binds provider, identity key and column), `identity_key(identity, org_scoped)` (`email:<e>|org:<id>`), `Credential` (decrypted, tokens hidden from `repr`), `StoredCredential`.
- `store.py`: `ProviderStore` protocol (implemented by `app/services/provider_store.py: SqlProviderStore`), `MemoryStore`, `Block`, `UsageWindow`, `UsageRecord`.
- `select.py`: `select(...)`, `AccountPool.lease(provider) -> CredentialHandle` (fresh token; skips accounts whose refresh is revoked), `CredentialHandle.record_usage/rate_limited/observe_windows`.
- `refresher.py`: `Refresher.sweep()`, `refresh(id, force=False, stale_version=None)`. `usage.py`: `UsageMonitor.refresh_stale()`, payload and header parsers. `oauth/`: `LoginManager`, code and device engines, loopback `CallbackServer`.
- `errors.py`: `ProviderRateLimited(reset_at, scope)`, `NoCredential`, `AuthenticationFailed`, `RefreshFailed(definitive)`, `VaultLocked(key_path, state)`, `StateMismatch`, ...
- `testing.py`: `FakeProviders` (httpx `MockTransport` for both providers' token, identity, device and usage endpoints; every token contains `PLAINTEXT`) and `Replay` (serves a fixture's exchanges in order and checks each request). Sections 6, 7, 9 can drive agents in tests with a `Replay` of their own fixtures.

### HTTP (all behind the session cookie)

| Method and path | Body | Answer |
| --- | --- | --- |
| `GET /api/providers` | | `ProvidersRead`: `vault`, `callbacks_enabled`, `usage_since`, `providers[]` with `enabled`, `login_kinds`, `callback_port`, `policy_notice`, `models[]`, `accounts[]` (identity, `status` connected/needs-reauthentication/locked/blocked, `expires_at`, `priority`, `blocked_until`, `blocks[]`, `windows[]`, `usage_today`). No token field anywhere. |
| `POST /api/providers/{id}/logins` | `{kind?}` | 201 `LoginRead` (`authorize_url`, `paste_code`, `callback_listening`, `callback_port`, `verification_url`, `user_code`, `interval_s`, `status`, `expires_at`). 404 unknown provider, 409 `provider-disabled` / `vault-locked`. |
| `GET /api/providers/logins/{login_id}` | | `LoginRead`; `status` pending/connected/failed/expired. |
| `POST /api/providers/logins/{login_id}/complete` | `{input}` | `LoginRead`; 400 `state-mismatch` (login failed, nothing stored), 409 `login-finished`. |
| `PATCH /api/providers/accounts/{id}` | `{priority}` | 204 |
| `DELETE /api/providers/accounts/{id}` | | 204 (revokes where the registry has `revoke_url`) |
| `POST /api/providers/vault/reset` | `{confirm: "reset"}` | `VaultRead`; 400 `confirmation-required` |
| `GET /api/settings/providers` | | `task_classes`, `enabled_providers` (null = all), `routing`, `effective` per task class (provider, model, `source` explicit/default, `connected`), `options` (enabled providers and models only) |
| `PUT /api/settings/providers` | `{enabled_providers, routing}` | full replace; 400 `invalid-routing` |
| `PATCH /api/settings/providers` | either field | partial update (used by the web) |

Routing: an explicit route applies while its provider is enabled; otherwise the first enabled provider with a connected account and its default model. A route to a disabled provider stays stored and is ignored until re-enabled.

### Web

- `src/api/providers.ts`: types named after the API models, `providerKeys` (`['providers', ...]`: `list()`, `login(id)`, `settings()`), `getProviders`, `startLogin`, `getLogin`, `completeLogin`, `deleteAccount`, `updateAccount`, `resetVault`, `getProviderSettings`, `patchProviderSettings`, `VAULT_RESET_CONFIRMATION`.
- `src/pages/providers/`: `ProvidersPage` (default export), `ProviderCard`, `AccountRow`, `ConnectDialog`, `RoutingCard`, `VaultAlert`, `StatusChip`, `format.ts`.
- `tests/support/providersMock.ts`: module-boundary mock of `src/api/providers` (same pattern as `authMock`). Any test that renders `/providers` through `routes` should import it.

## Known limits

- Recorded fixtures are transcriptions, not live captures; the first real inference call will be the true contract check (the Anthropic login, identity, and loopback path were checked live).
- Strands import is eager on 3.14 (`__lazy_modules__` is in place for 3.15): `app.services.providers` adds about 0.3 s to API start.
- Pending logins live in the API process memory: one uvicorn worker, as compose runs it.
