# BrainBench

BrainBench is a self-hosted study workspace for one software engineer. You describe your current skills, the skills you want to reach, and your weekly study time; you add the material you want to learn from (URLs, PDFs, Git repositories, notes). Agents extract topics from that material, score them against your goals, order them by prerequisites into a weekly study plan, and generate a narrated Manim video and an interactive isometric figure per topic. A tutor in the sidebar answers questions about the current topic, plan or profile and cites the passages of your material it used. Logging study sessions with a mastery rating feeds back into the next plan.

Inference uses your own Claude (Pro/Max) or ChatGPT (Plus/Pro, Codex) subscription: BrainBench runs the provider's OAuth sign-in itself and keeps the tokens in an encrypted vault on your machine. There are no API keys. Embeddings run locally (`BAAI/bge-small-en-v1.5`), generated Manim scripts and figure builds run in network-less sandbox containers, and all state lives in `./data` and one Postgres volume. One instance serves one person; the web UI is protected by an instance password.

## Install

The only prerequisite is Docker with Compose v2. From a clone of this repository:

```sh
docker compose up
```

The first run builds every image (app, web, and the two sandbox images), migrates the database, creates `./data`, generates the vault key, installs the Anatomy figure skill at its pinned commit, and downloads the embedding model. It needs network access for that. When it is done, the web service logs:

```
BrainBench ready at http://localhost:3000
```

Open that URL. The first-run wizard asks for an instance password, signs you in and opens the providers page. No `.env` file or environment variable is needed.

## LAN use

By default the UI is published on the host's loopback only. Three variables change that:

| Variable                | Default     | Effect                                                                                                      |
| ----------------------- | ----------- | ----------------------------------------------------------------------------------------------------------- |
| `BRAINBENCH_PORT`       | `3000`      | Host port of the web UI.                                                                                    |
| `BRAINBENCH_BIND`       | `127.0.0.1` | Host address the web port is published on. `0.0.0.0` makes it reachable from other machines.                |
| `BRAINBENCH_PUBLIC_URL` | unset       | The URL announced in the ready line. When it starts with `https://`, the session cookie is marked `Secure`. |

```sh
BRAINBENCH_BIND=0.0.0.0 BRAINBENCH_PORT=8080 BRAINBENCH_PUBLIC_URL=http://studybox.lan:8080 docker compose up
```

The OAuth callback ports (54545 for Anthropic, 1455 for OpenAI) are always published on `127.0.0.1` only. A browser on another machine cannot reach them, so sign in from there with the paste path described below.

## Provider sign-in

Open **Providers**. Each provider card shows its policy notice, its models, and its connected accounts. You can connect several accounts per provider; the **Priority** field orders them (lower is tried first), and the **Enabled** switch turns a provider off without deleting its accounts. The **Routing** card picks the provider and model per kind of work.

### Anthropic (Claude Pro/Max)

The registry states:

> Anthropic's terms do not permit third-party applications to offer Claude.ai login or to route requests through Free, Pro or Max credentials, and Anthropic may enforce without notice. The account you connect here is yours and bears that risk.

1. Click **Connect** on the Anthropic card.
2. Click **Open sign-in page** and approve access on claude.ai.
3. If your browser runs on the Docker host, the redirect to `http://localhost:54545/callback` completes the sign-in by itself (the dialog shows "Listening for the callback on localhost:54545").
4. Otherwise, paste the final redirect URL (or the code Anthropic shows) into **Redirect URL or code** and click **Complete sign-in**.

### OpenAI Codex (ChatGPT Plus/Pro)

The registry states:

> OpenAI documents ChatGPT sign-in for Codex surfaces only. Using it from BrainBench is outside that documented use; the account you connect here is yours and bears that risk.

1. Click **Connect** on the ChatGPT card, then **Open sign-in page** and sign in.
2. On the Docker host, the redirect to `http://localhost:1455/auth/callback` completes the sign-in. Otherwise, paste the URL of the page the browser could not load (it starts with `http://localhost:1455/auth/callback`) and click **Complete sign-in**.
3. Alternatively, click **Connect with a device code**, open the verification page shown, sign in, and enter the code. The dialog completes on its own once you approve.

Whichever path completes first wins; a sign-in left pending expires after 10 minutes. **Disconnect** deletes an account and its tokens.

## Backup, restore and the vault key

State lives in two places: the `./data` directory next to `docker-compose.yml` (vault key, uploaded PDFs, generated assets, the skill and the embedding model) and the Postgres volume. Back up both:

```sh
docker compose exec -T db pg_dump -U brainbench -d brainbench -Fc > brainbench.dump
tar czf brainbench-data.tgz data
```

On Linux the app containers create `./data` as root; use `sudo tar` there.

Restore on a stopped instance (for example after `docker compose down -v`, or on a new machine with the same release checked out):

```sh
tar xzf brainbench-data.tgz
docker compose up -d --wait db
docker compose exec -T db pg_restore -U brainbench -d brainbench --clean --if-exists --no-owner < brainbench.dump
docker compose up
```

### Vault key recovery

Provider tokens are encrypted with `./data/vault.key` (mode `0600`); the database records its SHA-256 fingerprint. If the key file is missing or does not match the fingerprint, BrainBench never generates a replacement on its own. The instance still starts, every account shows `locked`, and the providers page shows **Credential vault locked** with two ways out:

- Restore `./data/vault.key` from your backup (keep mode `0600`) and reload the page. No restart is needed.
- Click **Reset vault** and type `reset` to confirm. This deletes every stored account and generates a new key and fingerprint (`POST /api/providers/vault/reset` with `{"confirm": "reset"}`). Connect your providers again afterwards; nothing else is affected.

## Upgrade

```sh
git pull && docker compose up --build
```

The `init` service applies pending migrations before the API starts, and applies a changed skill pin. The API and the worker refuse to run against a schema revision they do not know.

## Rollback

Each release records its schema head in `infra/schema.rev`. An older release does not carry the newer migration files, so the schema is downgraded with the current release's code before the older one starts:

```sh
# 1. Stop everything that uses the database (db stays up).
docker compose stop web worker api

# 2. With the current release still checked out, downgrade to the previous release's revision.
docker compose run --rm init alembic downgrade "$(git show <previous-tag>:infra/schema.rev)"

# 3. Check out the previous release and start it.
git checkout <previous-tag>
docker compose up --build
```

`./data` is not changed by a rollback. The alternative is to restore the `./data` archive and the Postgres dump you took before upgrading (see above), then check out the previous release and run `docker compose up --build`.

## Figure skill pin

Interactive figures follow the Anatomy skill (`wheresryan22/anatomy`), vendored into `./data/skills/anatomy` at the commit named in `infra/anatomy.pin`. A pin change normally arrives with a release, so the upgrade command applies it. After editing the pin yourself, run:

```sh
docker compose up --build
```

`init` fetches the pinned commit into `./data/skills/anatomy.staging`, checks that `HEAD` equals the pin, and swaps it into place with a rename. If the fetch or the check fails, the installed skill stays in use and the `init` log says `anatomy skill not updated`. The pin must be a full 40-character commit hash.

## Budgets and limits

| Limit                  | Default                       | Meaning                                                                                                                                |
| ---------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `render_minutes_month` | 600                           | Sandbox wall-clock minutes per UTC month, counting video renders and figure Node runs.                                                 |
| `generation_jobs_day`  | 20                            | Video and figure jobs accepted per UTC day. A request for a job that is already queued or running returns that job and does not count. |
| Job concurrency        | 2                             | Jobs running at once across the instance (`JOB_CONCURRENCY`).                                                                          |
| Per-type concurrency   | 1 video and 1 figure          | At most one render and one figure session at a time (`RENDER_CONCURRENCY`).                                                            |
| Tutor                  | 1 reply                       | One tutor reply in flight per instance.                                                                                                |
| Video render           | 15 min per render, 3 attempts | `RENDER_TIMEOUT_S`, `RENDER_ATTEMPTS`; 2 CPUs and 4 GB per sandbox.                                                                    |
| Figure session         | 60 turns, 30 min              | `FIGURE_MAX_TURNS`, `FIGURE_WALL_CLOCK_S`; 180 s per Node call (`FIGURE_NODE_TIMEOUT_S`), 2 CPUs and 2 GB per sandbox.                 |

When a budget is used up, new generation requests are refused with the reset time, and queued video and figure jobs wait until the budget allows them. The topic page shows what is left. Generating visuals for a whole plan asks for confirmation with the job count first. Failed jobs retry up to three attempts (after 30 s, then 60 s); a provider rate limit defers the job until the provider's reset time without using an attempt.

The two budgets are stored in the database. To change them:

```sh
docker compose exec db psql -U brainbench -d brainbench \
  -c "update settings set render_minutes_month = 900, generation_jobs_day = 40"
```

The other limits are process settings read from the environment. Set them for the `api` and `worker` services in a `docker-compose.override.yml`, which Compose loads automatically:

```yaml
services:
  api:
    environment:
      FIGURE_MAX_TURNS: "40"
  worker:
    environment:
      FIGURE_MAX_TURNS: "40"
```

## Development

Tools: `uv`, Bun 1.3.13, `just`, and Docker.

| Command                  | What it does                                                                                                                                                                               |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `just dev`               | Postgres in Docker on `127.0.0.1:54329`, migrations, the API with reload on `:8000` and the web dev server with hot reload on `:3000`.                                                     |
| `just test`              | Python and web unit suites. `just test-py-eager` runs the Python suite with every lazy import forced eager.                                                                                |
| `just lint`              | ruff, ruff format, mypy (strict), oxlint, prettier, `tsc`.                                                                                                                                 |
| `just integration`       | Postgres-backed tests; set `DATABASE_URL` to a pgvector database.                                                                                                                          |
| `just smoke`             | Builds the app image, imports both entrypoints and runs the suite inside it, then renders the Manim fixture in the sandbox.                                                                |
| `just smoke-fresh-clone` | Clones `HEAD` into a temp dir, runs `docker compose up` with no environment, and checks bootstrap, re-runs, the locked vault and skill pin changes. Needs ports 3000, 54545 and 1455 free. |

The repository targets Python 3.15, but runs on Python 3.14 until `onnxruntime` (used by `fastembed`) publishes 3.15 wheels; `.python-version`, `requires-python` and the image's `PYTHON_VERSION` say 3.14. Python 3.14 cannot parse the PEP 810 `lazy import` keyword, so modules that defer a heavy dependency declare it with `__lazy_modules__` followed by a plain import, after the eager imports:

```python
__lazy_modules__ = {"fitz", "trafilatura"}
import fitz
import trafilatura
```

On 3.14 these imports are eager; on 3.15 they are lazy. Conventions are in `BACKEND_GOOD_PRACTICES.md` and `FRONTEND_GOOD_PRACTICES.md`.
