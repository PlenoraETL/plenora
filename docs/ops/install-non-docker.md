# Clean install — non-Docker single-machine

Single source of truth for installing Plenora-ETL from scratch on a
fresh Linux host (Ubuntu 24.04 reference) without containers. Windows
dev install variant in §10.

This runbook is the consolidated, committable form of audit
`BE_INSTALLATION_AUDIT.md` §3 (stream
`BE-GO-READY-INSTALLATION-007-CLEAN-INSTALL-RUNBOOK`). Other ops docs
(`runtime-prereqs.md`, `deploy-checklist.md`,
`backend/deploy/systemd/README.md`) link here instead of duplicating
the procedure.

For the Docker path, see [install-docker.md](install-docker.md) — at
the time of writing, that path is **NOT IMPLEMENTED** (no Dockerfile,
no compose). For day-2 operations (deploy of an UPDATE, not a fresh
install) see [`deploy-checklist.md`](deploy-checklist.md).

---

## 0. Acceptance criteria

The install is complete when **all** the following are true:

- `systemctl is-active plenora-backend plenora-celery plenora-beat`
  returns `active` for all three;
- `curl -fsS http://127.0.0.1:5004/health` returns HTTP 200 with body
  `{"postgres":{"ok":true},"redis":{"ok":true},...}`;
- `curl -fsS http://127.0.0.1:5004/ready` returns HTTP 200 with
  migrations aligned and pool headroom;
- admin login succeeds, a manual preview executes, the schedule tick
  appears in `plenora.schedule_logs`.

The four boot gates that must pass internally
(`Settings.validate_startup` → `validate_storage_config` →
`init_all_dbs`+`apply_pending_migrations` →
`_repair_psycopg2_ssl_env`, see `backend/app/bootstrap.py`) are
exercised automatically by uvicorn startup; any failure prevents
`/ready` from going green.

---

## 1. Host prerequisites

```
sudo apt update
sudo apt install -y \
  python3.11 python3.11-venv python3.11-dev \
  build-essential libpq-dev \
  postgresql-client-17 \
  libgdal-dev libmagic1 \
  redis-server \
  curl ca-certificates \
  nodejs npm
```

Notes:

- **Python**: pinned to 3.11 — the version validated by all CI lanes
  (`ci.yml`, `ci-cleanroom.yml`, `ci-functional.yml`). 3.12 is not
  validated.
- **Postgres client**: must match the major version of the target
  Postgres server. `pg_dump`/`pg_restore` are version-strict and
  refuse to read newer dumps. PG 17 client requires the PGDG repo on
  Ubuntu 24.04 (default ships PG 16).
- **Node**: the frontend `package.json` does not pin `engines`; use
  the LTS active line. The lockfile is `package-lock.json` (npm).
- **Redis**: required even on hosts that delegate to a managed
  external Redis (the `redis-cli` binary is convenient for the
  connectivity probe in §3).

### 1.1 Application user

The systemd units default to `User=ubuntu`,
`WorkingDirectory=/home/ubuntu/plenora-etl/backend`. To deviate from
this convention, see §7 — the unit files are hardcoded and must be
edited before installation.

```
sudo adduser --system --group --home /home/ubuntu --shell /bin/bash ubuntu
```

Skip if the host already has the user.

---

## 2. Provision PostgreSQL

External (RDS / Cloud SQL / on-host) is supported via `DATABASE_URL`.
The schema is auto-created on first boot by
`migrations_runner.ensure_schema(DB_SCHEMA)` — operators do **not**
need to run `psql ... CREATE SCHEMA` manually.

Required:

- a database (e.g. `plenora`);
- an application role with `CREATE` on the database and
  `ownership`/`USAGE` on the target schema (default `plenora`);
- for the weekly backup-test drill: `ALTER ROLE <user> CREATEDB;`
- `max_connections >= (UVICORN_WORKERS + CELERY_CONCURRENCY + 1) * DB_POOL_MAX + 5`.
  At default sizing `(2 + 4 + 1) × 12 + 5 = 89` connections, with
  PG 15 default `max_connections=100` and `superuser_reserved=3`
  leaves a 13-conn margin.

Connectivity probe (must succeed before §6):

```
psql "$DATABASE_URL" -c "SELECT 1"
```

---

## 3. Provision Redis

Single Redis instance, single DB. **Plenora intentionally has one
canonical Redis URL** (`CELERY_BROKER_URL`) covering broker, result
backend, scheduler leader-lock, and JWT blacklist. There is no
separate `REDIS_URL` env var — see
[`runtime-prereqs.md`](runtime-prereqs.md) "Redis — perché una sola
variabile".

Recommended for resilience: `appendonly yes` (AOF) so that beat
leader-lock state survives Redis restarts.

Connectivity probe:

```
redis-cli -u "$CELERY_BROKER_URL" PING
# expect: PONG
```

---

## 4. Provision object storage (only if `STORAGE_POLICY != db`)

Skip this section for `STORAGE_POLICY=db` installs. `BACKUP_BUCKET`
must still be set to a non-empty string (a dummy is acceptable —
`validate_storage_config()` requires non-empty even on `db`).

### 4.1 AWS S3

Buckets (must exist before first boot, distinct from one another):

- `OBJECT_STORAGE_BUCKET` — runtime artifacts (results, file sources,
  intermediate `full_artifact` mode, IMDF bundles, eseguibili
  outputs);
- `BACKUP_BUCKET` — DB dumps;
- `LOG_ARCHIVE_BUCKET` — only if `LOG_ARCHIVE_ENABLED=true`.

Lifecycle rules: managed by Terraform in
`backend/deploy/terraform/lifecycle.tf`. Bucket creation, IAM, KMS,
and SSM are NOT in the Terraform — they remain manual operator steps.

IAM role attached to the host needs at minimum:
`s3:{ListBucket,GetObject,PutObject,DeleteObject}` on the runtime
and backup buckets. Reference policies:
`backend/deploy/iam/plenora-backup-s3.json`,
`backend/deploy/iam/plenora-backup-ssm-read.json`.

### 4.2 MinIO

ADR-0015 path. Requires:

- MinIO server reachable from the host;
- bucket(s) pre-created;
- env: `CLOUD_PROVIDER=minio`, `MINIO_ENDPOINT_URL`,
  `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`;
- the boto3 client uses `addressing_style=path` automatically.

---

## 5. Provision secrets

### 5.1 AWS path (production reference)

`DATABASE_URL` lives in SSM Parameter Store as `SecureString` under
prefix `/plenora/prod/` (configurable via `SECRETS_PREFIX`). The
systemd backup units (`plenora-backup`, `plenora-backup-test`)
fetch secrets via `scripts/load_secrets.py` at runtime — they do
NOT read `backend/.env`.

### 5.2 Non-AWS path

Inline secrets directly in `backend/.env`. The long-running units
(`plenora-backend`, `plenora-celery`, `plenora-beat`) read
`backend/.env` via systemd `EnvironmentFile=` regardless of cloud
provider.

`backend/.env` permissions: `chmod 600 .env` and ensure ownership is
the application user.

---

## 6. Checkout, venv, configure

```
sudo -iu ubuntu
git clone <repo> /home/ubuntu/plenora-etl
cd /home/ubuntu/plenora-etl/backend

python3.11 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
# Do NOT install requirements-dev.txt in production.

cp .env.example .env
# Edit .env per the decision matrix below.

./venv/bin/python -c \
  "import secrets; print('PLENORA_ENCRYPTION_KEY=' + secrets.token_urlsafe(48))" \
  >> .env
./venv/bin/python -c \
  "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" \
  >> .env

chmod 600 .env
```

### 6.1 Decision matrix (minimum viable env)

The `.env` file at this point must define the following:

| Variable | Required when | Notes |
|---|---|---|
| `ENVIRONMENT` | always | `production` / `staging` / `development`. Drives fail-fast, dotenv loading, WSL Redis fallback gating. |
| `DATABASE_URL` | always | Full Postgres DSN. Module fail-fast at import if unset. |
| `DB_SCHEMA` | always | Default `plenora`. Auto-created at first boot. |
| `CELERY_BROKER_URL` | always | Canonical Redis URL. |
| `CELERY_RESULT_BACKEND` | always | Set to the same value as `CELERY_BROKER_URL`. |
| `PLENORA_ENCRYPTION_KEY` | hardened envs | Generated above. **Never rotate post-encryption** — rotation makes encrypted records unreadable. |
| `JWT_SECRET_KEY` | hardened envs | Generated above. Rotation invalidates all sessions. |
| `STORAGE_POLICY` | always | `db` / `hybrid` / `object_storage`. Production cloud: `object_storage`. |
| `BACKUP_BUCKET` | always | Required-non-empty even on `STORAGE_POLICY=db`. |
| `OBJECT_STORAGE_ENABLED` | `STORAGE_POLICY != db` | `true`. |
| `OBJECT_STORAGE_BUCKET` | `STORAGE_POLICY=object_storage` | Distinct from `BACKUP_BUCKET`. |
| `CLOUD_PROVIDER` | object storage in use | `aws` / `minio` / `none`. |
| `AWS_REGION` | `CLOUD_PROVIDER=aws` | e.g. `eu-south-1`. |
| `MINIO_ENDPOINT_URL` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | `CLOUD_PROVIDER=minio` | Validated at boot. |
| `LOG_ARCHIVE_BUCKET` | `LOG_ARCHIVE_ENABLED=true` | Distinct from `BACKUP_BUCKET`. |
| `CORS_ORIGINS` | prod (different origin from API) | CSV of public frontend URLs. |
| `TRUSTED_PROXIES` | behind a load balancer | CIDR allowlist for X-Forwarded-For. |

Pool sizing (`DB_POOL_MIN`, `DB_POOL_MAX`,
`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`), token lifetime
(`JWT_EXPIRE_HOURS`), login throttle (`LOGIN_*`), retention
(`LOG_RETENTION_DAYS`, `SCHEDULE_LOGS_RETENTION_DAYS`,
`AUDIT_RETENTION_DAYS`), feature flags (`ENABLE_ASYNC_ENRICHMENT`,
`MSSQL_FAST_EXECUTEMANY`, `CARICA_BULK_INSERT`), and storage
thresholds all have sensible defaults in `Settings.DEFAULTS` and do
not need to be set for a clean install. Override only when ops needs
require it.

For full classification per ADR-0021, see audit
`BE_INSTALLATION_AUDIT.md` §3.

---

## 7. Install systemd units

```
cd /home/ubuntu/plenora-etl/backend/deploy/systemd
sudo cp \
  plenora-backend.service plenora-celery.service plenora-beat.service \
  plenora-backup.service plenora-backup.timer \
  plenora-backup-test.service plenora-backup-test.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
  plenora-backend.service plenora-celery.service plenora-beat.service
sudo systemctl enable --now \
  plenora-backup.timer plenora-backup-test.timer
```

If the install does not match `User=ubuntu` and
`WorkingDirectory=/home/ubuntu/plenora-etl/backend`, edit the unit
files BEFORE `sudo cp`. `WorkingDirectory=`, `User=`,
`EnvironmentFile=`, and `ExecStart=` are all hardcoded
(`plenora-backend.service:6-11`).

`UVICORN_WORKERS` (`--workers 2`) and `CELERY_CONCURRENCY`
(`--concurrency=4`) are hardcoded as CLI flags. Resizing requires
editing the unit files + `daemon-reload`.

For details on the backup unit credential model (SSM via
`load_secrets.py`), see
[`backend/deploy/systemd/README.md`](../../backend/deploy/systemd/README.md).

---

## 8. Frontend build and serve

```
cd /home/ubuntu/plenora-etl/frontend
npm ci
npm run build
# Output: dist/ — serve via nginx/Caddy/Apache as static files.
```

Configure the reverse proxy to:

- serve `frontend/dist/` at the public origin;
- proxy `/api/*`, `/health`, `/ready`, `/auth/*` (and `/docs`,
  `/openapi.json`, `/redoc` only in non-production) to
  `127.0.0.1:5004`.

CORS: same-origin proxying bypasses CORS entirely. For split-origin
deployments, set `CORS_ORIGINS` to the public frontend URL(s).

Atomic frontend swap via `current → releases/<sha>` symlink is NOT
implemented; `deploy.sh:deploy_frontend()` does in-place
replacement. Tracked as residual `BE_INSTALLATION_AUDIT.md` §6.12.

---

## 9. Smoke test

```
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5004/health
# expect: 200

curl -s http://127.0.0.1:5004/ready
# expect: 200, migrations aligned, pool headroom positive

systemctl is-active plenora-backend plenora-celery plenora-beat
# expect: active (×3)

journalctl -u plenora-backend --since '5 minutes ago' --no-pager
# expect: bootstrap completed, no errors
```

Functional smoke (manual):

- admin login;
- create a connection, run a preview;
- schedule a one-off task and verify completion in the admin
  dashboard;
- confirm a row appears in `plenora.schedule_logs` for the next
  scheduler tick.

For the post-install backup verification, see
[`backup-restore.md`](backup-restore.md) and the `Test manuale`
section in
[`backend/deploy/systemd/README.md`](../../backend/deploy/systemd/README.md).

---

## 10. Dev-Windows variant

For a local development install on Windows (laptop):

```
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# Generate secrets — same Python one-liners as §6.
# Set ENVIRONMENT=development (or leave unset; dev is the default).

uvicorn app.main:app --reload --port 5004
# In a second shell: celery -A workers.celery_app worker --loglevel=info
# In a third shell:  celery -A workers.celery_app beat --loglevel=info

cd ..\frontend
npm install
npm run dev   # Vite dev server on :5173
```

Caveats:

- `Settings._ensure_redis()` will attempt
  `wsl redis-server --daemonize yes` if no Redis answers on the
  configured broker URL. The fallback is gated to
  `ENVIRONMENT=development` AND `sys.platform.startswith("win")`
  (see stream `INSTALLATION-003`); on Linux servers and in hardened
  environments the gate fails fast with an operational message
  citing the broker URL.
- The canonical env var pair is `CELERY_BROKER_URL` +
  `CELERY_RESULT_BACKEND`. Setting `REDIS_URL` has no effect.

---

## 11. Known limitations

- No single-command installer (`install.sh` / `install.ps1`) covers
  the entire procedure idempotently. Audit `BE_INSTALLATION_AUDIT.md`
  §6.2 / §7.2.
- The cleanroom CI verifier (`.github/workflows/ci-cleanroom.yml`)
  exercises HTTP boot only — no Celery worker, no beat, no full-stack
  smoke. Audit §7.3.
- Resizing `UVICORN_WORKERS` and `CELERY_CONCURRENCY` requires
  editing the systemd unit files (the env vars affect only the
  startup log, not the CLI flags). Audit §6.8 / §7.9.
- `DB_SCHEMA` other than `plenora` boots, but the migration tracking
  table location is still hardcoded to `plenora.schema_migrations`
  (`migrations_runner._CREATE_TABLE_SQL`). Audit §7.4.
- Local-filesystem object storage backend
  (`OBJECT_STORAGE_BACKEND=local`) is declared as future and is
  unimplemented. On-prem deploys without S3/MinIO must use
  `STORAGE_POLICY=db` or `hybrid`. Audit §6.7.

---

## 12. Cleanroom acceptance targets

A clean install is "complete" only when it passes the matrix below
end-to-end on a host that has had no prior Plenora-ETL artifacts. The
preflight gate (`scripts/install.sh --check`) covers the
host-packaging side; the `/ready` gate covers the application side.
Together they form the acceptance contract for any clean-install
claim.

### 12.1 Preflight: host packaging guard

`scripts/install.sh --check` is bounded, idempotent, ASCII-only, and
performs **no writes and no service probes that need credentials**.
It returns exit 0 only when ALL of the following are true on the
host:

| Probe | What is checked | Failure mode |
|---|---|---|
| `python3.11` on PATH and version `3.11.x` | binary present + interpreter reports the pinned major.minor | `[ERROR] python3.11 not found` / `unexpected version` |
| `node`, `npm`, `psql`, `redis-cli`, `curl`, `git` on PATH | each command resolvable | `[ERROR] Missing command: <name>` |
| `gdal` shared library | `python3.11 -c "ctypes.util.find_library('gdal')"` returns non-empty (matches what fiona/geopandas see at runtime) | `[ERROR] shared lib 'gdal' NOT found` |
| `pg_dump` version | reports a usable major version (warn-only - server-side major is operator territory) | `[WARN] pg_dump not found` |
| Optional: `backend/venv/bin/python` smoke import | `psycopg2`, `redis`, `celery`, `fastapi`, `uvicorn`, `pyarrow`, `pandas`, `fiona`, `geopandas`, `shapely` all import cleanly via the venv | `[ERROR] venv smoke import FAILED` |

The single source of truth for these probes is `scripts/install.sh`
(`INSTALLER_REQUIRED_COMMANDS`, `INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR`,
`INSTALLER_REQUIRED_SHARED_LIBS`). A source-bound test
(`backend/tests2/shared/test_install_script_packaging_guard.py`)
locks the contract so silent regressions surface as test failures.

### 12.2 Non-Docker single-machine cleanroom (canonical target)

Acceptance criteria (all required):

1. `scripts/install.sh --check` exits 0 on a fresh Ubuntu 24.04
   host whose only Plenora-related state is the cloned repo.
2. After `scripts/install.sh --non-interactive` (with the env vars
   from section 6.1 set) and the manual steps section 7 to section 9, **all** of:
   - `systemctl is-active plenora-backend plenora-celery plenora-beat`
     reports `active` for each;
   - `curl -fsS http://127.0.0.1:5004/health` returns 200 with
     `postgres.ok=true` and `redis.ok=true`;
   - `curl -fsS http://127.0.0.1:5004/ready` returns 200 with
     migrations aligned and `reconciliation_backlog` ok;
   - admin login succeeds and an end-to-end pipeline preview
     completes.
3. **Idempotency**: a second run of `scripts/install.sh
   --non-interactive` on the same host preserves operator-added
   non-managed keys (e.g. `LOG_RETENTION_DAYS=60`,
   `CORS_ORIGINS=https://...`) and existing secrets.

This target is operational TODAY - the production deployment at
`lab.plenora.it` is the reference. The cleanroom step is gated
on operator availability of a fresh VM; no in-repo CI lane runs it.

### 12.3 Docker / compose cleanroom (NOT IMPLEMENTED)

See [install-docker.md](install-docker.md). The Docker batch
(`INSTALLATION-010..013` in the audit) has not landed; an operator
cannot `docker compose up` Plenora-ETL today. Once the batch ships,
the acceptance criteria are:

1. `docker compose -f docker-compose.yml up -d --wait` returns 0
   on a fresh host with only Docker installed (no Plenora-related
   volumes, images, or networks pre-existing).
2. `docker compose ps` shows postgres, redis, backend, worker, beat,
   frontend all `healthy`.
3. `curl -fsS http://localhost:<frontend-port>/api/health` returns
   200 (proxied through frontend) and the frontend serves the SPA.
4. `docker compose down -v` removes ALL state without leaking
   volumes or networks.
5. CI lane runs the same compose-up + smoke + compose-down on every
   PR that touches `backend/**`, `frontend/**`, or
   `docker-compose.yml`.

Until the artifacts exist, this section is normative for the
future stream, not procedure operators run today.

### 12.4 CI clean install smoke

Authoritative workflow today:
[`.github/workflows/ci-cleanroom.yml`](../../.github/workflows/ci-cleanroom.yml).
Triggers an HTTP-only boot from an empty Postgres + Redis pair on
every path-filtered PR (paths: `backend/app/db/**`, `backend/app/main.py`,
`backend/app/config.py`, `backend/migrations/**`,
`backend/requirements.txt`, the workflow file itself).

Acceptance criteria:

1. Pre-condition: `plenora` schema does NOT pre-exist (regression
   guard against accidental `CREATE SCHEMA` workarounds).
2. `python -m uvicorn app.main:app` boots on a fresh stack and
   `/ready` returns 200 within `ready_timeout_seconds` (default 60s).
3. Post-condition: `plenora.schema_migrations` exists with N
   migrations applied (currently 18) - confirms the runner-side
   `ensure_schema(DB_SCHEMA)` path actually fired, not a
   workaround.
4. **Celery cleanroom readiness** (added by
   `BE-GO-READY-INSTALLATION-014`): after the backend boot
   verification, an in-process probe asserts:
   - `workers.celery_app` imports cleanly with the cleanroom env
     (`CELERY_BROKER_URL`, `DATABASE_URL`, `PLENORA_ENCRYPTION_KEY`,
     etc.);
   - `celery_app.main == "dataflow_tasks"` (CLI contract for
     `celery -A workers.celery_app worker` and the systemd
     `plenora-celery.service` ExecStart);
   - each include module
     (`workers.tasks.{enrichment_task,carica_task,schedule_task,pipeline_task,imdf_publish_task}`)
     imports without exception - catches import-time bugs in any
     task module that would crash a real worker on startup;
   - the broker (the cleanroom Redis service) accepts a
     `kombu.connection.ensure_connection(timeout=5, max_retries=1)` -
     bounded, sleep-free, single attempt;
   - `workers.beat_scheduler.PlenoraBeatScheduler` symbol resolves
     (rename / deletion guard for `plenora-beat.service` ExecStart
     `-S` argument).
   No real `celery worker` / `celery beat` is spawned (would
   require fragile sleep loops). Task enqueue / dequeue is
   intentionally NOT exercised here.
5. Diagnostic-tier today (`continue-on-error: true`): green runs do
   not yet promote to required check. Promotion criterion: 3-4
   consecutive green runs on real PRs (audit section 7.B residual
   INSTALLATION-006 evidence).

Gaps still open vs the ideal CI smoke:

- No real `celery worker` enqueue/dequeue - the readiness probe
  catches import + broker reachability only. End-to-end task
  execution requires either fragile sleep loops or a deterministic
  task-completed signal we do not have today; closure is a future
  bounded stream `INSTALLATION-018-CELERY-WORKER-ENQUEUE-SMOKE`.
- No real `celery beat` schedule tick exercised - same constraint;
  beat scheduler instantiation is gated on PG state and beat
  leader-lock state, which the readiness probe deliberately does
  NOT touch (PG state is already validated by the `/ready`
  post-condition).
- No frontend build verified - frontend CI is in `ci.yml` (vitest
  only), no end-to-end `npm run build` + serve on the cleanroom
  stack. Closure: `INSTALLATION-017-FRONTEND-CLEANROOM`.
- No backup unit smoke - covered by the production weekly
  `plenora-backup-test.timer` drill, not by CI.

These are explicitly accepted gaps; closing each is a bounded
follow-up stream.

### 12.5 Test harness

Source-bound architecture lock for the install script:
`backend/tests2/shared/test_install_script_packaging_guard.py`.
Asserts that:

- `INSTALLER_REQUIRED_COMMANDS` contains the canonical 7 entries
  in the documented order;
- `INSTALLER_REQUIRED_PYTHON_MAJOR_MINOR` matches the Python
  version pinned in `.github/workflows/ci-cleanroom.yml`;
- `INSTALLER_REQUIRED_SHARED_LIBS` mentions `gdal`;
- the `--check` flag is wired to `CHECK_ONLY` and short-circuits
  before any prompt or service probe;
- `step_validate_packaging` exists and is invoked in `main`;
- the script is ASCII-only;
- the script does not invoke `apt install` / `apt-get install`
  / `dnf install` / `yum install` / `pacman -S` (no destructive
  package install automation, per scope rule).

The lock test is the contract surface for the cleanroom acceptance
target - if a future refactor silently removes a probe or the
ASCII-only invariant, the lock test fails before the operator ever
sees a regressed installer.

### 12.6 Real worker enqueue/dequeue smoke (IMPLEMENTED)

`BE-GO-READY-INSTALLATION-019-CELERY-WORKER-ENQUEUE-SMOKE-IMPL`
(scaffolded as design + skipped contract by INSTALLATION-018; the
runtime primitive and the workflow step landed together in 019,
flipping the previously-skipped Group C contract tests to PASS).

The CI cleanroom lane spawns a real `celery worker` process and
exercises a real enqueue/dequeue round-trip via the pure no-op
`cleanroom.ping` task. The smoke is bounded, sleep-free, and
deterministic. The contract is locked by
`backend/tests2/shared/test_celery_enqueue_smoke_design_guard.py`
so a future refactor cannot silently regress to a fragile pattern.

#### 12.6.1 Bounded smoke pattern

The cleanroom stack provides every primitive a sleep-free smoke
needs:

- **Redis result backend**: `CELERY_RESULT_BACKEND=redis://...` is
  set in `ci-cleanroom.yml` env, so `AsyncResult.get(timeout=N)`
  blocks at most `N` seconds by polling the backend - sleep-free
  from the test's perspective.
- **`task_track_started=True`** is on (`backend/workers/celery_app.py`),
  so `STARTED` is observable.
- **`task_send_sent_event=True`** is on, so the publisher side has
  a definite "sent" event.

The implemented pattern (in `.github/workflows/ci-cleanroom.yml`,
step "Verify Celery worker enqueue smoke"):

```bash
nohup celery -A workers.celery_app worker \
    --pool=solo --loglevel=info \
    -Q celery -n cleanroom@%h \
    > .cleanroom-logs/worker.log 2>&1 &
worker_pid=$!
echo "$worker_pid" > .cleanroom-logs/worker.pid
trap 'kill -TERM "$worker_pid" 2>/dev/null || true; sleep 2; kill -9 "$worker_pid" 2>/dev/null || true' EXIT
python - <<'PYEOF'
from workers.celery_app import celery_app
ar = celery_app.send_task("cleanroom.ping")
result = ar.get(timeout=30)   # bounded, sleep-free
assert result == "pong", f"got {result!r}"
PYEOF
```

Notes:

- `--pool=solo` avoids prefork complexity (no fork-unsafe pool
  class of bugs in this slice; covered separately by 12.6.4).
- `-Q celery -n cleanroom@%h` isolates the worker (custom node
  name + the default queue).
- `AsyncResult.get(timeout=30)` polls Redis with an internal cap;
  no shell sleep loop is needed. If the worker takes a few
  seconds to come online, `get()` retries silently up to the cap.
- The trap-based teardown (`TERM` then `KILL` after 2s) runs
  whether the assertion passed or failed. The `sleep 2` here is
  bounded **teardown** sleep, not bounded **readiness** sleep.
- A failure-only artifact upload step
  (`Upload worker log on failure`) makes the worker.log
  available for diagnosis on `AsyncResult` timeout or other
  failure mode.

#### 12.6.2 Runtime primitive

The pure no-op task lives at
`backend/workers/tasks/cleanroom_task.py` and is registered via
`workers/celery_app.py:include=[...]` (6th entry, after the 5
production task modules):

```python
# backend/workers/tasks/cleanroom_task.py
from workers.celery_app import celery_app

@celery_app.task(name="cleanroom.ping", ignore_result=False, bind=False)
def cleanroom_ping() -> str:
    return "pong"
```

Hard contract (locked by Group C of the design guard test):

- pure no-op: NO imports of `app.*`, `requests`, `boto3`,
  `open(`, `Path(`, or `os.environ`. Even an indirect DB or
  filesystem touch defeats the determinism guarantee.
- `ignore_result=False`: the result MUST round-trip through the
  Redis backend so `AsyncResult.get` can observe completion.
- name `"cleanroom.ping"`: stable, namespaced, will not collide
  with production task names.
- module file `workers/tasks/cleanroom_task.py` and entry in the
  `include=[...]` list of `workers/celery_app.py`.
- task is the ONLY symbol exported from the module - no helpers,
  no constants, no transitive imports.

Production cost: zero behavioural change. `plenora-celery.service`
loads the task on startup; the only effect is one additional
entry in the registry.

#### 12.6.3 Failure modes covered by the smoke

| Failure | Detected as |
|---|---|
| `cleanroom_task.py` import error (e.g. accidental `from app.x import y`) | worker fails to start; `AsyncResult.get(timeout=30)` raises `TimeoutError`; `worker.log` artifact uploaded |
| Task body raises | `AsyncResult.get(...)` re-raises the worker-side exception |
| Task body returns wrong value | assertion `result == "pong"` fails |
| Broker not reachable from worker side | worker bootstrap fails; `worker.log` shows the connection error |
| Production task module breaks on import (regression) | already covered by INSTALLATION-014 readiness step (separate failure) |

#### 12.6.4 What stays open

Two gaps remain after 019:

- **Beat schedule tick smoke**: requires seeded PG `schedules`
  rows and a deterministic "tick fired" signal. The only
  beat-driven task today is `beat_heartbeat`, which is
  `ignore_result=True` and unsuitable. Closure path:
  `INSTALLATION-020-CELERY-BEAT-TICK-SMOKE`.
- **Prefork worker smoke**: 019 uses `--pool=solo` for
  determinism. A separate smoke under the production prefork
  pool would catch the W2 fork-unsafe pool class of bugs.
  Closure path: optional `INSTALLATION-021-CELERY-PREFORK-SMOKE`.

Neither is a hard blocker for the current cleanroom contract;
both are documented so the next stream picks them up explicitly.

### 12.7 Real beat tick smoke (DESIGN, NOT IMPLEMENTED)

`BE-GO-READY-INSTALLATION-020-CELERY-BEAT-TICK-SMOKE-DESIGN-FIRST-GUARD`.

The CI cleanroom probe added by INSTALLATION-014 covers the beat
scheduler **symbol** (`PlenoraBeatScheduler` resolves), and 019
covers the **worker** enqueue/dequeue round-trip. Neither covers
a real beat **tick** firing a task end-to-end. This section
documents the audit findings, the missing primitive, and the
bounded smoke pattern that should land once the primitive exists.
The design is locked by
`backend/tests2/shared/test_celery_beat_tick_smoke_design_guard.py`
so the gap cannot be silently forgotten.

#### 12.7.1 Audit: what completion signals exist today

Two beat-related surfaces exist:

1. **`PlenoraBeatScheduler._write_heartbeat()`** in
   `backend/workers/beat_scheduler.py` is an **in-process
   method** (not a Celery task). It runs on every leader tick
   (`max_interval=60.0` seconds default) and writes the current
   timestamp to the PG `beat_heartbeat` table via
   `INSERT ... ON CONFLICT (id) DO UPDATE`. Observable via
   `psql -c "SELECT ts FROM plenora.beat_heartbeat WHERE
   id='beat'"`.

2. **`workers.tasks.schedule_task.beat_heartbeat`** is a Celery
   task with `ignore_result=True` and `max_retries=0`. Its
   docstring says "scritto da Beat ogni 60 secondi", but a
   source review of `PlenoraBeatScheduler.tick()` shows that
   `_write_heartbeat()` is invoked DIRECTLY in the beat process
   - the Celery task is NOT actually wired to the production
   beat schedule. It is a residual primitive without a current
   firing path.

Available primitives that a sleep-free smoke could rely on:

- **Redis result backend**: same as 019, available.
- **`AsyncResult.get(timeout=N)`**: same, available - but only
  observes tasks that round-trip a non-ignored result.
- **PG observation of `beat_heartbeat.ts`**: deterministic but
  requires a polling loop (even if bounded, a 60s `max_interval`
  is too coarse for a CI smoke without a tight cadence).

Available control surfaces in the beat scheduler that a smoke
could use:

- **`max_interval`** is class-level; reducing it for cleanroom
  would touch production runtime code.
- **`celery_app.conf.beat_schedule`** is the standard Celery
  beat config. `PlenoraBeatScheduler` does NOT explicitly
  consume it (its `tick()` calls `_sync_from_db()` and delegates
  to `super().tick()`). Whether the parent `Scheduler.tick()`
  honors `beat_schedule` in this composition is undocumented and
  must be verified before the implementation stream relies on
  it.

#### 12.7.2 Blocker: the missing primitive

There is no beat-fired Celery task today with
`ignore_result=False`. The only candidate (`beat_heartbeat`) is
`ignore_result=True` AND not actually fired by the production
beat (the `_write_heartbeat()` in-process method is what runs).

The minimal primitive required:

```python
# in backend/workers/tasks/cleanroom_beat_task.py (NEW file)
from workers.celery_app import celery_app

@celery_app.task(name="cleanroom.beat_ping", ignore_result=False, bind=False)
def cleanroom_beat_ping() -> str:
    """Cleanroom beat tick smoke target - pure no-op, fired
    deterministically by beat when the cleanroom opt-in is on."""
    return "beat_pong"
```

Plus an opt-in firing path for beat:

```python
# in backend/workers/celery_app.py (additive, gated by env)
import os
if os.getenv("PLENORA_CLEANROOM_BEAT_PING") == "1":
    from celery.schedules import schedule
    celery_app.conf.beat_schedule = {
        **(celery_app.conf.beat_schedule or {}),
        "cleanroom-beat-ping": {
            "task": "cleanroom.beat_ping",
            "schedule": schedule(run_every=5.0),  # 5s interval
        },
    }
```

Plus a confirmation that `PlenoraBeatScheduler.tick()` propagates
`celery_app.conf.beat_schedule` entries (or, if not, a small
shim in `_sync_from_db()` that merges them).

Hard contract (locked by Group C of the design guard test):

- `cleanroom.beat_ping` is a pure no-op (same banned imports as
  `cleanroom.ping`);
- `ignore_result=False`;
- the firing path is **gated by an env var** so production
  deploys never enqueue the cleanroom heartbeat;
- the cleanroom CI workflow sets the env var before spawning
  beat, asserts via `AsyncResult.get(timeout=...)` against the
  result backend, then tears down the beat process via the
  same trap-based pattern as 019.

#### 12.7.3 Why this is a separate stream

Adding the firing path requires editing
`backend/workers/celery_app.py` to consume an env-gated
`beat_schedule` entry, and possibly editing
`backend/workers/beat_scheduler.py` if the parent
`Scheduler.tick()` does not propagate config-based entries
through the PG-driven custom scheduler. This is non-trivial app-
code work that:

- changes production beat startup behaviour conditional on an
  env var (small but real surface);
- requires a new lock test extension on the existing
  INSTALLATION-014 readiness guard to handle the 7th `include`
  module entry;
- requires verifying that `_sync_from_db()` and
  `super().tick()` compose correctly for config-based entries.

The next bounded stream is
`BE-GO-READY-INSTALLATION-021-CELERY-BEAT-TICK-SMOKE-IMPL`,
which:

1. Adds `backend/workers/tasks/cleanroom_beat_task.py` with the
   contract in 12.7.2;
2. Updates `backend/workers/celery_app.py` to register the task
   AND wire the env-gated `beat_schedule` config;
3. Verifies `PlenoraBeatScheduler` honors config entries (or
   adds a minimal merge shim if it does not);
4. Adds the workflow steps `Verify Celery beat tick smoke` and
   the matching `Upload beat log on failure` between 019's
   enqueue smoke and `Stop backend`, with the env var
   `PLENORA_CLEANROOM_BEAT_PING=1` set on the spawned beat
   process only;
5. Flips the Group C tests in
   `test_celery_beat_tick_smoke_design_guard.py` from skip to
   active.

#### 12.7.4 Why design + guard, not implementation today

The brief explicitly bans:

- broad production schedule seeding (which a PG `schedules`
  table seed would be);
- fragile sleep loops in CI;
- Docker artifacts.

The cleanest implementation requires the env-gated
`beat_schedule` mechanism, which is a behavioural change to the
beat startup path. That justifies a dedicated stream rather than
folding into INSTALLATION-019. The audit confirms no existing
deterministic primitive with `AsyncResult.get`-observable result
is fired by the current beat - so a smoke today would have to
choose between PG polling (a sleep loop, even if bounded) or
seeding the production `schedules` table (banned).

#### 12.7.5 Bounded smoke pattern (target shape)

When INSTALLATION-021 lands, the smoke pattern is:

```bash
# in ci-cleanroom.yml, AFTER the existing enqueue smoke step:
mkdir -p .cleanroom-logs
PLENORA_CLEANROOM_BEAT_PING=1 \
nohup celery -A workers.celery_app beat \
    -S workers.beat_scheduler.PlenoraBeatScheduler \
    --loglevel=info \
    > .cleanroom-logs/beat.log 2>&1 &
beat_pid=$!
echo "$beat_pid" > .cleanroom-logs/beat.pid
trap 'kill -TERM "$beat_pid" 2>/dev/null || true; sleep 2; kill -9 "$beat_pid" 2>/dev/null || true' EXIT
python - <<'PYEOF'
# A worker is already up from the 019 enqueue step; reuse it.
# AsyncResult.get(timeout=20) blocks at most 20s polling Redis.
# The 5s beat schedule fires twice in that window, so a single
# tick missed for transient reasons still leaves the smoke green.
from workers.celery_app import celery_app
import time
deadline = time.monotonic() + 20.0
last_observed = None
while time.monotonic() < deadline:
    # Inspect the result backend for any cleanroom.beat_ping
    # result emitted by the running worker. Fast path: poll the
    # backend's recent results. Implementation detail TBD by 021
    # depending on how Celery exposes it.
    ...
PYEOF
```

Implementation note for 021: the canonical pattern uses
`celery_app.events.Receiver` or backend keyspace polling rather
than reusing `AsyncResult.get` (the latter requires the task
ID, which beat does not surface to the test harness). 021 will
finalize the pattern and lock it.

#### 12.7.6 What stays open after 021

- **Prefork pool beat tick smoke**: 021 will use `--pool=solo`
  for the worker side. A separate smoke under prefork covers
  the W2 fork-unsafe pool class for beat-driven tasks. Bounded
  follow-up.
- **Beat leader-election smoke**: two beat processes racing for
  the Redis leader lock with deterministic outcome. Optional;
  not blocking work.
- **Catchup detection smoke**: `_emit_catchup_runs()` covers a
  separate code path. Optional follow-up.

---

## 13. Related docs

- [`runtime-prereqs.md`](runtime-prereqs.md) — runtime contract
  (env vars, IAM, dependencies, graceful-degradation policy).
- [`deploy-checklist.md`](deploy-checklist.md) — pre/post deploy
  checklist for an UPDATE (not a fresh install).
- [`backup-restore.md`](backup-restore.md) — DB backup and restore
  procedures.
- [`runbook.md`](runbook.md) — recovery and incident-handling
  procedures.
- [`backend/deploy/systemd/README.md`](../../backend/deploy/systemd/README.md)
  — systemd unit file inventory and modification procedure.
- [`backend/deploy/iam/README.md`](../../backend/deploy/iam/README.md)
  — IAM policy install for the AWS path.
- [`install-docker.md`](install-docker.md) — Docker install path
  (NOT IMPLEMENTED, target shape only).
