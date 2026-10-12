## Purpose

Lets an engineer go from a fresh clone to a working BrainBench in the browser with a single `docker compose up`, with no manual setup steps, environment files, or extra scripts required for the default configuration.

## ADDED Requirements

### Requirement: One command brings up a working instance
The system SHALL start fully from `docker compose up` on a host with only Docker installed: it SHALL build or pull every image it needs (including the render and figure sandbox images used by the worker), create the data directory, generate the vault key, run database migrations, vendor the figure skill at its pinned commit, and reach a ready state without any prior command, environment variable, or `.env` file.

#### Scenario: Fresh clone to ready
- **WHEN** an engineer runs `docker compose up` in a fresh clone with no environment variables set
- **THEN** every long-running service (database, API, worker, web) reaches healthy, every one-shot service (bootstrap, sandbox image builds) exits successfully, the data directory exists with the vault key and the vendored skill, migrations are applied, and the web UI answers at the announced URL

#### Scenario: Sandbox images available to the worker
- **WHEN** the stack is up
- **THEN** the worker can start a Manim sandbox and a figure sandbox without the engineer building or pulling any image by hand

#### Scenario: Re-running is idempotent
- **WHEN** `docker compose up` is run again on an existing data directory with the same release
- **THEN** no data is lost, the vault key and the vendored skill are unchanged, no migration runs, and the instance is ready

#### Scenario: Missing vault key on an existing instance is not replaced
- **WHEN** `docker compose up` runs on an instance whose database records a vault fingerprint but whose key file is missing
- **THEN** bootstrap does not generate a key, the stack still starts, and the providers page shows the vault as locked with recovery options

#### Scenario: Skill pin upgrade is staged and atomic
- **WHEN** a release changes the pinned skill commit and `docker compose up --build` runs on an existing data directory
- **THEN** the new commit is fetched into a staging directory, verified, and swapped into place in one rename, and a failed fetch or verification leaves the previously installed skill in use

### Requirement: The access URL is announced
The system SHALL log a single line `BrainBench ready at http://localhost:<port>` from the web service once the API is reachable, SHALL default the port to 3000, and SHALL allow overriding the port and bind address with `BRAINBENCH_PORT` and `BRAINBENCH_BIND`; the default bind is the host loopback only.

#### Scenario: Default URL
- **WHEN** the stack starts with defaults
- **THEN** the log shows `BrainBench ready at http://localhost:3000` and that URL serves the UI

#### Scenario: LAN exposure is opt-in
- **WHEN** the stack starts with defaults
- **THEN** the UI is not reachable from another machine on the network until `BRAINBENCH_BIND=0.0.0.0` is set

### Requirement: First run completes in the browser
The system SHALL present a first-run wizard at the announced URL when no instance password exists, asking only for the password, and SHALL then land the engineer on the providers page; no setup step requires a terminal.

#### Scenario: First visit
- **WHEN** the engineer opens the URL on a fresh instance
- **THEN** the wizard asks for an instance password, stores it, signs the engineer in, and shows the providers page with Connect actions

### Requirement: Worker discovers sandbox mount paths itself
The system SHALL resolve the host path of its data directory at worker start by inspecting its own container through the Docker socket, and SHALL only require a manual override when that inspection fails.

#### Scenario: Default data directory
- **WHEN** the worker starts with the default compose file
- **THEN** it resolves the host path of the data directory without configuration and its sandbox probe succeeds

#### Scenario: Inspection unavailable
- **WHEN** the worker cannot inspect its own container
- **THEN** it stops with a message naming the `BRAINBENCH_DATA_HOST` override instead of running jobs with broken mounts

### Requirement: Upgrades are a pull and an up
The system SHALL apply schema migrations in the bootstrap step, before the API starts, so that upgrading is `git pull` followed by `docker compose up --build`, and SHALL refuse to start the API or the worker against a schema revision they do not know.

#### Scenario: Upgrade applies migrations
- **WHEN** a newer version is started on an existing database
- **THEN** pending migrations run in bootstrap before the API accepts requests and the data directory is unchanged apart from a staged skill upgrade when the pin changed

#### Scenario: Rollback to a previous release
- **WHEN** the engineer follows the documented rollback (stop web and worker, downgrade the schema to the previous release's revision using the current release's migration code, then start the previous release)
- **THEN** the previous release starts against the downgraded schema and the data directory is intact

#### Scenario: Old worker against new schema
- **WHEN** a worker older than the applied schema starts
- **THEN** it exits with a message naming the schema version mismatch
