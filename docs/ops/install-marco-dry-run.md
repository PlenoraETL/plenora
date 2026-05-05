# Marco's first-PC clean-install dry-run pack

`BE-GO-READY-INSTALLATION-024-LINUX-CLEAN-INSTALL-DRY-RUN-PACK`.

Companion to [install-non-docker.md](install-non-docker.md). One
page, copy/paste-ready. The full reference (decision matrix,
rollback details, gotchas) lives in
[install-non-docker.md section 13](install-non-docker.md#13-operator-quick-reference-checklist-real-linux-pc-test);
this page is the executable subset for **the first real PC test
on a fresh Ubuntu/Linux host with Docker already installed**.

> **Docker status (honest, NOT the go-ready path).** Marco's PC
> has Docker installed. Plenora-ETL has **NO `Dockerfile`** and
> **NO `docker-compose.yml`** and NO Helm chart in this commit.
> An operator with Docker pre-installed CANNOT install Plenora-ETL
> via `docker compose up`. See
> [install-docker.md](install-docker.md) for the target shape
> of the future Docker batch (`INSTALLATION-010..013`); until
> that batch ships and is tested, **the only go-ready path is
> this one**, which does not use Docker at all.

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

## 3. Failure triage checklist

If any step fails, check this in order. The four boot gates
log loudly: `journalctl` (or the foreground terminal output)
will say which one tripped.

### 3.1 `scripts/install.sh --check` FAIL

| Symptom | Fix |
|---|---|
| `python3.11 not found` | re-run apt install (step 2.1); on Ubuntu 24.04 it is in the default repo |
| `python3.11 reports an unexpected version` | a corrupted symlink. Run `which python3.11 && python3.11 --version` and reinstall apt |
| `psql / redis-cli / node / npm / curl / git missing` | apt install line covers them; if Ubuntu container without `apt`, install manually |
| `shared lib 'gdal' NOT found` | `sudo apt install libgdal-dev` (step 2.1 covers it) |
| `pg_dump not found` | `sudo apt install postgresql-client-NN` matched to server major; warn-only - the install proceeds |

### 3.2 `bash scripts/install.sh --non-interactive` FAIL

| Symptom | Fix |
|---|---|
| `psql SELECT 1 failed` | check `DATABASE_URL` value; check `pg_hba.conf` allows `127.0.0.1` md5 or trust; check role exists with password |
| `redis-cli PING failed` | confirm `redis-server` is running (`sudo systemctl status redis-server`); test PING manually |
| `Missing /path/.env.example` | wrong CWD - run `bash scripts/install.sh` from the repo root |
| `--check` passes but install hangs | likely an interactive prompt - re-run with `--non-interactive` and the `PLENORA_INSTALL_*` env vars |

### 3.3 Foreground uvicorn FAIL

The four boot gates fire in this order; whichever first
prints `[ERROR]`/`SystemExit` is the one to fix.

| Gate | Symptom in log | Fix |
|---|---|---|
| `validate_startup` | `DATA_DIR not writable` OR `Redis non raggiungibile` | check write permission on the working directory; check Redis with `redis-cli` |
| `validate_storage_config` | `StorageConfigError: ...` | re-check `.env` STORAGE_POLICY x BACKUP_BUCKET x OBJECT_STORAGE_*. For first PC test: `STORAGE_POLICY=db` AND `BACKUP_BUCKET` non-empty are the only requirements |
| `init_all_dbs` -> migrations | `permission denied for schema "plenora"` | role missing `CREATE` on DB; re-run the `CREATE USER ... CREATEDB` SQL from step 2.2 |
| `_repair_psycopg2_ssl_env` | rare, log self-explanatory | usually a downstream symptom of a failed `init_all_dbs` |

### 3.4 Foreground celery worker FAIL

Same four boot gates run via `worker_init` signal. Most
common new failure here:

- `kombu.exceptions.OperationalError: [Errno 111] Connection
  refused` -> Redis is down or the broker URL is wrong;
- `psycopg2.OperationalError: ...` -> Postgres unreachable
  by the worker process (separate connection from API).

### 3.5 Foreground celery beat FAIL

- `[LEADER] Redis non raggiungibile` -> Redis down. Beat
  goes to standby; tick is a no-op until Redis is back.
- `PlenoraBeatScheduler avviato (...)` but no tick after 60s
  -> standby due to leader-lock contention. Ensure no other
  beat process is running.

### 3.6 `/health` returns 200 but `/ready` returns 503

Read the failing check name from the response body:

| Failing check | Likely cause |
|---|---|
| `pool_headroom` | PG `max_connections` too low for `(uvicorn_workers + celery_concurrency + 1) * DB_POOL_MAX + 5`. Section 2 of install-non-docker.md has the formula. |
| `migrations_aligned` | a migration file in `backend/migrations/` did not apply cleanly. Inspect the foreground uvicorn log around startup. |
| `object_storage` | only fires on `STORAGE_POLICY != db`. For first PC test stay on `db`. |
| `reconciliation_backlog` | only fires when `pending` or `lost` cross threshold. Fresh DB cannot trip this. |

### 3.7 Total reset (DESTRUCTIVE - first PC test only)

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

---

## 4. What is NOT in this dry-run pack

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
- Docker / docker-compose - **NOT IMPLEMENTED** in this
  repo at this commit;
- Helm / Kubernetes / multi-host - **OUT OF SCOPE**.

---

## 5. Related docs

- [install-non-docker.md section 13](install-non-docker.md#13-operator-quick-reference-checklist-real-linux-pc-test)
  - the full reference checklist with decision matrix and
  rollback details.
- [install-non-docker.md section 12.8](install-non-docker.md#128-cleanroom-celery-smoke-vs-production-drift-sanity-check-not-equivalence)
  - cleanroom-vs-production drift; explains why a green
  `ci-cleanroom.yml` does NOT mean a real PC install will
  pass.
- [install-docker.md](install-docker.md) - Docker target
  shape, NOT IMPLEMENTED.
- [runtime-prereqs.md](runtime-prereqs.md) - runtime
  contract (env, IAM, dependencies).
- [runbook.md](runbook.md) and
  [backup-restore.md](backup-restore.md) for ops once the
  install is up.
