#!/usr/bin/env bash
# Fresh-clone test (task 10.1, self-host-deployment spec): clone the committed HEAD into a temp
# dir, `docker compose up` with no environment, and assert the bootstrap contract, idempotent
# re-runs, the locked vault on a missing key, and the staged skill pin swap (good and bad pin).
#
# Needs Docker with Compose v2, git, curl and python3 on the host, port 3000 and the OAuth
# callback ports 54545 and 1455 free, and network access (images, Anatomy skill, embedding model).
# The temp dir is under /tmp; set FRESH_CLONE_TMPDIR to another path the Docker daemon shares.
# Only committed changes are tested. The stack, its volume, the project's app and web images, the
# temp clone and its ./data are removed on exit, also on failure (the shared sandbox images stay).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
REV="$(git -C "$SRC" rev-parse HEAD)"
ID="bbfresh$(date +%s)$$"
# /tmp, not $TMPDIR: Docker Desktop for macOS does not share /var/folders by default, and a
# bind mount from an unshared path silently lands inside its VM instead of on the host.
WORK="$(mktemp -d "${FRESH_CLONE_TMPDIR:-/tmp}/brainbench-fresh-clone.XXXXXX")"
CLONE="$WORK/$ID"
COOKIES="$WORK/cookies"
URL="http://localhost:3000"
READY_LINE="BrainBench ready at http://localhost:3000"
PASSWORD="fresh-clone-$ID"
HELPER_IMAGE="pgvector/pgvector:pg16"
PASSED=0

# "With no environment": nothing the compose file reads may leak in from the caller's shell.
unset BRAINBENCH_PORT BRAINBENCH_BIND BRAINBENCH_PUBLIC_URL BRAINBENCH_DATA_HOST \
  BRAINBENCH_OAUTH_CALLBACKS COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_ENV_FILES

step() { printf '\n== %s\n' "$*"; }
info() { printf '      %s\n' "$*"; }
pass() {
  PASSED=$((PASSED + 1))
  printf 'PASS  %s\n' "$*"
}
fail() {
  printf 'FAIL  %s\n' "$*" >&2
  exit 1
}

dc() { (cd "$CLONE" && docker compose "$@"); }
# Run a command as root over ./data: on Linux the app containers create it root-owned.
in_data() { docker run --rm -v "$CLONE/data:/data" --entrypoint "$1" "$HELPER_IMAGE" "${@:2}"; }
psql_value() { dc exec -T db psql -U brainbench -d brainbench -tAc "$1" | tr -d '[:space:]'; }
json() { python3 -c "import json, sys; d = json.load(sys.stdin); print($1)"; }
file_mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
inode() { ls -di "$1" | awk '{print $1}'; }
container_id() { dc ps -a -q "$1"; }
mark() {
  sleep 1
  date -u +%Y-%m-%dT%H:%M:%SZ
}
init_log() { dc logs --no-color --no-log-prefix --since "$1" init; }

cleanup() {
  local status=$?
  set +e
  if [ -f "$CLONE/docker-compose.yml" ]; then
    if [ "$status" -ne 0 ]; then
      printf '\n---- failure diagnostics\n'
      dc ps -a
      dc logs --no-color --tail 80
    fi
    dc down -v --remove-orphans >/dev/null 2>&1
    # Only this project's images: `--rmi local` would also delete brainbench/manim and
    # brainbench/anatomy, the fixed sandbox image names every checkout on this daemon shares.
    docker image rm "$ID-init" "$ID-api" "$ID-worker" "$ID-web" >/dev/null 2>&1
    if [ -d "$CLONE/data" ]; then
      docker run --rm -v "$WORK:/w" --entrypoint rm "$HELPER_IMAGE" -rf "/w/$ID/data" >/dev/null 2>&1
    fi
  fi
  rm -rf "$WORK"
  if [ "$status" -eq 0 ]; then
    printf '\nfresh-clone test passed: %d checks on %s\n' "$PASSED" "$REV"
  else
    printf '\nfresh-clone test FAILED after %d passing checks on %s\n' "$PASSED" "$REV" >&2
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

http_code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

wait_for() {
  local what=$1 timeout=$2
  shift 2
  local deadline=$((SECONDS + timeout))
  until "$@"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "$what (waited ${timeout} s)"
    sleep 2
  done
}

ready_logged() { dc logs --no-color --no-log-prefix web 2>/dev/null | grep -qF "$READY_LINE"; }
healthy() { [ "$(http_code "$URL/api/health")" = 200 ]; }
probe_logged() { dc logs --no-color --no-log-prefix worker 2>/dev/null | grep -q "sandbox probe passed"; }

login() {
  local code
  code=$(http_code -c "$COOKIES" -H 'Content-Type: application/json' \
    -d "{\"password\": \"$PASSWORD\"}" "$URL/api/auth/login")
  [ "$code" = 200 ] || fail "login with the instance password returned $code"
}

api() { curl -sf -b "$COOKIES" "$@"; }

assert_init_ok() {
  local cid code status
  cid=$(container_id init)
  code=$(docker inspect -f '{{.State.ExitCode}}' "$cid")
  status=$(docker inspect -f '{{.State.Status}}' "$cid")
  [ "$status" = exited ] && [ "$code" = 0 ] || fail "init exited 0 (status $status, exit code $code)"
  pass "init exited 0"
}

assert_log() {
  local log=$1 pattern=$2 what=$3
  grep -qF -- "$pattern" <<<"$log" || {
    printf '%s\n' "$log" >&2
    fail "$what: init log has no line with '$pattern'"
  }
  pass "$what (init log: '$pattern')"
}

assert_no_log() {
  local log=$1 pattern=$2 what=$3
  if grep -qF -- "$pattern" <<<"$log"; then
    printf '%s\n' "$log" >&2
    fail "$what: init log unexpectedly has '$pattern'"
  fi
  pass "$what (init log has no '$pattern')"
}

KEY_HASH="" FINGERPRINT="" SKILL_INODE="" PASSWORD_HASH="" SOURCE_ID="" CHUNKS=""

assert_data_intact() {
  local when=$1
  login
  pass "$when: the instance password set before still signs in"
  local hash
  hash=$(psql_value "select password_hash from settings")
  [ "$hash" = "$PASSWORD_HASH" ] || fail "$when: settings.password_hash changed"
  pass "$when: settings.password_hash unchanged"
  local status chunks
  status=$(api "$URL/api/sources" | json "[s['status'] for s in d if s['id'] == '$SOURCE_ID']")
  [ "$status" = "['ready']" ] || fail "$when: note source $SOURCE_ID is $status, expected ['ready']"
  chunks=$(psql_value "select count(*) from chunks where source_id = '$SOURCE_ID'")
  [ "$chunks" = "$CHUNKS" ] || fail "$when: chunks for the note: $chunks, expected $CHUNKS"
  pass "$when: note source $SOURCE_ID still ready with $chunks chunk(s)"
}

# ---------------------------------------------------------------------------------------------
step "Preflight"
for tool in docker git curl python3; do
  command -v "$tool" >/dev/null || fail "$tool is required"
done
docker compose version >/dev/null || fail "docker compose v2 is required"
for port in 3000 54545 1455; do
  if port_busy "$port"; then fail "port $port is in use; stop whatever listens there first"; fi
done
pass "docker, compose, git, curl, python3 present; ports 3000, 54545, 1455 free"
if [ -n "$(git -C "$SRC" status --porcelain)" ]; then
  info "note: uncommitted changes in $SRC are not part of the clone"
fi

step "Clone $REV into $CLONE"
git clone --quiet "$SRC" "$CLONE"
mkdir "$CLONE/.probe" && docker run --rm -v "$CLONE/.probe:/p" --entrypoint touch "$HELPER_IMAGE" /p/x >/dev/null 2>&1 &&
  [ -e "$CLONE/.probe/x" ] || fail "a bind mount of $CLONE does not reach the host; set FRESH_CLONE_TMPDIR to a path Docker shares"
docker run --rm -v "$CLONE:/w" --entrypoint rm "$HELPER_IMAGE" -rf /w/.probe >/dev/null 2>&1
git -C "$CLONE" checkout --quiet --detach "$REV"
[ "$(git -C "$CLONE" rev-parse HEAD)" = "$REV" ] || fail "clone is not at $REV"
[ ! -e "$CLONE/data" ] && [ ! -e "$CLONE/.env" ] || fail "fresh clone already has ./data or .env"
pass "fresh clone at $REV, no ./data and no .env"
PIN=$(tr -d '[:space:]' <"$CLONE/infra/anatomy.pin")
info "anatomy pin: $PIN"

# ---------------------------------------------------------------------------------------------
step "First docker compose up -d --build (no environment)"
dc up -d --build --quiet-pull
wait_for "web never logged '$READY_LINE'" 600 ready_logged
pass "web logged '$READY_LINE'"

body=$(curl -sf -D "$WORK/headers" "$URL/")
grep -qi '^content-type: text/html' "$WORK/headers" || fail "GET / is not text/html"
grep -q '<div id="root">' <<<"$body" || fail "GET / does not contain the SPA root"
pass "GET / answers 200 text/html with the SPA root"
me=$(curl -sf "$URL/api/auth/me")
[ "$(json "d['password_set']" <<<"$me")" = False ] || fail "fresh instance already has a password: $me"
pass "GET /api/auth/me on the fresh instance: $me"
dom=$(docker run --rm --network "${ID}_default" --entrypoint chromium brainbench/anatomy \
  --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --virtual-time-budget=20000 --dump-dom http://web:3000/ 2>/dev/null)
grep -q "Welcome" <<<"$dom" && grep -q "Set password and continue" <<<"$dom" ||
  fail "headless Chromium did not render the wizard at /"
pass "headless Chromium renders the first-run wizard at / ('Welcome', 'Set password and continue')"

KEY="$CLONE/data/vault.key"
[ -f "$KEY" ] || fail "./data/vault.key does not exist"
mode=$(file_mode "$KEY")
[ "$mode" = 600 ] || fail "./data/vault.key has mode $mode, expected 600"
pass "./data/vault.key exists with mode 0600"
KEY_HASH=$(in_data sha256sum /data/vault.key | awk '{print $1}')
FINGERPRINT=$(psql_value "select vault_fingerprint from settings")
[ -n "$FINGERPRINT" ] && [ "$KEY_HASH" = "$FINGERPRINT" ] ||
  fail "sha256(vault.key)=$KEY_HASH but settings.vault_fingerprint=$FINGERPRINT"
pass "sha256(./data/vault.key) equals settings.vault_fingerprint ($FINGERPRINT)"

COMMIT_FILE="$CLONE/data/skills/anatomy/.commit"
commit=$(tr -d '[:space:]' <"$COMMIT_FILE")
[ "$commit" = "$PIN" ] || fail "./data/skills/anatomy/.commit is $commit, pin is $PIN"
pass "./data/skills/anatomy/.commit equals infra/anatomy.pin ($PIN)"
SKILL_INODE=$(inode "$CLONE/data/skills/anatomy")
for dir in jobs assets models skills; do
  [ -d "$CLONE/data/$dir" ] || fail "./data/$dir was not created"
done
ls "$CLONE"/data/models/.ready-* >/dev/null 2>&1 || fail "embedding model marker missing in ./data/models"
pass "./data/{jobs,assets,models,skills} exist and the embedding model is downloaded"

for image in brainbench/manim brainbench/anatomy; do
  docker image inspect "$image" >/dev/null 2>&1 || fail "image $image is not on the daemon"
  pass "image $image exists on the daemon ($(docker image inspect -f '{{.Id}}' "$image" | cut -c1-19))"
done
for svc in sandbox-manim sandbox-anatomy; do
  code=$(docker inspect -f '{{.State.ExitCode}}' "$(container_id "$svc")")
  [ "$code" = 0 ] || fail "$svc exited $code"
  pass "build-only service $svc exited 0"
done

assert_init_ok
db_ready=$(dc logs --no-color --no-log-prefix --timestamps db |
  grep 'database system is ready to accept connections' | tail -n 1 | awk '{print $1}')
init_started=$(docker inspect -f '{{.State.StartedAt}}' "$(container_id init)")
python3 - "$db_ready" "$init_started" <<'PY' || fail "init started before db was ready"
import re, sys
def key(ts):
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", ts)
    return m.group(1), (m.group(2) or "").ljust(9, "0")
sys.exit(0 if key(sys.argv[1]) < key(sys.argv[2]) else 1)
PY
pass "init started ($init_started) after db accepted connections ($db_ready)"
first_log=$(dc logs --no-color --no-log-prefix init)
assert_log "$first_log" "schema migrated from empty to $(cat "$CLONE/infra/schema.rev")" "first run migrated"
assert_log "$first_log" "vault key generated" "first run generated the vault key"
assert_log "$first_log" "anatomy skill installed at $PIN" "first run installed the skill"
assert_log "$first_log" "bootstrap complete" "bootstrap completed"

wait_for "worker never logged 'sandbox probe passed'" 180 probe_logged
pass "worker log: $(dc logs --no-color --no-log-prefix worker | grep -m1 'sandbox probe passed' | sed 's/^.*\(sandbox probe passed.*\)$/\1/')"

step "Seed data: instance password and one ingested note"
code=$(http_code -c "$COOKIES" -H 'Content-Type: application/json' \
  -d "{\"password\": \"$PASSWORD\"}" "$URL/api/auth/setup")
[ "$code" = 201 ] || fail "POST /api/auth/setup returned $code"
pass "POST /api/auth/setup 201"
PASSWORD_HASH=$(psql_value "select password_hash from settings")
SOURCE_ID=$(api -H 'Content-Type: application/json' \
  -d '{"type": "note", "title": "Fresh clone note", "text": "Raft elects a leader by majority vote. The leader replicates its log to followers."}' \
  "$URL/api/sources" | json "d['id']")
note_ready() {
  [ "$(api "$URL/api/sources" | json "[s['status'] for s in d if s['id'] == '$SOURCE_ID'][0]")" = ready ]
}
wait_for "note source $SOURCE_ID never became ready" 300 note_ready
CHUNKS=$(psql_value "select count(*) from chunks where source_id = '$SOURCE_ID'")
[ "$CHUNKS" -ge 1 ] || fail "note source has no chunks"
pass "note source $SOURCE_ID ingested by the worker: ready, $CHUNKS chunk(s)"

# ---------------------------------------------------------------------------------------------
step "Second docker compose up -d (same release, same data)"
since=$(mark)
dc up -d
wait_for "API not healthy through the web proxy" 180 healthy
assert_init_ok
log=$(init_log "$since")
assert_log "$log" "no migration ran" "re-run: no migration ran"
assert_log "$log" "vault key ok" "re-run: vault key verified, not regenerated"
assert_log "$log" "anatomy skill unchanged" "re-run: skill unchanged"
[ "$(in_data sha256sum /data/vault.key | awk '{print $1}')" = "$KEY_HASH" ] || fail "vault.key changed"
[ "$(psql_value "select vault_fingerprint from settings")" = "$FINGERPRINT" ] || fail "fingerprint changed"
pass "re-run: vault.key hash and recorded fingerprint unchanged"
[ "$(tr -d '[:space:]' <"$COMMIT_FILE")" = "$PIN" ] && [ "$(inode "$CLONE/data/skills/anatomy")" = "$SKILL_INODE" ] ||
  fail "skill directory changed on re-run"
pass "re-run: skill .commit and directory unchanged"
assert_data_intact "re-run"

# ---------------------------------------------------------------------------------------------
step "Remove ./data/vault.key, docker compose up -d"
in_data rm /data/vault.key
[ ! -e "$KEY" ] || fail "could not remove ./data/vault.key"
since=$(mark)
dc up -d
wait_for "API not healthy through the web proxy" 180 healthy
assert_init_ok
log=$(init_log "$since")
assert_log "$log" "vault is locked (locked-missing)" "missing key: init reports the vault locked"
assert_log "$log" "no key was generated" "missing key: init generated no key"
[ ! -e "$KEY" ] || fail "a new ./data/vault.key was generated"
[ "$(psql_value "select vault_fingerprint from settings")" = "$FINGERPRINT" ] || fail "fingerprint changed"
pass "missing key: no ./data/vault.key created, recorded fingerprint unchanged"
login
vault=$(api "$URL/api/providers" | json "json.dumps(d['vault'])")
[ "$(json "' '.join([d['state'], d['detail'], d['key_path']])" <<<"$vault")" = \
  "locked locked-missing /data/vault.key" ] || fail "GET /api/providers vault is $vault"
pass "GET /api/providers reports the vault locked: $vault"
reset=$(curl -s -b "$COOKIES" -H 'Content-Type: application/json' -d '{"confirm": ""}' \
  -w ' %{http_code}' "$URL/api/providers/vault/reset")
grep -q '"confirmation-required"' <<<"$reset" && [ "${reset##* }" = 400 ] ||
  fail "unconfirmed vault reset answered: $reset"
pass "recovery: restore the key at /data/vault.key, or reset; an unconfirmed reset is refused (400 confirmation-required)"
assert_data_intact "missing key"

# ---------------------------------------------------------------------------------------------
step "Change infra/anatomy.pin to another commit, docker compose up -d --build"
REPO_URL=$(sed -n 's/.*anatomy_repo_url: str = "\(.*\)"/\1/p' "$CLONE/apps/api/app/core/config.py")
git init -q "$WORK/pin"
git -C "$WORK/pin" fetch -q --depth 2 "$REPO_URL" "$PIN"
NEW_PIN=$(git -C "$WORK/pin" rev-parse FETCH_HEAD~1)
info "new pin: $NEW_PIN (parent of $PIN in $REPO_URL)"
printf '%s\n' "$NEW_PIN" >"$CLONE/infra/anatomy.pin"
since=$(mark)
dc up -d --build --quiet-pull
wait_for "API not healthy through the web proxy" 180 healthy
assert_init_ok
log=$(init_log "$since")
assert_log "$log" "anatomy skill installed at $NEW_PIN" "pin change: init installed the new pin"
[ "$(tr -d '[:space:]' <"$COMMIT_FILE")" = "$NEW_PIN" ] || fail ".commit is not the new pin"
pass "pin change: ./data/skills/anatomy/.commit is $NEW_PIN"
new_inode=$(inode "$CLONE/data/skills/anatomy")
[ "$new_inode" != "$SKILL_INODE" ] || fail "skill directory was modified in place, not swapped"
[ ! -e "$CLONE/data/skills/anatomy.staging" ] && [ ! -e "$CLONE/data/skills/anatomy.old" ] ||
  fail "staging or old skill directory left behind"
[ -f "$CLONE/data/skills/anatomy/SKILL.md" ] || fail "swapped skill has no SKILL.md"
pass "pin change: swapped by rename (directory inode $SKILL_INODE -> $new_inode), no anatomy.staging or anatomy.old left, SKILL.md present"
SKILL_INODE=$new_inode
assert_data_intact "pin change"

step "Deliberately bad pin, docker compose up -d --build"
BAD_PIN=deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
printf '%s\n' "$BAD_PIN" >"$CLONE/infra/anatomy.pin"
since=$(mark)
dc up -d --build --quiet-pull
wait_for "API not healthy through the web proxy" 180 healthy
assert_init_ok
log=$(init_log "$since")
assert_log "$log" "anatomy skill not updated (installed: $NEW_PIN)" "bad pin: init logged the failure and kept the installed skill"
assert_no_log "$log" "anatomy skill installed" "bad pin: nothing installed"
[ "$(tr -d '[:space:]' <"$COMMIT_FILE")" = "$NEW_PIN" ] && [ "$(inode "$CLONE/data/skills/anatomy")" = "$SKILL_INODE" ] ||
  fail "bad pin changed the installed skill"
[ ! -e "$CLONE/data/skills/anatomy.staging" ] && [ ! -e "$CLONE/data/skills/anatomy.old" ] ||
  fail "bad pin left a staging or old directory"
pass "bad pin: previous skill $NEW_PIN still in place (same directory), no staging left"
assert_data_intact "bad pin"
[ "$(http_code "$URL/")" = 200 ] || fail "UI not served after the bad pin"
pass "bad pin: the instance still serves the UI"

step "Teardown (docker compose down -v, temp clone removed)"
