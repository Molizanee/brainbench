# Wave 4 notes: end-to-end verification and docs (tasks 10.1 to 10.3)

Branch `wave4-verification`, from `main` at `e774392`. Tasks 10.1, 10.2 and 10.3 are checked in `tasks.md`; with them every task of the change is checked. Commits, in order:

| Commit | Content |
| --- | --- |
| `b3b28cf fix(foundation)` | `db` healthcheck over TCP (bug 1) |
| `9be8686 fix(foundation)` | compose builds the `runtime` stage of `Dockerfile.app` (bug 2) |
| `141a5d4 feat(verify) (10.1)` | `infra/smoke/fresh_clone.sh`, `just smoke-fresh-clone`, CI job `fresh-clone` |
| `9f96e07 fix(verify)` | fresh-clone teardown keeps the shared sandbox images (bug 3, in my own script) |
| `66edc8a fix(providers)` | a repeated OAuth callback for a completed sign-in shows Connected (bug 4) |
| `e3d40f0 fix(web)` | absolute asset URLs in the production build (bug 5) |
| `1ae8441 fix(visual)` | topic page numbers plan weeks from 1 (bug 6) |
| `d1b3698 feat(verify) (10.2)` | smoke screenshots under `notes/smoke/` |
| `0759393 docs (10.3)` | `README.md`, the `BACKEND_GOOD_PRACTICES.md` §6 correction |
| this file | |

No migration, no dependency, no config field. `infra/schema.rev` is unchanged (`0001`).

## Final state of the checks (HEAD `0759393`, macOS arm64, Docker Desktop 29.2.1, Compose v5.1.0)

- `just lint`: green.
- `just test`: Python 650 passed, 20 skipped (649 on `main` plus the new OAuth test); web 125 passed (123 plus the build test and the topic header test).
- `just test-py-eager`: 650 passed.
- `just integration` against pgvector pg16 from `infra/compose.dev.yml`: 24 passed, 1 skipped (`test_figure_build.py` needs the Anatomy skill installed in `BRAINBENCH_DATA`; `just visual-integration` covers it).
- `just smoke-fresh-clone`: 56 of 56 checks (output below).

## 10.1 Fresh-clone test

`infra/smoke/fresh_clone.sh` (bash, `set -euo pipefail`; needs Docker with Compose v2, git, curl and python3; macOS and Linux). `just smoke-fresh-clone` runs it; the CI job `fresh-clone` (ubuntu-latest, 60-minute timeout) runs the recipe. What it does:

1. Preflight: the tools, and ports 3000, 54545 and 1455 free. Every `BRAINBENCH_*` and `COMPOSE_*` variable the compose file could read is unset, so the run really has no environment.
2. `git clone "$SRC" "$tmp/<unique id>"` and a detached checkout of `HEAD` (CI's checkout can be a detached merge commit). The project name is unique (`bbfresh<epoch><pid>`), so it never touches another checkout's stack or volume. Only committed changes are tested; the script says so when the tree is dirty.
3. `docker compose up -d --build --quiet-pull`, then waits up to 600 s for `BrainBench ready at http://localhost:3000` in the web log.
4. Assertions after the first up: `GET /` is 200 `text/html` with the SPA root; `/api/auth/me` says `password_set: false`; headless Chromium from `brainbench/anatomy`, on the compose network, renders the wizard ("Welcome", "Set password and continue"); `./data/vault.key` exists with mode 600 and its SHA-256 (computed in a container, since the file is root-owned on Linux) equals `settings.vault_fingerprint` read through `docker compose exec db psql`; `.commit` equals `infra/anatomy.pin`; the data directories and the embedding model exist; `brainbench/manim` and `brainbench/anatomy` exist and both build-only services exited 0; `init` exited 0, started after the db's last "ready to accept connections" log line, and logged the migration, the generated key, the skill install and "bootstrap complete"; the worker logged "sandbox probe passed".
5. Seed: `POST /api/auth/setup` and one note source, ingested by the worker to `ready`.
6. A second `up -d`: `init` logs "no migration ran", "vault key ok" and "anatomy skill unchanged"; the key hash, the fingerprint, `.commit` and the skill directory inode are unchanged; the password still signs in, its hash is unchanged, and the note is still `ready` with the same chunk count. These "data intact" checks repeat after every later step.
7. `./data/vault.key` removed, `up -d`: `init` logs "vault is locked (locked-missing)" and "no key was generated"; no key file appears; the fingerprint is unchanged; `GET /api/providers` reports `{"state": "locked", "detail": "locked-missing", "key_path": "/data/vault.key"}`; an unconfirmed `POST /api/providers/vault/reset` is refused with 400 `confirmation-required` (the reset option exists without being used).
8. `infra/anatomy.pin` set to the pin's parent commit (fetched with `git fetch --depth 2`), `up -d --build`: `init` logs the install of the new pin, `.commit` is the new pin, the `skills/anatomy` inode changed (swapped by rename, not edited in place), no `anatomy.staging` or `anatomy.old` is left, `SKILL.md` exists.
9. A well-formed but nonexistent pin, `up -d --build`: `init` still exits 0 and logs "anatomy skill not updated (installed: <previous pin>)", nothing is installed, the directory and `.commit` are unchanged, no staging directory is left, the UI is served.
10. An `EXIT` trap, also on failure or interrupt: prints `ps` and the last log lines when failing, then `docker compose down -v --remove-orphans`, removes the project's `init`, `api`, `worker` and `web` images, deletes `./data` through a container (root-owned on Linux) and the temp dir.

### Runs by hand on this machine

- Run 1 (stopped by me): the clone was under `$TMPDIR` (`/var/folders/...`). This Docker Desktop does not share that path with the VM: the bind mount silently landed inside the VM, and `./data` never appeared on the host. The script now creates its temp dir under `/tmp` (override: `FRESH_CLONE_TMPDIR`) and checks first that a bind mount of the clone reaches the host. During this run the Hugging Face CDN (`us.aws.cdn.hf.co`) refused TCP connections from this machine for several minutes and the model download stalled; it worked again later.
- Run 2 on `9be8686`: 56 of 56, about 8 minutes including the image builds. Its teardown used `docker compose down -v --rmi local`, which also deleted `brainbench/manim` and `brainbench/anatomy` (bug 3).
- Run 3 on `0759393` (final, through `just smoke-fresh-clone`): 56 of 56, about 3 minutes with cached layers; the sandbox images were still on the daemon afterwards. Output summary:

```
== Preflight
PASS  docker, compose, git, curl, python3 present; ports 3000, 54545, 1455 free
== Clone 0759393c54020cf31af9d740cc81f12ba1532aab into /tmp/brainbench-fresh-clone.Xa62EP/bbfresh179181513663654
PASS  fresh clone at 0759393c54020cf31af9d740cc81f12ba1532aab, no ./data and no .env
      anatomy pin: 689dcf966e63d92be10a5d593c6e19e10acd4422
== First docker compose up -d --build (no environment)
PASS  web logged 'BrainBench ready at http://localhost:3000'
PASS  GET / answers 200 text/html with the SPA root
PASS  GET /api/auth/me on the fresh instance: {"password_set":false,"authenticated":false}
PASS  headless Chromium renders the first-run wizard at / ('Welcome', 'Set password and continue')
PASS  ./data/vault.key exists with mode 0600
PASS  sha256(./data/vault.key) equals settings.vault_fingerprint (466a5852d28efd83c9139948d71da0e5174d36af7b8ce94c398cd756c4c5afac)
PASS  ./data/skills/anatomy/.commit equals infra/anatomy.pin (689dcf966e63d92be10a5d593c6e19e10acd4422)
PASS  ./data/{jobs,assets,models,skills} exist and the embedding model is downloaded
PASS  image brainbench/manim exists on the daemon (sha256:8e33473a9385)
PASS  image brainbench/anatomy exists on the daemon (sha256:8f5470c6a68e)
PASS  build-only service sandbox-manim exited 0
PASS  build-only service sandbox-anatomy exited 0
PASS  init exited 0
PASS  init started (2026-10-12T14:25:42.121731464Z) after db accepted connections (2026-10-12T14:25:40.321066130Z)
PASS  first run migrated (init log: 'schema migrated from empty to 0001')
PASS  first run generated the vault key (init log: 'vault key generated')
PASS  first run installed the skill (init log: 'anatomy skill installed at 689dcf966e63d92be10a5d593c6e19e10acd4422')
PASS  bootstrap completed (init log: 'bootstrap complete')
PASS  worker log: sandbox probe passed (host data dir /tmp/brainbench-fresh-clone.Xa62EP/bbfresh179181513663654/data)
== Seed data: instance password and one ingested note
PASS  POST /api/auth/setup 201
PASS  note source dc39e39b-14cc-43fd-b6f0-b8c548ee375f ingested by the worker: ready, 1 chunk(s)
== Second docker compose up -d (same release, same data)
PASS  init exited 0
PASS  re-run: no migration ran (init log: 'no migration ran')
PASS  re-run: vault key verified, not regenerated (init log: 'vault key ok')
PASS  re-run: skill unchanged (init log: 'anatomy skill unchanged')
PASS  re-run: vault.key hash and recorded fingerprint unchanged
PASS  re-run: skill .commit and directory unchanged
PASS  re-run: the instance password set before still signs in
PASS  re-run: settings.password_hash unchanged
PASS  re-run: note source dc39e39b-14cc-43fd-b6f0-b8c548ee375f still ready with 1 chunk(s)
== Remove ./data/vault.key, docker compose up -d
PASS  init exited 0
PASS  missing key: init reports the vault locked (init log: 'vault is locked (locked-missing)')
PASS  missing key: init generated no key (init log: 'no key was generated')
PASS  missing key: no ./data/vault.key created, recorded fingerprint unchanged
PASS  GET /api/providers reports the vault locked: {"state": "locked", "detail": "locked-missing", "key_path": "/data/vault.key"}
PASS  recovery: restore the key at /data/vault.key, or reset; an unconfirmed reset is refused (400 confirmation-required)
PASS  missing key: the instance password set before still signs in
PASS  missing key: settings.password_hash unchanged
PASS  missing key: note source dc39e39b-14cc-43fd-b6f0-b8c548ee375f still ready with 1 chunk(s)
== Change infra/anatomy.pin to another commit, docker compose up -d --build
      new pin: e28bc3f2f91d4a27f3f60edf2207e05596e5f77d (parent of 689dcf966e63d92be10a5d593c6e19e10acd4422 in https://github.com/wheresryan22/anatomy.git)
PASS  init exited 0
PASS  pin change: init installed the new pin (init log: 'anatomy skill installed at e28bc3f2f91d4a27f3f60edf2207e05596e5f77d')
PASS  pin change: ./data/skills/anatomy/.commit is e28bc3f2f91d4a27f3f60edf2207e05596e5f77d
PASS  pin change: swapped by rename (directory inode 17923402 -> 17926342), no anatomy.staging or anatomy.old left, SKILL.md present
PASS  pin change: the instance password set before still signs in
PASS  pin change: settings.password_hash unchanged
PASS  pin change: note source dc39e39b-14cc-43fd-b6f0-b8c548ee375f still ready with 1 chunk(s)
== Deliberately bad pin, docker compose up -d --build
PASS  init exited 0
PASS  bad pin: init logged the failure and kept the installed skill (init log: 'anatomy skill not updated (installed: e28bc3f2f91d4a27f3f60edf2207e05596e5f77d)')
PASS  bad pin: nothing installed (init log has no 'anatomy skill installed')
PASS  bad pin: previous skill e28bc3f2f91d4a27f3f60edf2207e05596e5f77d still in place (same directory), no staging left
PASS  bad pin: the instance password set before still signs in
PASS  bad pin: settings.password_hash unchanged
PASS  bad pin: note source dc39e39b-14cc-43fd-b6f0-b8c548ee375f still ready with 1 chunk(s)
PASS  bad pin: the instance still serves the UI
== Teardown (docker compose down -v, temp clone removed)
fresh-clone test passed: 56 checks on 0759393c54020cf31af9d740cc81f12ba1532aab
```

Not verified: the CI job itself has not run on GitHub (nothing is pushed), and the script has only been run by hand on macOS. On a Linux runner `./data` is root-owned; the script reads the key and deletes files through a container for that reason, but that path has not been exercised on Linux.

## 10.2 Full smoke on the running stack

### Setup, and what was replaced

- `docker compose up --build` from this worktree, default port 3000, no `BRAINBENCH_*` variables, driven in Orca's embedded browser.
- **No live provider account exists on this machine**, so I added a throwaway compose override (kept in my scratchpad, not committed) that runs `api` and `worker` through two wrapper scripts:
  - API: the real `app.main:app`, with the process `ProviderRuntime` built with `http=FakeProviders().http_factory()` (`brainbench_providers.testing`). Only the providers' token, identity and usage endpoints are fake. The OAuth engine (PKCE, state, the loopback listener on 0.0.0.0:54545/1455 published on host loopback, the paste path, first-wins), the identity key, the AES-GCM vault and the Postgres rows are the real code. A throwaway route `GET /api/_smoke/authorize?url=` plays the provider's consent page: it returns the redirect (`redirect_uri?code=…&state=…`) the provider would send for the real authorize URL the dialog shows.
  - API and worker: `providers.model_for` (and the tutor's imported copy) still runs the real `resolve_route` (routing, `no-provider`, `vault-locked`), then returns a scripted Strands model instead of the OAuth transport. It answers every agent step from the real prompt it receives: curation extraction by keyword over the real chunks, merge, scoring, prerequisites; a storyboard citing the chunk ids listed in the prompt; a figure brief; a three-section Manim script built from the scene ids in the prompt; a figure session that writes `build.mjs`, runs it and runs the skill's `capture.mjs` in the real anatomy sandbox; a tutor reply that quotes the retrieved passages under their labels, streamed in word groups.
  - Everything else is the real stack: Postgres and pgvector, the jobs runner and claim limits, the embedding model, ingestion, retrieval, the Manim and Anatomy sandboxes, SSE, the Bun server and the SPA.
- The upgrade, rollback and restore steps ran on the plain compose file, without the override.
- Orca driver notes (not app bugs): `click` does not scroll an element into view, and clicks on some MUI buttons, tabs and select options did not reach React. Where that happened I used a DOM `click()` or `form.requestSubmit()` through `orca eval`, as waves 2 and 3 did. Screenshots taken right after a programmatic scroll or while a dialog fades in can come out blank or ghosted; I retook or dropped those.

### Steps, screenshots and outcomes

| # | Step | Screenshot | Outcome |
| --- | --- | --- | --- |
| 1 | Wizard | `smoke/01-wizard.png`, `smoke/02-providers-after-wizard.png` | `/` redirected to `/setup`; the password was set and the browser landed signed in on `/providers` with the policy banner, both provider cards and Connect actions. |
| 2 | Anthropic, loopback path | `smoke/03-anthropic-connect-dialog.png`, `smoke/04a-loopback-duplicate-request-bug.png`, `smoke/04-anthropic-connected-by-loopback.png` | The dialog showed the real `https://claude.ai/oauth/authorize` URL (client `9d1c250a-…`, `redirect_uri=http://localhost:54545/callback`, S256 challenge, state) and "Listening for the callback on localhost:54545". A second browser tab followed the consent redirect to `http://localhost:54545/callback?code=…&state=…`. API log: `credential … stored for anthropic`, `login … completed by callback`, `listener on port 54545 closed`; the dialog switched to "Connected". The tab itself showed "Sign-in link already used": the browser had sent the callback twice (bug 4, fixed). |
| 3 | Codex, paste path | `smoke/05-codex-paste-redirect.png`, `smoke/06-codex-connected-by-paste.png`, `smoke/07-providers-two-accounts.png` | `https://auth.openai.com/oauth/authorize` with `redirect_uri=http://localhost:1455/auth/callback` and the omp extras; the redirect URL was pasted into "Redirect URL or code", then "Complete sign-in": `login … completed by paste`, listener on 1455 closed, a late request to the callback URL got connection refused. `provider_credentials` holds `email:dev@example.com\|org:org-1` (anthropic) and `email:dev@example.com\|org:ws-1` (codex), tokens encrypted. |
| 4 | Profile | `smoke/08-profile-add-target.png`, `smoke/09-profile-created.png` | Created "Smoke Engineer", 4 h/week, then the current skill Python and the target Distributed consensus (proficient, high). API: version 3. |
| 5 | Ingest a PDF and a URL | `smoke/10-sources-ready.png` | `https://en.wikipedia.org/wiki/Raft_(algorithm)` from the URL tab, then `raft.pdf` (the Raft paper, 554 KB) uploaded through the PDF tab's file input. Both `ready` through the real worker: 12 and 70 chunks. |
| 6 | Curation | `smoke/12-plan-v1.png` | "Run curation" on `/plan`; the worker finished in about 2 s: 47 candidates, merged into 6 topics, 6 new, 5 kept and 1 excluded below 0.4, 4 prerequisite relations, plan version 1. Week 1 holds Replicated state machines, Leader election, Log replication; week 2 the rest. |
| 7 | One video and one figure | `smoke/13-topic-before-generate.png`, `smoke/14-topic-generating.png`, `smoke/15-topic-video.png`, `smoke/16-topic-figure.png` | "Generate" on Leader election queued both; they ran at the same time (one per type). Video: one render attempt, 3.4 s in `brainbench/manim`, sections intro/majority/mechanism at 1.7 s, 2.2 s, 2.2 s, 6.1 s total; in the browser it played to 2.5 s with no media error and three caption cues on those boundaries. Figure: 4 turns, `build.mjs` 0.2 s and `capture.mjs` 3.8 s in `brainbench/anatomy`; the iframe is `sandbox="allow-scripts"`, `referrerpolicy="no-referrer"`, opaque origin (`contentDocument` null), and the HTML is served with exactly `sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:`. A `Range: bytes=0-99` request returned 206. Render minutes used: 0.12; jobs today: 2 of 20. Before bug 5 was fixed, loading `/topics/<id>` directly gave a blank page. |
| 8 | Tutor with citations | `smoke/17-tutor-streaming.png`, `smoke/18-tutor-answer-citations.png` | Asked "Why does a Raft candidate need votes from a majority of servers?" in the topic context ("Tutor context: Leader election"). The reply streamed (17 shows Stop and the first citation chip mid-stream) and ended with two citation chips, `raft.pdf, p. 8` (links to `/api/tutor/sources/<id>/file#page=8`, 200 `application/pdf`) and the Wikipedia page by text fragment. Both messages persisted, the reply with 2 citations and `stopped: false`. No related-topic link was emitted (no plan topic reached cosine 0.8 against this reply). |
| 9 | Log a session, plan re-prioritizes | `smoke/22-progress.png`, `smoke/20-plan-v2-reprioritized.png`, `smoke/21-plan-v2-mastered-last.png` | 60 min "Could teach it" on Replicated state machines (position 0, no dependents): status `mastered`. "Run curation again": reconcile 0 new, 6 unchanged (same topic ids), plan version 2 with Leader election first and Replicated state machines last, marked Mastered. Progress page: 1 h of 4 h this week, 1 mastered, 4 not started. |
| 10 | Upgrade on the same data | `smoke/23-after-upgrade-topic.png` | The stack was stopped as Ctrl-C would, then `docker compose up --build` (no override). The "pull" was the three fix commits made after the stack was built (`66edc8a`, `e3d40f0`, `1ae8441`). I did not add a separate trivial commit, since these are real changes to every image. `init`: "schema already at head 0001; no migration ran", "vault key ok", "anatomy skill unchanged". Row counts, key hash, skill commit and asset files were identical to the snapshot before (sources 2, chunks 82, topics 6, plans 2, assets 2, sessions 1, tutor messages 2, credentials 2, jobs 6, profile version 3). The password still signed in, both accounts were `connected`, the video served, a direct load of `/topics/<id>` worked, and the header now said "week 1". |
| 11 | Rollback | `smoke/24-rollback-previous-release-plan.png` | `main`'s commit `e774392` tagged locally as `v0.0.0-wave3` (its `schema.rev` is `0001`). (1) `docker compose stop web worker api`; (2) `docker compose run --rm init alembic downgrade 0001` from the current checkout: exit 0, a no-op since `0001` is the only revision, schema still `0001`; (3) `git checkout v0.0.0-wave3`, `docker compose up --build`. The previous release started (no migration, key ok, probe passed) and served the same data: identical counts, login 200, plan v2. Afterwards I returned to the branch and deleted the local tag. |
| 12 | Backup and restore (README alternative) | | `pg_dump -Fc` plus `tar czf data`, then `docker compose down -v` and `./data` moved away; restored with `tar xzf`, `up -d --wait db`, `pg_restore --clean --if-exists --no-owner`, `up --build`. Counts, key hash and skill identical, login 200, vault `ok`, both accounts connected. |
| 13 | Vault key recovery | `smoke/25-vault-locked-recovery.png` | With `./data/vault.key` moved away: vault `locked-missing`, both accounts `locked`, "Credential vault locked" with the key path and "Reset vault". With the file put back: `ok` and `connected` again, no restart. |

Teardown: Orca tab closed, `docker compose down -v`, `./data` removed, the dev Postgres used for `just integration` removed, no `bbfresh*` or `brainbench.sandbox` containers left, `brainbench/manim` and `brainbench/anatomy` kept.

### What was not exercised, and why

- **Live provider sign-in and inference.** No Claude or ChatGPT account is available to me on this machine. The authorize URLs were the real ones, but consent, token exchange and identity came from `FakeProviders`. No real model ever ran: the curation, storyboard, script, figure and tutor prompts have still not been tried against a real model, and neither have the Anthropic and Codex transports and their streaming, tool use and structured output, beyond the recorded fixtures in the unit tests.
- Because of that: `provider_usage` rows (written by the real adapters, so the providers page shows 0 calls), refresh and rotation against the real token endpoints, usage windows (the real usage endpoints answered 401 to the fake tokens after the upgrade, logged as warnings, accounts untouched), and throttling and deferral on a real 429.
- Codex device-code sign-in was not exercised in this smoke (the task asks for the loopback and paste paths).
- The script-repair loop (the scripted script rendered on the first attempt), figure turn and time limits, and cancellation were not exercised here; unit and integration tests cover them.
- A schema downgrade that changes anything: with one migration, step 2 of the rollback is a no-op by construction.
- The fresh-clone CI job on a GitHub runner, and the fresh-clone script on a Linux host.

## Bugs fixed

1. **`db` healthy before Postgres accepts TCP** (`b3b28cf`). On a new data directory the pgvector image runs a temporary server with `listen_addresses=''` (checked in its `docker-entrypoint.sh`). `pg_isready` over the unix socket passes against it, so compose could start `init`, whose connection to `db:5432` then fails. The healthcheck now uses `-h 127.0.0.1`. Not unit-testable; the fresh-clone script asserts that `init` started after the db's last "ready to accept connections".
2. **Compose built the CI `test` stage** (`9be8686`). `Dockerfile.app` ends with the `test` stage and `x-app.build` had no `target`, so `init`, `api` and `worker` ran an image with dev dependencies and the tests, from `WORKDIR /app`. One consequence: rollback step 2 (`docker compose run --rm init alembic downgrade …`) fails there with "No 'script_location' key found in configuration" (reproduced on `e774392` during the rollback). Fixed with `target: runtime`. A rollback whose *current* release predates this commit needs `docker compose run --rm -w /app/apps/api init alembic downgrade …`.
3. **Fresh-clone teardown deleted the shared sandbox images** (`9f96e07`, my own script). `down --rmi local` removed `brainbench/manim` and `brainbench/anatomy`. It now removes only the clone project's four images.
4. **A repeated OAuth callback showed "Sign-in link already used" after a successful sign-in** (`66edc8a`, `packages/providers`). Reproduced in the smoke: the browser sent the loopback redirect twice. The losing request answered 400, and that was the page left in the tab. A callback for a login that is already `connected` is still ignored (nothing exchanged again) but now answers the Connected page; unknown, failed and expired states keep the 400 page. Settled states are remembered with their login until it is pruned. New test `test_duplicate_concurrent_callback_shows_connected_and_exchanges_once` (two concurrent requests, one token exchange); `test_paste_wins_…` now expects the Connected page for the late callback, plus a check that nothing was persisted twice.
5. **Blank page on reload or direct link of `/topics/<id>`** (`e3d40f0`, `apps/web`). The production build wrote `<script src="./index-<hash>.js">`, which resolves to `/topics/index-<hash>.js`, which the server answers with the SPA HTML. The build now passes `--public-path=/`. New `tests/build.test.ts` runs the real `build` script into a temp dir and checks every asset URL in the built HTML is absolute; it fails without the fix.
6. **Topic page said "week 2" for a week-1 topic** (`1ae8441`, `apps/web`). The planner numbers weeks from 1 and `TopicHeader` added 1. The fixture used `week_start: 0`, which the planner never writes; it is now 1, and a new test pins "plan v1, item 2, week 1".

## Bugs found and not fixed

- **ERROR log when an `/events` client disconnects.** Reproduction: open any page (the shell holds `/events`), then navigate away or close the tab. The API logs `ERROR sqlalchemy.pool.impl.NullPool: Exception closing connection … asyncio.exceptions.CancelledError`, followed by `WARNING app.services.job_events: job event listener connection closed`. The stream and the next subscriber work; it is log noise at ERROR level from the `EventHub`'s LISTEN connection being cancelled mid-close. The fix belongs in `app/services/job_events.py` (shield or suppress the close on cancellation) and needs a decision on the hub's shutdown order, so I left it.
- **Tutor citation labels in the reply text lose their brackets.** The stored reply contains `[raft.pdf, p. 8]`, but the rendered reply shows plain "raft.pdf, p. 8" (`smoke/18-tutor-answer-citations.png`). I did not investigate the cause; the markdown renderer treating `[...]` as link syntax is the likely one. The chips below the reply carry the links, so nothing is lost. Noted for the tutor owner.

## README sections written (10.3)

In this order: what BrainBench is (two paragraphs); Install (`docker compose up`, the ready line and URL, the wizard); LAN use (`BRAINBENCH_PORT`, `BRAINBENCH_BIND`, `BRAINBENCH_PUBLIC_URL`, callback ports loopback-only); Provider sign-in (Anthropic and OpenAI Codex steps, each registry `policy_notice` quoted verbatim, loopback, paste and device code, priority, enable switch, disconnect); Backup, restore and the vault key (`pg_dump`/`tar`, `pg_restore`, the Linux `sudo` note, key recovery by restoring the file, the confirmed reset and its endpoint `POST /api/providers/vault/reset`); Upgrade; Rollback (the three steps with `git show <previous-tag>:infra/schema.rev`, and the backup-restore alternative); Figure skill pin; Budgets and limits (`render_minutes_month`, `generation_jobs_day`, job, per-type and tutor concurrency, render timeout and attempts, figure turns, wall clock and Node timeout, retries and deferral, how to change each); Development (`just dev`, `test`, `lint`, `integration`, `smoke`, `smoke-fresh-clone`, the Python 3.14 note and the `__lazy_modules__` form). The backup, restore, key recovery, upgrade and rollback commands are the ones run in steps 10 to 13 above. The endpoint is `/api/providers/vault/reset` (the brief wrote `/providers/vault/reset`; the API mounts it under `/api`).

`BACKEND_GOOD_PRACTICES.md` §6: the eager-imports sentence now names `BRAINBENCH_EAGER_IMPORTS=1` (`just test-py-eager`, CI job `python-eager-imports`), says why (`PYTHON_LAZY_IMPORTS` accepts only `all` and `normal` in 3.15.0), and marks the root `conftest.py` filter as the one exception to the no-filter rule. No other edit to that file.
