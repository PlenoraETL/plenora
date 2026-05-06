# Marco's first-PC clean-install dry-run pack

`BE-GO-READY-INSTALLATION-024-LINUX-CLEAN-INSTALL-DRY-RUN-PACK`.

Companion to [install-non-docker.md](install-non-docker.md). One
page, copy/paste-ready. The full reference (decision matrix,
rollback details, gotchas) lives in
[install-non-docker.md section 13](install-non-docker.md#13-operator-quick-reference-checklist-real-linux-pc-test);
this page is the executable subset for **the first real PC test
on a fresh Ubuntu/Linux host with Docker already installed**.

> **Docker status (honest, NOT the go-ready path).** Marco's PC
> has Docker installed. Plenora-ETL now ships a **FIRST SCAFFOLD**
> `Dockerfile` and `docker-compose.yml`
> (`BE-GO-READY-INSTALL-011`), but the scaffold is **NOT YET
> VALIDATED end-to-end on a real Linux PC** and there is no Helm
> chart. An operator who runs `docker compose up` against the
> scaffold is testing the scaffold, NOT following the go-ready
> path. See [install-docker.md](install-docker.md) for the
> scaffold runbook (build / up / health / cleanroom.ping /
> non-destructive stop) and the explicit limitations.
> **The only go-ready path for the first PC test is this one**,
> which does not use Docker at all.

---

## 1. Manual choices Marco must make BEFORE running anything

Resolve each one. Defaults in **bold** are the recommended
first-PC-test values.

| Choice | Options | First-PC-test value |
|---|---|---|
| Operating system | Ubuntu 22.04 / 24.04 / Debian 12 | **Ubuntu 24.04** (reference; the apt one-liner targets it) |
| `ENVIRONMENT` | `development` / `staging` / `production` | **`development`** |
| Postgres location | local on the same PC OR managed (RDS/Cloud SQL) | **local on the same PC** |
| Redis location | local on the same PC OR managed | **local on the same PC** |
| Object storage | (a) disabled (`STORAGE_POLICY=db`) (b) local FS NOT IMPLEMENTED (c) MinIO (d) AWS | **(a) disabled** - no buckets, no IAM, no MinIO server |
| Backup bucket | required-non-empty even on `db` | **`plenora-pc-test-no-real-s3`** (literal dummy string) |
| Backend port | 1024-65535 | **5004** |
| Worker count | uvicorn `--workers`, celery `--concurrency` | **uvicorn 1, celery 1 (foreground); systemd later if green** |
| Application user | dedicated `ubuntu` user OR your own user | **your own user** for first PC test (skip the `adduser` step) |

`ENVIRONMENT=development` matters: it disables the `dotenv`
ignore for `backend/.env`, allows the WSL-Redis fallback
(harmless on Linux because gating also checks
`sys.platform.startswith("win")`), and skips the
hardened-env fail-fast on default secrets. For a first PC
test this is the right setting.

---

## 2. Exact command sequence (copy/paste-ready)

The repo path below assumes Marco clones into his home
directory. Adjust if you prefer `/opt/plenora-etl` or
`/home/ubuntu/plenora-etl` (the systemd unit defaults expect
the latter).

### 2.1 Step 1 - host packages

```bash
sudo apt update
sudo apt install -y \
  python3.11 python3.11-venv python3.11-dev \
  build-essential libpq-dev \
  postgresql-17 postgresql-client-17 \
  libgdal-dev libmagic1 \
  redis-server \
  curl ca-certificates \
  nodejs npm \
  git
```

The `postgresql-17` server package is the local PG. If
Ubuntu 24.04 default is PG 16, either keep PG 16 (and use
`postgresql-client-16`) or add the PGDG apt repo for PG 17.
The schema autocreate path supports both.

### 2.2 Step 2 - local Postgres role + DB

```bash
sudo -iu postgres psql <<'SQL'
CREATE USER plenora WITH PASSWORD 'plenora' CREATEDB;
CREATE DATABASE plenora OWNER plenora;
SQL
```

Connectivity probe (must return `1`):

```bash
psql "postgresql://plenora:plenora@127.0.0.1:5432/plenora" -c "SELECT 1"
```

Do NOT pre-create the `plenora` schema. The runner creates it
at first boot via `migrations_runner.ensure_schema(DB_SCHEMA)`.

### 2.3 Step 3 - local Redis

```bash
sudo systemctl enable --now redis-server
redis-cli PING   # expect: PONG
```

If Marco's PC already runs another service on `:6379`, use a
different port and update `CELERY_BROKER_URL` accordingly.

### 2.4 Step 4 - clone + preflight

```bash
cd ~
git clone <repo-url> plenora-etl
cd plenora-etl

# Preflight (no writes, no service probes that need creds)
bash scripts/install.sh --check
```

`--check` exits 0 only when ALL host prereqs pass:
`python3.11` (3.11.x), `node`, `npm`, `psql`, `redis-cli`,
`curl`, `git`, plus the `gdal` shared library probed via
`ctypes.util.find_library`. Any FAIL line lists the missing
piece - go back to step 2.1.

### 2.5 Step 5 - guided install (writes `backend/.env`)

```bash
PLENORA_INSTALL_ENVIRONMENT=development \
PLENORA_INSTALL_DATABASE_URL='postgresql://plenora:plenora@127.0.0.1:5432/plenora' \
PLENORA_INSTALL_DB_SCHEMA=plenora \
PLENORA_INSTALL_REDIS_URL=redis://127.0.0.1:6379/0 \
PLENORA_INSTALL_STORAGE_POLICY=db \
PLENORA_INSTALL_BACKUP_BUCKET=plenora-pc-test-no-real-s3 \
PLENORA_INSTALL_BACKEND_PORT=5004 \
bash scripts/install.sh --non-interactive
```

The installer:

- validates host prereqs (same as `--check`);
- generates `PLENORA_ENCRYPTION_KEY` and `JWT_SECRET_KEY`
  via `secrets.token_urlsafe(48)`;
- probes Postgres (`SELECT 1`) and Redis (`PING`);
- writes `backend/.env` atomically with `chmod 600`.

Rerun is idempotent: operator-added non-installer-managed
keys are preserved (per `INSTALLATION-012`). To rotate the
secrets, add `--overwrite-secrets` and confirm the prompt.

### 2.6 Step 6 - venv + Python deps (the installer does NOT do this)

```bash
cd backend
python3.11 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

Do NOT install `requirements-dev.txt` for the smoke. If
something at runtime imports a dev dep, that is a bug worth
catching here.

### 2.7 Step 7 - foreground smoke (NOT systemd yet)

Three terminals. The first run must be foreground so Marco
sees the four boot gate logs in real time.

**Terminal 1 - API**:

```bash
cd ~/plenora-etl/backend
./venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port 5004 --workers 1
```

Expected on success: a few seconds of bootstrap log lines,
then `INFO: Uvicorn running on http://127.0.0.1:5004`.

**Terminal 2 - Celery worker**:

```bash
cd ~/plenora-etl/backend
./venv/bin/celery -A workers.celery_app worker \
    --pool=solo --loglevel=info
```

Expected on success: `celery@<host> ready.` after the worker
bootstrap sequence (it re-runs `app.bootstrap.bootstrap()`
via `worker_init` signal, same four gates as the API).

**Terminal 3 - Celery beat**:

```bash
cd ~/plenora-etl/backend
./venv/bin/celery -A workers.celery_app beat \
    -S workers.beat_scheduler.PlenoraBeatScheduler --loglevel=info
```

Expected on success:
`PlenoraBeatScheduler avviato (schedule da Postgres, ...)`
followed by tick logs every ~60 seconds.

### 2.8 Step 8 - smoke `/health` and `/ready`

In a fourth terminal:

```bash
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
```

Expected high-level shape (NOT byte-exact - field order and
detail strings can drift; the contract is the keys and the
boolean / "ok" markers):

`/health` (HTTP 200):

```json
{
  "status": "ok",
  "checks": {
    "postgres": {"ok": true, ...},
    "redis":    {"ok": true, ...}
  }
}
```

`/ready` (HTTP 200) - 6 checks, all must report ok:

```json
{
  "status": "ok",
  "checks": {
    "postgres":               {"ok": true, ...},
    "redis":                  {"ok": true, ...},
    "pool_headroom":          {"ok": true, ...},
    "migrations_aligned":     {"ok": true, ...},
    "object_storage":         {"ok": true, "detail": "skipped: STORAGE_POLICY=db"},
    "reconciliation_backlog": {"ok": true, "detail": "pending=0 lost=0"}
  }
}
```

Notes on the expected body:

- `object_storage` reports `skipped: STORAGE_POLICY=db` on
  the first PC test because the bucket path is intentionally
  not exercised. This is the success state for `db` policy.
- `reconciliation_backlog` reports `pending=0 lost=0` on a
  fresh DB. The threshold-based 503 from
  `INSTALLATION-015` only triggers when the counts cross
  configured caps (default `pending>=1000` or `lost>=10`).
- Migrations applied: 18 at the time of writing. The post-
  smoke check `psql ... SELECT count(*) FROM
  plenora.schema_migrations` should return `18`.

If `/ready` returns `503`, the body lists which check failed
and the failure reason. Take that to the triage checklist
below.

### 2.9 Step 9 - if green, install systemd units (persistent)

Only after `/health` AND `/ready` are both 200 in foreground:

```bash
cd ~/plenora-etl/backend/deploy/systemd
# If the install path differs from /home/ubuntu/plenora-etl,
# edit User= / WorkingDirectory= / EnvironmentFile= /
# ExecStart= in EACH unit BEFORE the cp.
sudo cp \
  plenora-backend.service \
  plenora-celery.service \
  plenora-beat.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
  plenora-backend.service \
  plenora-celery.service \
  plenora-beat.service
sudo systemctl is-active \
  plenora-backend.service plenora-celery.service plenora-beat.service
# expect: active (x3)
```

The backup unit + `.timer` + the weekly restore drill are
optional for a first PC test - skip them.

---

## 3. Native dependency verification

`BE-GO-READY-INSTALLATION-025-LINUX-NATIVE-DEPS-VERIFY`.

After step 2.1 (apt install) AND step 2.6 (venv +
`pip install -r requirements.txt`), run the probes below to
confirm every native binary AND every Python wheel that
binds to a native shared library is actually importable on
the host. Each probe prints a version string on success and
exits non-zero on failure - they are safe to copy/paste in
any order.

> **Distro target**: the apt one-liner in step 2.1 is
> Ubuntu 24.04 specific. Ubuntu 22.04 / Debian 12 use the
> same package names with possibly different major
> versions for `postgresql-*`. Fedora / RHEL use different
> package names entirely (`gdal-devel`, `postgresql-devel`,
> etc.) - those are NOT in scope for this dry-run pack;
> stick to Ubuntu/Debian for the first PC test.

### 3.1 OS binaries (run after step 2.1)

```bash
# Python
python3.11 --version
# expect: Python 3.11.x

# Postgres (server + client)
pg_config --version
# expect: PostgreSQL 17.x (or 16.x if Ubuntu default is kept)
psql --version
# expect: psql (PostgreSQL) 17.x (matched to server major)

# Redis (server + client)
redis-server --version
# expect: Redis server v=7.x.x ... (or 6.x; both work)
redis-cli --version
# expect: redis-cli 7.x.x

# GDAL (geo stack used by fiona / geopandas via ctypes)
gdalinfo --version
# expect: GDAL 3.x.x ...

# Node + npm (frontend build)
node --version
# expect: v20.x or v22.x (LTS active line; package.json does
# NOT pin engines, the LTS is the operational baseline)
npm --version

# Other CLI prereqs
curl --version | head -n 1
git --version
```

If `gdalinfo` is missing on a host that supposedly installed
`libgdal-dev`, the most common cause is a partial apt
install: re-run the apt one-liner in step 2.1 with `-y` AND
without any `--no-install-recommends` flag.

### 3.2 Python imports inside the venv (run after step 2.6)

This probe is the same surface that
`scripts/install.sh --check` runs in `step_validate_packaging`
when `backend/venv/` already exists. Running it manually
post-install gives Marco an explicit signal that every wheel
binding to a native shared library actually loads:

```bash
cd ~/plenora-etl/backend
./venv/bin/python - <<'PYEOF'
import importlib
import sys

modules = (
    # Web stack
    "fastapi", "uvicorn", "starlette", "pydantic",
    # PG client (psycopg2-binary bundles libpq)
    "psycopg2",
    # Redis client (pure-Python)
    "redis",
    # Celery + kombu
    "celery", "kombu",
    # Geo / dataframe stack (fiona binds GDAL via ctypes;
    # geopandas + shapely use it transitively)
    "fiona", "geopandas", "shapely", "pyarrow", "pandas",
    # MIME sniffing - filetype is pure-Python, NO libmagic
    # dependency. We import it explicitly so a future
    # accidental swap to python-magic surfaces here.
    "filetype",
    # Misc deps that touch the network or the FS
    "boto3", "requests", "httpx", "paramiko",
    "cryptography",
)

failures = []
for m in modules:
    try:
        importlib.import_module(m)
    except Exception as exc:
        failures.append((m, type(exc).__name__, str(exc)))

if failures:
    for m, etype, emsg in failures:
        print(f"FAIL {m}: {etype}: {emsg}")
    sys.exit(1)

print(f"OK: {len(modules)} runtime modules import cleanly")
PYEOF
```

Notes:

- **`fiona` is NOT a direct entry in `requirements.txt`** -
  it is pulled in transitively by `geopandas>=1.1.0`. If the
  probe above fails on `fiona` import after a successful
  `pip install -r requirements.txt`, the most likely cause
  is an ABI mismatch between the `geopandas` wheel and the
  `gdalinfo` major version. Fix: stick to a manylinux wheel
  (`pip install --only-binary :all: geopandas fiona`).
- **`libmagic` is intentionally NOT verified.**
  `requirements.txt:61` documents `filetype>=1.2.0` as
  "MIME sniffing pure-Python (no libmagic dep)". The apt
  one-liner in step 2.1 includes `libmagic1` as a
  precautionary inclusion only; if it is missing on the
  host, NO Plenora-ETL runtime import fails. Do NOT add a
  `libmagic` verification.
- **`psycopg2-binary` bundles libpq**: the wheel ships with
  its own libpq.so. The OS-level `psql` and `pg_config`
  checks above are for the *backup unit* (`pg_dump` /
  `pg_restore`) and for `pg_hba.conf` operator visibility,
  NOT for the application import path.

### 3.3 Service probes (run after step 2.2 + 2.3)

Already part of the command sequence (step 2.2 Postgres
probe, step 2.3 Redis probe, step 2.4 `--check` runs both).
Reproduced here as the canonical verification commands so a
post-install sanity sweep is one place to find them:

```bash
# Postgres reachable + role privileges OK
psql "postgresql://plenora:plenora@127.0.0.1:5432/plenora" \
    -c "SELECT current_user, current_database(), version();"
# expect: 1 row with user=plenora, database=plenora,
#         server version 17.x or 16.x

# Postgres role can CREATE on the DB (required by schema autocreate)
psql "postgresql://plenora:plenora@127.0.0.1:5432/plenora" \
    -c "SELECT has_database_privilege('plenora', 'CREATE');"
# expect: t

# Redis reachable
redis-cli -u redis://127.0.0.1:6379/0 PING
# expect: PONG

# Redis selected DB is writable (broker / result backend / leader-lock)
redis-cli -u redis://127.0.0.1:6379/0 SET plenora:smoke ok
redis-cli -u redis://127.0.0.1:6379/0 GET plenora:smoke
redis-cli -u redis://127.0.0.1:6379/0 DEL plenora:smoke
# expect: OK / ok / 1
```

If the `has_database_privilege` probe returns `f`, the role
will fail at `migrations_runner.ensure_schema(DB_SCHEMA)`
during the first uvicorn boot. Re-run the role creation in
step 2.2 with `CREATEDB` (the script there already does -
double-check for typos).

---

## 4. Failure triage checklist

If any step fails, check this in order. The four boot gates
log loudly: `journalctl` (or the foreground terminal output)
will say which one tripped.

### 4.1 `scripts/install.sh --check` FAIL

| Symptom | Fix |
|---|---|
| `python3.11 not found` | re-run apt install (step 2.1); on Ubuntu 24.04 it is in the default repo |
| `python3.11 reports an unexpected version` | a corrupted symlink. Run `which python3.11 && python3.11 --version` and reinstall apt |
| `psql / redis-cli / node / npm / curl / git missing` | apt install line covers them; if Ubuntu container without `apt`, install manually |
| `shared lib 'gdal' NOT found` | `sudo apt install libgdal-dev` (step 2.1 covers it) |
| `pg_dump not found` | `sudo apt install postgresql-client-NN` matched to server major; warn-only - the install proceeds |

### 4.2 `bash scripts/install.sh --non-interactive` FAIL

| Symptom | Fix |
|---|---|
| `psql SELECT 1 failed` | check `DATABASE_URL` value; check `pg_hba.conf` allows `127.0.0.1` md5 or trust; check role exists with password |
| `redis-cli PING failed` | confirm `redis-server` is running (`sudo systemctl status redis-server`); test PING manually |
| `Missing /path/.env.example` | wrong CWD - run `bash scripts/install.sh` from the repo root |
| `--check` passes but install hangs | likely an interactive prompt - re-run with `--non-interactive` and the `PLENORA_INSTALL_*` env vars |

### 4.3 Foreground uvicorn FAIL

The four boot gates fire in this order; whichever first
prints `[ERROR]`/`SystemExit` is the one to fix.

| Gate | Symptom in log | Fix |
|---|---|---|
| `validate_startup` | `DATA_DIR not writable` OR `Redis non raggiungibile` | check write permission on the working directory; check Redis with `redis-cli` |
| `validate_storage_config` | `StorageConfigError: ...` | re-check `.env` STORAGE_POLICY x BACKUP_BUCKET x OBJECT_STORAGE_*. For first PC test: `STORAGE_POLICY=db` AND `BACKUP_BUCKET` non-empty are the only requirements |
| `init_all_dbs` -> migrations | `permission denied for schema "plenora"` | role missing `CREATE` on DB; re-run the `CREATE USER ... CREATEDB` SQL from step 2.2 |
| `_repair_psycopg2_ssl_env` | rare, log self-explanatory | usually a downstream symptom of a failed `init_all_dbs` |

### 4.4 Foreground celery worker FAIL

Same four boot gates run via `worker_init` signal. Most
common new failure here:

- `kombu.exceptions.OperationalError: [Errno 111] Connection
  refused` -> Redis is down or the broker URL is wrong;
- `psycopg2.OperationalError: ...` -> Postgres unreachable
  by the worker process (separate connection from API).

### 4.5 Foreground celery beat FAIL

- `[LEADER] Redis non raggiungibile` -> Redis down. Beat
  goes to standby; tick is a no-op until Redis is back.
- `PlenoraBeatScheduler avviato (...)` but no tick after 60s
  -> standby due to leader-lock contention. Ensure no other
  beat process is running.

### 4.6 `/health` returns 200 but `/ready` returns 503

Read the failing check name from the response body:

| Failing check | Likely cause |
|---|---|
| `pool_headroom` | PG `max_connections` too low for `(uvicorn_workers + celery_concurrency + 1) * DB_POOL_MAX + 5`. Section 2 of install-non-docker.md has the formula. |
| `migrations_aligned` | a migration file in `backend/migrations/` did not apply cleanly. Inspect the foreground uvicorn log around startup. |
| `object_storage` | only fires on `STORAGE_POLICY != db`. For first PC test stay on `db`. |
| `reconciliation_backlog` | only fires when `pending` or `lost` cross threshold. Fresh DB cannot trip this. |

### 4.7 Total reset (DESTRUCTIVE - first PC test only)

If the install is completely stuck and Marco wants to start
over, this wipes EVERYTHING the install created. Do NOT run
on a real DB.

```bash
# Stop services if installed
sudo systemctl stop plenora-backend plenora-celery plenora-beat 2>/dev/null || true
sudo systemctl disable plenora-backend plenora-celery plenora-beat 2>/dev/null || true
sudo rm -f /etc/systemd/system/plenora-{backend,celery,beat,backup,backup-test}.{service,timer}
sudo systemctl daemon-reload

# Wipe schema (DESTRUCTIVE)
psql "postgresql://plenora:plenora@127.0.0.1:5432/plenora" \
  -c "DROP SCHEMA IF EXISTS plenora CASCADE;"

# Wipe Redis broker / result backend / leader-lock state
redis-cli FLUSHDB

# Wipe the local app state
rm -rf ~/plenora-etl/backend/data
rm -f  ~/plenora-etl/backend/.env

# Now restart at step 2.4
```

### 4.8 Non-destructive rollback (foreground smoke)

`BE-GO-READY-INSTALL-010-LINUX-EXECUTION-PACK`.

If the foreground smoke (step 2.7) failed but Marco wants to
keep the configured `.env`, the migrated schema, and the Redis
state for the next attempt, this rollback path stops the
foreground processes WITHOUT touching DB / Redis / `.env`. Use
it when iterating on a transient issue (port conflict, missing
dep) and you do NOT want to redo step 2.5 (installer) or step
2.7 (uvicorn boot triggers migration runner).

```bash
# Stop the three foreground processes (Ctrl-C in each terminal,
# or kill by PID if the shell was lost).
pkill -INT -f 'uvicorn app.main:app' 2>/dev/null || true
pkill -INT -f 'celery -A workers.celery_app worker' 2>/dev/null || true
pkill -INT -f 'celery -A workers.celery_app beat' 2>/dev/null || true

# Confirm none survived (give SIGTERM 2s, then SIGKILL stragglers
# only if necessary).
sleep 2
pkill -KILL -f 'uvicorn app.main:app' 2>/dev/null || true
pkill -KILL -f 'celery -A workers.celery_app' 2>/dev/null || true
pgrep -af 'uvicorn|celery' || echo "no plenora processes left"
```

What this DOES NOT do (intentional):

- does NOT `DROP SCHEMA` (migrations stay applied);
- does NOT `FLUSHDB` (Redis broker / result backend / leader-
  lock keys preserved);
- does NOT remove `backend/.env` (secrets + decisions
  preserved; `chmod 600` retained);
- does NOT touch systemd units (none installed in step 2.7
  yet);
- does NOT remove the venv or the cloned repo.

Restart from step 2.7 (foreground processes) after fixing the
underlying cause. If the issue requires re-running the
installer to refresh non-secret keys, run
`bash scripts/install.sh --non-interactive` again - the
idempotent merger preserves operator-added keys
(`INSTALLATION-012`).

For a destructive total reset (PC-test-only), see section 4.7.

---

## 5. Clean-install smoke harness (canonical sequence)

`BE-GO-READY-INSTALL-007-CLEAN-INSTALL-SMOKE-HARNESS`.

The eight items below are the canonical clean-install smoke
harness. Each row is a single thing the operator must verify
before the install can be declared green; the right-hand column
points at the exact command/section. This page is the
operator-facing single source - do NOT improvise the order; the
boot gates fire in the same order and skipping a row makes the
diagnosis path harder.

### 5.0 Executable hook

`BE-GO-READY-INSTALL-008-SMOKE-SCRIPT-HOOK`. Rows 1-6 are
covered by `bash scripts/install.sh --check` (preflight) and the
boot/curl steps in section 2. Rows 7 and 8 plus an app-import
sanity probe are wrapped by:

```bash
cd ~/plenora-etl
PLENORA_SMOKE_BACKEND_URL=http://127.0.0.1:5004 \
PLENORA_SMOKE_RUN_PING=1 \
bash scripts/install.sh --smoke
```

The flag is non-destructive: zero prompts, zero writes, zero
package installs, zero service restarts. It exits 0 only when
every attempted check is green. Both env vars are optional - omit
`PLENORA_SMOKE_BACKEND_URL` to skip the health/ready curl
(useful when the operator has not yet started uvicorn) and omit
`PLENORA_SMOKE_RUN_PING` to skip the cleanroom.ping round-trip
(useful when no foreground worker is running). Skipped checks
emit WARN but do not fail the harness.

What `--smoke` does internally:

1. `Smoke 1 - Memory caps visibility`: runs the section 5.2
   one-liner inside `backend/venv/bin/python` and prints all
   six canonical caps via `app.config.settings.get(...)`.
2. `Smoke 2 - Backend import sanity`: imports `app.config` and
   `workers.celery_app` to verify the venv + `backend/.env` can
   construct the Settings singleton without booting FastAPI.
3. `Smoke 3 - /health and /ready`: when
   `PLENORA_SMOKE_BACKEND_URL` is set, curls `${URL}/health` and
   `${URL}/ready` with a 5-10 s timeout.
4. `Smoke 4 - Celery cleanroom.ping round-trip`: when
   `PLENORA_SMOKE_RUN_PING=1`, sends `cleanroom.ping` and
   asserts `pong` via `AsyncResult.get(timeout=30)` exactly as
   documented in section 5.3.

The prose in 5.2 / 5.3 / 5.4 below remains the canonical
description of the contract; the script is the convenience
wrapper, not a replacement. Each subsection below stays
copy/paste-ready for the operator who prefers to run a single
step manually or has an installation that diverges from the
default `~/plenora-etl/backend/venv` layout.

### 5.1 Eight-item canonical mapping

| # | Harness item | Where the command lives | Pass signal |
|---|---|---|---|
| 1 | clone + env setup (repo cloned, decisions resolved) | step 2.4 above; decisions table in section 1 | `--check` exits 0 |
| 2 | backend deps (venv + `pip install -r requirements.txt`) | step 2.6 above | `./venv/bin/python -c "import fastapi, celery, psycopg2"` exits 0 |
| 3 | DB init + migrations (schema autocreate on first uvicorn boot) | step 2.7 (uvicorn) + post-smoke `psql ... SELECT count(*) FROM plenora.schema_migrations` | row count matches the deployed migration count (currently 18; see step 2.8) |
| 4 | object storage choice locked in `.env` | step 2.5 (installer) - first PC test = `STORAGE_POLICY=db` + dummy `BACKUP_BUCKET` | `/ready` reports `object_storage` as `skipped: STORAGE_POLICY=db` |
| 5 | workers/beat count (foreground for first run) | step 2.7 - uvicorn `--workers 1`, celery worker `--pool=solo`, beat singleton | three foreground processes log "ready" / "running on" / "PlenoraBeatScheduler avviato" |
| 6 | health + ready checks | step 2.8 above | `/health` 200 AND `/ready` 200 with all 6 ready checks ok |
| 7 | memory caps visibility (RESOURCE_MAX_BYTES + siblings) | section 5.2 below | one-liner prints the six caps with the values the runtime will use |
| 8 | tiny ETL/project smoke (real worker dispatches a real task) | section 5.3 below | `cleanroom.ping` round-trips `pong` via `AsyncResult.get(timeout=30)` |

The harness is COMPLETE only when all eight rows are green. A
green `/health` + `/ready` (rows 1-6) without rows 7 and 8 means
the boot gates pass but the operator has NOT verified that the
memory budget on this host is what the runtime will enforce, and
has NOT verified that a worker actually consumes a task from the
broker. Both have been the silent-failure mode in past first-PC
attempts.

### 5.2 Memory caps visibility (row 7)

The runtime memory budget is governed by six `Settings.DEFAULTS`
keys. Defaults are sane for an 8-16 GB server; for a smaller PC
the operator should override them BEFORE step 2.7. Run inside
the venv with `backend/.env` already in place:

```bash
cd ~/plenora-etl/backend
./venv/bin/python - <<'PYEOF'
from app.config import settings
keys = (
    "LOAD_FILE_MAX_BYTES",
    "WORKER_RSS_MAX_BYTES",
    "RESOURCE_MAX_BYTES",
    "ARTIFACT_PREVIEW_MAX_BYTES",
    "CROSS_JOIN_MAX_OUTPUT_ROWS",
    "MULTI_JOIN_MAX_OUTPUT_ROWS",
)
for key in keys:
    val = settings.get(key)
    print(f"{key}={val}")
PYEOF
```

Expected (defaults, no override in `backend/.env`):

```
LOAD_FILE_MAX_BYTES=209715200
WORKER_RSS_MAX_BYTES=1500000000
RESOURCE_MAX_BYTES=209715200
ARTIFACT_PREVIEW_MAX_BYTES=26214400
CROSS_JOIN_MAX_OUTPUT_ROWS=50000000
MULTI_JOIN_MAX_OUTPUT_ROWS=50000000
```

For Marco's reference PC (small single-machine, <=4 GB host
RAM) the recommended overrides per
[install-non-docker.md section 6.2](install-non-docker.md#62-memory-caps-recommended-values)
are:

```
LOAD_FILE_MAX_BYTES=104857600    # 100 MB upload cap
WORKER_RSS_MAX_BYTES=838860800   # 800 MB worker RSS
RESOURCE_MAX_BYTES=104857600     # 100 MB single-blob read cap
ARTIFACT_PREVIEW_MAX_BYTES=10485760 # 10 MB preview cap
```

The runtime default for `RESOURCE_MAX_BYTES` is 200 MB
(BE-GO-READY-MEMORY-ENGINE-005, lowered from 500 MB). Hosts
with >=8 GB RAM can keep the default; hosts with <=4 GB
should override down to 100 MB as shown above.

A cap `<= 0` disables the corresponding guard (escape hatch).
`RESOURCE_MAX_BYTES` is enforced by bounded blob/resource readers
(`ResultBlobLoader`, file blob materialization). Preview endpoints
use the lower `ARTIFACT_PREVIEW_MAX_BYTES` cap because they decode
the bounded bytes into pandas/pyarrow structures and can run
concurrently on API workers.

### 5.3 Tiny ETL/project smoke (row 8)

Plenora-ETL ships a pure no-op task `cleanroom.ping`
(`backend/workers/tasks/cleanroom_task.py`). It is the same
deterministic primitive the cleanroom CI lane uses for the
worker enqueue/dequeue smoke. Reuse it locally for the first
PC test - it requires NO PG `connections` row, NO file source,
and NO real ETL state:

```bash
cd ~/plenora-etl/backend
./venv/bin/python - <<'PYEOF'
from workers.celery_app import celery_app
ar = celery_app.send_task("cleanroom.ping")
print(ar.get(timeout=30))
PYEOF
```

Expected output: `pong`.

A `kombu.exceptions.OperationalError` means the broker URL is
wrong or Redis is down - re-run step 2.3 probe. A
`celery.exceptions.TimeoutError` means the foreground worker in
step 2.7 is not actually consuming tasks; re-check the worker
terminal log for an exception during boot. A non-`pong` return
means a future change broke the cleanroom contract; bisect from
`workers/tasks/cleanroom_task.py` and the `include=[...]` list
in `workers/celery_app.py`.

This smoke is bounded (`AsyncResult.get(timeout=30)`),
sleep-free, and idempotent. Re-running it does not pollute any
domain table.

### 5.4 Docker vs single-machine deltas

Plenora-ETL now has a FIRST SCAFFOLD Docker path
(`backend/Dockerfile`, `docker-compose.yml`, `.env.docker.example`,
`scripts/docker-validate.sh`; see [install-docker.md](install-docker.md)).
It is **NOT YET VALIDATED** end-to-end on Marco's Linux PC. The
single-machine sequence above (steps 2.1-2.9 + harness rows 7-8)
remains the ONLY go-ready path today.

Docker scaffold deltas:

| Harness item | Single-machine path (today) | Docker path (first scaffold, not yet validated) |
|---|---|---|
| 1 (clone + env) | `git clone` + `bash scripts/install.sh` | `git clone` + `cp .env.docker.example .env` + secret generation per [install-docker.md section D](install-docker.md#d-envdockerexample) |
| 2 (backend deps) | `./venv/bin/pip install -r requirements.txt` | baked into `backend/Dockerfile` build stage (per [install-docker.md section A](install-docker.md#a-backenddockerfile)) |
| 3 (DB init) | first uvicorn boot triggers `migrations_runner.ensure_schema(DB_SCHEMA)` | same code path; `backend` service `depends_on: postgres` (healthy) |
| 4 (object storage) | `STORAGE_POLICY=db` in `backend/.env` | `STORAGE_POLICY=db` in `.env.docker.example` (already set) |
| 5 (workers/beat) | three foreground processes (uvicorn / worker / beat) | `worker` and `beat` services share the backend image with overridden `CMD` |
| 6 (health/ready) | `curl http://127.0.0.1:5004/{health,ready}` | `docker compose ps` healthcheck (gated by `HEALTHCHECK` in `Dockerfile`) plus the same `curl` |
| 7 (memory caps) | `RESOURCE_MAX_BYTES`, `ARTIFACT_PREVIEW_MAX_BYTES`, etc. in `backend/.env` | commented overrides already present in `.env.docker.example` (per [install-docker.md section D](install-docker.md#d-envdockerexample) lines `LOAD_FILE_MAX_BYTES` / `WORKER_RSS_MAX_BYTES` / `RESOURCE_MAX_BYTES` / `ARTIFACT_PREVIEW_MAX_BYTES`) |
| 8 (tiny ETL smoke) | `celery_app.send_task("cleanroom.ping")` from `./venv/bin/python` | `docker compose exec worker python -c "..."` against the running worker container |

The caps surface MUST stay visible in both `.env`/`.env.docker.example`
and in the harness step 7 output. Removing any of the six caps
from either location is a regression - locked by the harness
guard test.

---

## 6. What is NOT in this dry-run pack

For each below, the answer for the first PC test is "skip;
not needed":

- AWS SSM Parameter Store / IAM role (only needed for the
  AWS path in production);
- MinIO server provisioning (only needed for
  `CLOUD_PROVIDER=minio`);
- backup unit + weekly restore drill (`plenora-backup.timer`
  / `plenora-backup-test.timer`) - safe to skip until the
  install is otherwise working;
- frontend build + reverse proxy (Marco can hit the API
  directly via `curl`/`http` for the first smoke; bring up
  the frontend after the API is stable);
- Docker / docker-compose - first scaffold exists, but is not
  validated; skip unless explicitly running the Docker validation
  pack from [install-docker.md](install-docker.md);
- Helm / Kubernetes / multi-host - **OUT OF SCOPE**.

---

## 7. Related docs

- [install-non-docker.md section 13](install-non-docker.md#13-operator-quick-reference-checklist-real-linux-pc-test)
  - the full reference checklist with decision matrix and
  rollback details.
- [install-non-docker.md section 12.8](install-non-docker.md#128-cleanroom-celery-smoke-vs-production-drift-sanity-check-not-equivalence)
  - cleanroom-vs-production drift; explains why a green
  `ci-cleanroom.yml` does NOT mean a real PC install will
  pass.
- [install-docker.md](install-docker.md) - Docker first scaffold
  and Linux validation pack, NOT YET VALIDATED.
- [runtime-prereqs.md](runtime-prereqs.md) - runtime
  contract (env, IAM, dependencies).
- [runbook.md](runbook.md) and
  [backup-restore.md](backup-restore.md) for ops once the
  install is up.
